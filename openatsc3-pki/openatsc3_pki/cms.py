"""CMS SignedData (RFC 5652) for ATSC A/360 signaling signatures.

Implements the A/360 5.2.2.1 "basic characteristics" shared by every ATSC
signing construct:

1) a ``SigningTime`` signed attribute is present (whole seconds, no fractional
   part);
2) the ``SubjectKeyIdentifier`` is the ``SignerIdentifier``;
3) **no** encapsulated content, certificates or CRLs are included — the CMS
   blob is detached;
4) the signature/digest algorithm is one of the permitted pairs (P-256/SHA-256,
   P-384/SHA-384, P-521/SHA-512).

The verified bytes are always computed by the caller: for LLS that is the
``SignedMultiTable`` extent from ``LLS_payload_count`` up to but not including
``signature_length`` (A/331 6.7); for the CDT it is the full ``ToBeSignedData``
element including its tags (A/360 5.2.2.2).

Reference: RFC 5652, RFC 5753; ATSC A/360 5.2.2.1.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional, Tuple

from asn1crypto import cms, algos, core
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from . import keys

#: OID -> (curve, hash) for the A/360 5.2.2.1 item 4 permitted pairs.
_SIG_ALG = {
    "secp256r1": algos.SignedDigestAlgorithm({"algorithm": "sha256_ecdsa"}),
    "secp384r1": algos.SignedDigestAlgorithm({"algorithm": "sha384_ecdsa"}),
    "secp521r1": algos.SignedDigestAlgorithm({"algorithm": "sha512_ecdsa"}),
}
_DIGEST_ALG = {
    "secp256r1": algos.DigestAlgorithm({"algorithm": "sha256"}),
    "secp384r1": algos.DigestAlgorithm({"algorithm": "sha384"}),
    "secp521r1": algos.DigestAlgorithm({"algorithm": "sha512"}),
}
_HASH = {"secp256r1": "sha256", "secp384r1": "sha384", "secp521r1": "sha512"}

#: Curve name -> cryptography ECDSA hash object (A/360 5.2.2.1 item 4).
_CRYPTO_HASH = {
    "secp256r1": hashes.SHA256,
    "secp384r1": hashes.SHA384,
    "secp521r1": hashes.SHA512,
}


def _digest(curve: str, data: bytes) -> bytes:
    h = hashes.Hash(_CRYPTO_HASH[curve]())
    h.update(data)
    return h.finalize()


def sign_detached(signed_bytes: bytes, cert, key: keys.KeyPair,
                  signing_time: Optional[_dt.datetime] = None) -> bytes:
    """Return a detached CMS SignedData (ContentInfo DER) over ``signed_bytes``.

    ``cert`` is an :class:`~openatsc3_pki.x509.Certificate`; its SubjectKey
    Identifier is used as the ``SignerIdentifier`` and it is *not* embedded
    (A/360 5.2.2.1 item 3).
    """
    if signing_time is None:
        signing_time = _dt.datetime.now(_dt.timezone.utc)
    signing_time = signing_time.replace(microsecond=0)
    curve = key.curve
    if curve not in _SIG_ALG:
        raise ValueError(f"curve {curve} is not a permitted signing curve")

    attrs = cms.CMSAttributes([
        cms.CMSAttribute({"type": "content_type",
                          "values": ["data"]}),
        cms.CMSAttribute({"type": "signing_time",
                          "values": [cms.Time({"utc_time": signing_time})]}),
        cms.CMSAttribute({"type": "message_digest",
                          "values": [core.OctetString(_digest(curve, signed_bytes))]}),
    ])
    to_sign = attrs.dump()  # SET OF, the octets actually signed (RFC 5652 5.4)
    signature = key.private.sign(to_sign, ec.ECDSA(_CRYPTO_HASH[curve]()))

    sid = cert.subject_key_identifier()
    signer_info = cms.SignerInfo({
        "version": "v3",
        "sid": cms.SignerIdentifier({"subject_key_identifier": core.OctetString(sid)}),
        "digest_algorithm": _DIGEST_ALG[curve],
        "signed_attrs": attrs,
        "signature_algorithm": _SIG_ALG[curve],
        "signature": signature,
    })
    signed_data = cms.SignedData({
        "version": "v3",
        "digest_algorithms": cms.DigestAlgorithms([_DIGEST_ALG[curve]]),
        "encap_content_info": cms.EncapsulatedContentInfo({
            "content_type": "data"}),
        "certificates": None,
        "crls": None,
        "signer_infos": [signer_info],
    })
    return cms.ContentInfo({"content_type": "signed_data",
                            "content": signed_data}).dump()


@dataclass(frozen=True)
class CmsSignature:
    """The parsed pieces of a detached CMS SignedData that verification needs."""
    signer_ski: bytes
    signing_time: _dt.datetime
    message_digest: bytes
    signature: bytes
    digest_algorithm: str
    signed_attrs_der: bytes


def parse(cms_der: bytes) -> CmsSignature:
    """Parse a detached CMS SignedData produced by :func:`sign_detached`."""
    ci = cms.ContentInfo.load(cms_der)
    if ci["content_type"].native != "signed_data":
        raise ValueError("not a CMS SignedData")
    sd = ci["content"]
    si = sd["signer_infos"][0]
    ski = si["sid"].chosen.native
    attrs = si["signed_attrs"]
    signing_time = None
    message_digest = None
    for a in attrs:
        if a["type"].native == "signing_time":
            signing_time = a["values"][0].native
        elif a["type"].native == "message_digest":
            message_digest = a["values"][0].native
    if signing_time is None or message_digest is None:
        raise ValueError("missing SigningTime or messageDigest attribute")
    return CmsSignature(
        signer_ski=ski,
        signing_time=signing_time,
        message_digest=message_digest,
        signature=si["signature"].native,
        digest_algorithm=si["digest_algorithm"]["algorithm"].native,
        signed_attrs_der=attrs.untag().dump(),
    )


def verify(cms_der: bytes, signed_bytes: bytes, public_key) -> bool:
    """Verify a detached CMS signature over ``signed_bytes`` with ``public_key``.

    Checks the message digest, the signature over the signed attributes, and
    that the digest algorithm is one of the A/360 5.2.2.1 pairs.  Chain and
    certificate-profile checks are the caller's responsibility.
    """
    sig = parse(cms_der)
    curve = _curve_of(public_key)
    expected_digest = _digest(curve, signed_bytes)
    if expected_digest != sig.message_digest:
        return False
    if sig.digest_algorithm != _HASH[curve]:
        return False
    try:
        public_key.verify(sig.signature, sig.signed_attrs_der,
                           ec.ECDSA(_CRYPTO_HASH[curve]()))
    except InvalidSignature:
        return False
    return True


def _curve_of(public_key) -> str:
    return public_key.curve.name


def signing_time(cms_der: bytes) -> _dt.datetime:
    """The ``SigningTime`` of a detached CMS SignedData."""
    return parse(cms_der).signing_time
