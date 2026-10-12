"""OCSP responses for ATSC A/360 certificate status (RFC 6960).

The CertificationData LLS table carries a stapled ``OCSPResponse`` for **each**
certificate in the chain (A/360 5.2.2.2), so a receiver validates revocation
offline and bounded.  This module issues those responses with an OCSP
responder certificate (A/360 5.3.1.7) and a tiny serial -> status store.

The response is assembled with ``asn1crypto`` and signed through a
:class:`~openatsc3_pki.signer.Signer`, so the responder key may be a KMS key
with no local private key object (``cryptography``'s OCSP builder requires
one).  ``CertID`` is computed per RFC 6960: SHA-1 of the issuer ``Name`` and of
the issuer ``SubjectPublicKey`` BIT STRING contents.

Verification-time rules a receiver applies (A/360 5.2.2.6 item 5): the response
must authenticate, ``CertStatus`` must be ``good``, and now must be within
``producedAt`` - 1 h, ``nextUpdate``, ``producedAt`` + 10 days, and
``producedAt`` + ``OCSPRefresh``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from asn1crypto import algos, core
from asn1crypto import keys as _asn1_keys
from asn1crypto import ocsp as _asn1_ocsp
from asn1crypto import x509 as _asn1_x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import signer as _signer
from .x509 import Certificate

#: The RFC 6960 ``id-pkix-ocsp-basic`` response type.
BASIC_RESPONSE_TYPE = "basic_ocsp_response"


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


def _whole_seconds(when: _dt.datetime) -> _dt.datetime:
    """Drop sub-second precision (A/360 5.2.2.1: signing times are whole seconds).

    ``asn1crypto`` would otherwise emit a fractional-second GeneralizedTime,
    which receivers (and ``cryptography``'s OCSP parser) reject.
    """
    return when.replace(microsecond=0)


def _public_key_hash(cert: Certificate) -> bytes:
    """SHA-1 of the SubjectPublicKey BIT STRING (RFC 6960 KeyHash)."""
    spki = cert.cert.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    bit_string = _asn1_keys.PublicKeyInfo.load(spki)["public_key"]
    return hashlib.sha1(bit_string.native).digest()


def _cert_id(cert: Certificate, issuer: Certificate) -> _asn1_ocsp.CertId:
    return _asn1_ocsp.CertId({
        "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha1"}),
        "issuer_name_hash": hashlib.sha1(
            issuer.cert.subject.public_bytes()).digest(),
        "issuer_key_hash": _public_key_hash(issuer),
        "serial_number": cert.cert.serial_number,
    })


def _cert_status(store: StatusStore, serial: int,
                 this_update: _dt.datetime) -> _asn1_ocsp.CertStatus:
    if store.is_revoked(serial):
        return _asn1_ocsp.CertStatus(name="revoked", value=_asn1_ocsp.RevokedInfo({
            "revocation_time": _whole_seconds(
                store.revocation_time(serial) or this_update),
            "revocation_reason": "unspecified"}))
    return _asn1_ocsp.CertStatus(name="good", value=None)


def respond(
    cert: Certificate,
    issuer: Certificate,
    responder_cert: Certificate,
    responder_key,
    store: StatusStore,
    this_update: Optional[_dt.datetime] = None,
    next_update: Optional[_dt.datetime] = None,
) -> bytes:
    """A DER OCSPResponse for ``cert``, signed by the responder.

    ``issuer`` is the certificate that issued ``cert`` (its SubjectName/Key are
    hashed into the ``CertID``).  ``next_update`` defaults to ``this_update`` +
    OCSPRefresh of one day.  ``responder_key`` is a
    :class:`~openatsc3_pki.keys.KeyPair` or any
    :class:`~openatsc3_pki.signer.Signer`.
    """
    this_update = _whole_seconds(this_update or _dt.datetime.now(_dt.timezone.utc))
    next_update = _whole_seconds(
        next_update or (this_update + _dt.timedelta(days=1)))
    signer = _signer.as_signer(responder_key)
    hash_name = _signer.hash_for_curve(signer.curve)

    single = _asn1_ocsp.SingleResponse({
        "cert_id": _cert_id(cert, issuer),
        "cert_status": _cert_status(store, cert.cert.serial_number, this_update),
        "this_update": this_update,
        "next_update": next_update,
    })
    tbs = _asn1_ocsp.ResponseData({
        "responder_id": _asn1_ocsp.ResponderId(
            name="by_key", value=_public_key_hash(responder_cert)),
        "produced_at": this_update,
        "responses": [single],
    })
    signature = signer.sign(tbs.dump(), hash_name)
    basic = _asn1_ocsp.BasicOCSPResponse({
        "tbs_response_data": tbs,
        "signature_algorithm": algos.SignedDigestAlgorithm(
            {"algorithm": f"{hash_name}_ecdsa"}),
        "signature": core.OctetBitString(signature),
        "certs": [_asn1_x509.Certificate.load(responder_cert.der())],
    })
    response_bytes = _asn1_ocsp.ResponseBytes()
    response_bytes["response_type"] = BASIC_RESPONSE_TYPE
    response_bytes["response"] = core.ParsableOctetString(parsed=basic)
    response = _asn1_ocsp.OCSPResponse({
        "response_status": "successful",
        "response_bytes": response_bytes,
    })
    return response.dump()


def respond_chain(
    chain: Iterable[Tuple[Certificate, Certificate]],
    responder_cert: Certificate,
    responder_key,
    store: StatusStore,
    this_update: Optional[_dt.datetime] = None,
    next_update: Optional[_dt.datetime] = None,
) -> List[bytes]:
    """One OCSPResponse per ``(cert, issuer)`` pair, in order."""
    return [respond(c, i, responder_cert, responder_key, store,
                    this_update, next_update)
            for c, i in chain]
