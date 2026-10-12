"""X.509 certificate revocation lists for ATSC certificates (RFC 5280).

A/360 makes revocation OCSP-based (a stapled response per certificate), but a CRL
is a useful, offline-distributable companion artifact.  Signing goes through the
:class:`~openatsc3_pki.signer.Signer` seam exactly like ``x509`` and ``ocsp``, so
the issuing CA key may be an offline/KMS key with no local private key object.

The permitted ECDSA curve/digest pairs are fixed by A/360 5.2.2.1 item 4, reusing
:mod:`openatsc3_pki.signer`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Iterable, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding

from . import signer as _signer
from .x509 import Certificate

#: Default CRL lifetime (RFC 5280 does not fix one; a week is conventional).
DEFAULT_CRL_DAYS = 7

#: Revocation reason strings (our ledger values) -> RFC 5280 ``ReasonFlags``.
_REASONS = {
    "unspecified": x509.ReasonFlags.unspecified,
    "key-compromise": x509.ReasonFlags.key_compromise,
    "keycompromise": x509.ReasonFlags.key_compromise,
    "ca-compromise": x509.ReasonFlags.ca_compromise,
    "cacompromise": x509.ReasonFlags.ca_compromise,
    "affiliation-changed": x509.ReasonFlags.affiliation_changed,
    "superseded": x509.ReasonFlags.superseded,
    "cessation-of-operation": x509.ReasonFlags.cessation_of_operation,
    "certificate-hold": x509.ReasonFlags.certificate_hold,
    "privilege-withdrawn": x509.ReasonFlags.privilege_withdrawn,
    "aa-compromise": x509.ReasonFlags.aa_compromise,
}


def _reason(reason: str) -> x509.ReasonFlags:
    key = (reason or "unspecified").strip().lower().replace("_", "-").replace(" ", "-")
    return _REASONS.get(key, x509.ReasonFlags.unspecified)


def build(issuer: Certificate, issuer_key,
          revoked: Iterable[Tuple[int, _dt.datetime, str]],
          *, this_update: Optional[_dt.datetime] = None,
          next_update: Optional[_dt.datetime] = None,
          days: int = DEFAULT_CRL_DAYS) -> bytes:
    """Return the DER of a CRL issued by ``issuer`` over its revoked children.

    ``revoked`` is an iterable of ``(serial, revocation_date, reason)``.  The CRL
    is signed by ``issuer_key`` (a local ``KeyPair`` or a
    :class:`~openatsc3_pki.signer.Signer`).
    """
    this_update = _whole_seconds(
        this_update or _dt.datetime.now(_dt.timezone.utc))
    next_update = _whole_seconds(next_update or (this_update + _dt.timedelta(days=days)))
    signer = _signer.as_signer(issuer_key)
    hash_name = _signer.hash_for_curve(signer.curve)
    hash_algorithm = _signer.HASH_ALGORITHMS[hash_name]()

    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.subject)
        .last_update(this_update)
        .next_update(next_update)
    )
    for serial, when, reason in revoked:
        entry = (
            x509.RevokedCertificateBuilder()
            .serial_number(int(serial))
            .revocation_date(_whole_seconds(when))
            .add_extension(x509.CRLReason(_reason(reason)), critical=False)
            .build()
        )
        builder = builder.add_revoked_certificate(entry)

    if isinstance(signer, _signer.LocalSigner):
        return builder.sign(signer.private, hash_algorithm).public_bytes(Encoding.DER)
    placeholder = builder.sign(
        ec.generate_private_key(_signer.EC_CURVES[signer.curve]()), hash_algorithm)
    signature = signer.sign(placeholder.tbs_certlist_bytes, hash_name)
    return _splice_signature(placeholder, signature)


def pem(der: bytes) -> bytes:
    """The PEM encoding of a DER CRL."""
    return x509.load_der_x509_crl(der).public_bytes(Encoding.PEM)


def _whole_seconds(when: _dt.datetime) -> _dt.datetime:
    return when.replace(microsecond=0)


def _splice_signature(placeholder: x509.CertificateRevocationList,
                      signature: bytes) -> bytes:
    """Replace a placeholder CRL's ``signature`` with an external signer's."""
    from asn1crypto import core as _core
    from asn1crypto import crl as _crl

    parsed = _crl.CertificateList.load(placeholder.public_bytes(Encoding.DER))
    parsed["signature"] = _core.OctetBitString(signature)
    return parsed.dump()
