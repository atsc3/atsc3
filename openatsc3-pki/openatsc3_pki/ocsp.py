"""OCSP responses for ATSC A/360 certificate status (RFC 6960).

The CertificationData LLS table carries a stapled ``OCSPResponse`` for **each**
certificate in the chain (A/360 5.2.2.2), so a receiver validates revocation
offline and bounded.  This module issues those responses with an OCSP
responder certificate (A/360 5.3.1.7) and a tiny serial -> status store.

Verification-time rules a receiver applies (A/360 5.2.2.6 item 5): the response
must authenticate, ``CertStatus`` must be ``good``, and now must be within
``producedAt`` - 1 h, ``nextUpdate``, ``producedAt`` + 10 days, and
``producedAt`` + ``OCSPRefresh``.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from cryptography import x509 as _x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509 import ocsp as _ocsp
from cryptography.x509.oid import NameOID

from . import keys
from .x509 import Certificate


@dataclass
class StatusStore:
    """Serial-number -> revocation status for issued certificates."""
    revoked: Dict[int, _dt.datetime] = field(default_factory=dict)

    def mark_revoked(self, serial: int,
                     when: Optional[_dt.datetime] = None) -> None:
        self.revoked[int(serial)] = when or _dt.datetime.now(_dt.timezone.utc)

    def is_revoked(self, serial: int) -> bool:
        return int(serial) in self.revoked

    def revocation_time(self, serial: int) -> Optional[_dt.datetime]:
        return self.revoked.get(int(serial))


def respond(
    cert: Certificate,
    issuer: Certificate,
    responder_cert: Certificate,
    responder_key: keys.KeyPair,
    store: StatusStore,
    this_update: Optional[_dt.datetime] = None,
    next_update: Optional[_dt.datetime] = None,
) -> bytes:
    """A DER OCSPResponse for ``cert``, signed by the responder.

    ``issuer`` is the certificate that issued ``cert`` (its SubjectName/Key are
    hashed into the ``CertID``).  ``next_update`` defaults to ``this_update`` +
    OCSPRefresh of one day.
    """
    this_update = this_update or _dt.datetime.now(_dt.timezone.utc)
    next_update = next_update or (this_update + _dt.timedelta(days=1))
    serial = cert.cert.serial_number
    if store.is_revoked(serial):
        builder = _ocsp.OCSPResponseBuilder().add_response(
            cert=cert.cert, issuer=issuer.cert, algorithm=hashes.SHA1(),
            cert_status=_ocsp.OCSPCertStatus.REVOKED,
            this_update=this_update, next_update=next_update,
            revocation_time=store.revocation_time(serial),
            revocation_reason=_x509.ReasonFlags.unspecified)
    else:
        builder = _ocsp.OCSPResponseBuilder().add_response(
            cert=cert.cert, issuer=issuer.cert, algorithm=hashes.SHA1(),
            cert_status=_ocsp.OCSPCertStatus.GOOD,
            this_update=this_update, next_update=next_update,
            revocation_time=None, revocation_reason=None)
    builder = builder.responder_id(
        _ocsp.OCSPResponderEncoding.HASH, responder_cert.cert)
    builder = builder.certificates([responder_cert.cert])
    response = builder.sign(responder_key.private, hashes.SHA256())
    from cryptography.hazmat.primitives.serialization import Encoding
    return response.public_bytes(Encoding.DER)


def respond_chain(
    chain: Iterable[Tuple[Certificate, Certificate]],
    responder_cert: Certificate,
    responder_key: keys.KeyPair,
    store: StatusStore,
    this_update: Optional[_dt.datetime] = None,
    next_update: Optional[_dt.datetime] = None,
) -> List[bytes]:
    """One OCSPResponse per ``(cert, issuer)`` pair, in order."""
    return [respond(c, i, responder_cert, responder_key, store,
                    this_update, next_update)
            for c, i in chain]
