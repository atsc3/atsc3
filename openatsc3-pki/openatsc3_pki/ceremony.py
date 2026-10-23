"""Offline-root ceremony: build a request online, sign it air-gapped, assemble.

The root key never touches the online host.  The online side builds a
*placeholder* certificate -- the final ``TBSCertificate`` wrapped in a throwaway
signature, assembled from public material only -- and the air-gapped side signs
that TBS with the CA key.  The result is the finished certificate, indistinguishable
from one issued in-process, so verification is unchanged.

This is the "offline CSR/ceremony split" the plan defers to Phase 3, and it works
through the :class:`~openatsc3_pki.signer.Signer` seam, so the online subject keys
may be AWS KMS keys and the offline CA key may live on an HSM/air-gapped host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from cryptography import x509 as _cx509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key
from cryptography.x509.oid import ExtensionOID

from . import keys, signer as _signer, x509
from .x509 import _splice_signature


@dataclass(frozen=True)
class PublicKey:
    """A public key without private material, usable where ``x509`` wants a key."""

    public: ec.EllipticCurvePublicKey
    curve: str

    @classmethod
    def from_der(cls, spki_der: bytes) -> "PublicKey":
        """Load a SubjectPublicKeyInfo (DER)."""
        public = load_der_public_key(spki_der)
        if not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError("only EC public keys are supported")
        return cls(public, public.curve.name)

    @classmethod
    def from_signer(cls, key) -> "PublicKey":
        """The public half of a :class:`~openatsc3_pki.signer.Signer`/``KeyPair``."""
        s = _signer.as_signer(key)
        return cls(s.public, s.curve)


@dataclass(frozen=True)
class _SignOnly:
    """A real issuer public key with a throwaway private key (placeholder signing)."""

    public: ec.EllipticCurvePublicKey
    private: ec.EllipticCurvePrivateKey


def _issuer_curve(issuer: x509.Certificate) -> str:
    public = issuer.cert.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey):
        raise ValueError("issuer must be an EC certificate")
    return public.curve.name


def _placeholder(curve: Optional[str], issuer: x509.Certificate, build) -> bytes:
    """Build a certificate with a throwaway signature and return its DER.

    The AuthorityKeyIdentifier (and issuer name) come from the real ``issuer``
    certificate, and the subject key is the real (public) key; only the signature
    is throwaway, so the returned ``TBSCertificate`` is exactly what must be
    signed by the offline CA key.  ``curve`` is the issuer's curve; it defaults
    to the issuer certificate's actual curve.
    """
    throwaway = keys.generate(curve or _issuer_curve(issuer))
    sign_only = _SignOnly(public=issuer.cert.public_key(), private=throwaway.private)
    return build(sign_only).der()


def ca_request(subject: x509.SubjectInfo, subject_public: PublicKey,
               issuer: x509.Certificate, *, days: int = 3650,
               curve: Optional[str] = None) -> bytes:
    """A placeholder intermediate/tenant-CA certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_ca(
        subject, subject_public, issuer, ik, days=days))


def responder_request(subject: x509.SubjectInfo, subject_public: PublicKey,
                      issuer: x509.Certificate, *, days: int = 365,
                      curve: Optional[str] = None) -> bytes:
    """A placeholder OCSP-responder certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_ocsp_responder(
        subject, subject_public, issuer, ik, days=days))


def signaling_signer_request(subject: x509.SubjectInfo, subject_public: PublicKey,
                             issuer: x509.Certificate, bsids: Iterable[int], *,
                             days: int = 365,
                             curve: Optional[str] = None) -> bytes:
    """A placeholder signaling-signer (leaf) certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_signaling_signer(
        subject, subject_public, issuer, ik, tuple(bsids), days=days))


def _public_der(public) -> bytes:
    return public.public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _same_public(a, b) -> bool:
    return _public_der(a) == _public_der(b)


def _request_aki(placeholder: _cx509.Certificate) -> Optional[bytes]:
    try:
        ext = placeholder.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER)
    except _cx509.ExtensionNotFound:
        return None
    return ext.value.key_identifier


def sign_request(request_der: bytes, ca_key,
                 issuer: Optional[x509.Certificate] = None) -> x509.Certificate:
    """Sign a request's TBS with ``ca_key`` and return the finished certificate.

    ``ca_key`` is the issuing key (local ``KeyPair`` or a ``Signer``); it must
    match the request's AuthorityKeyIdentifier and sign with the curve's
    A/360 digest.  ``issuer``, when supplied, is the expected issuer certificate
    and binds the request to that CA.
    """
    placeholder = x509.load_der(request_der)
    s = _signer.as_signer(ca_key)
    hash_name = _signer.hash_for_curve(s.curve)
    if placeholder.cert.signature_hash_algorithm.name != hash_name:
        raise ValueError(
            "request was built for a different issuer curve/hash than the "
            "supplied signing key")
    aki = _request_aki(placeholder.cert)
    expected_aki = _cx509.SubjectKeyIdentifier.from_public_key(s.public).digest
    if aki is None:
        raise ValueError("request has no AuthorityKeyIdentifier")
    if aki != expected_aki:
        raise ValueError("request AuthorityKeyIdentifier does not match the "
                         "supplied signing key")
    if issuer is not None:
        if placeholder.cert.issuer != issuer.cert.subject:
            raise ValueError("request issuer does not match the expected issuer")
        if not _same_public(issuer.cert.public_key(), s.public):
            raise ValueError("signing key does not match the expected issuer")
    signature = s.sign(placeholder.cert.tbs_certificate_bytes, hash_name)
    signed = x509.Certificate(_splice_signature(placeholder.cert, signature))
    if issuer is not None:
        try:
            issuer.cert.public_key().verify(
                signed.cert.signature, signed.cert.tbs_certificate_bytes,
                ec.ECDSA(_signer.HASH_ALGORITHMS[hash_name]()))
        except InvalidSignature:
            raise ValueError("signed certificate does not verify under the "
                             "expected issuer") from None
    return signed
