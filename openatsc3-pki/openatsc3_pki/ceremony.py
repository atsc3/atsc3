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
from typing import Iterable

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

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


def _placeholder(curve: str, issuer: x509.Certificate, build) -> bytes:
    """Build a certificate with a throwaway signature and return its DER.

    The AuthorityKeyIdentifier (and issuer name) come from the real ``issuer``
    certificate, and the subject key is the real (public) key; only the signature
    is throwaway, so the returned ``TBSCertificate`` is exactly what must be
    signed by the offline CA key.
    """
    throwaway = keys.generate(curve)
    sign_only = _SignOnly(public=issuer.cert.public_key(), private=throwaway.private)
    return build(sign_only).der()


def ca_request(subject: x509.SubjectInfo, subject_public: PublicKey,
               issuer: x509.Certificate, *, days: int = 3650,
               curve: str = keys.ROOT_CURVE) -> bytes:
    """A placeholder intermediate/tenant-CA certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_ca(
        subject, subject_public, issuer, ik, days=days))


def responder_request(subject: x509.SubjectInfo, subject_public: PublicKey,
                      issuer: x509.Certificate, *, days: int = 365,
                      curve: str = keys.ROOT_CURVE) -> bytes:
    """A placeholder OCSP-responder certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_ocsp_responder(
        subject, subject_public, issuer, ik, days=days))


def signaling_signer_request(subject: x509.SubjectInfo, subject_public: PublicKey,
                             issuer: x509.Certificate, bsids: Iterable[int], *,
                             days: int = 365,
                             curve: str = keys.CA_CURVE) -> bytes:
    """A placeholder signaling-signer (leaf) certificate to be signed by ``issuer``."""
    return _placeholder(curve, issuer, lambda ik: x509.issue_signaling_signer(
        subject, subject_public, issuer, ik, tuple(bsids), days=days))


def sign_request(request_der: bytes, ca_key) -> x509.Certificate:
    """Sign a request's TBS with ``ca_key`` and return the finished certificate.

    ``ca_key`` is the issuing key (local ``KeyPair`` or a ``Signer``); the curve
    must match the ``curve`` the request was built with.
    """
    placeholder = x509.load_der(request_der)
    s = _signer.as_signer(ca_key)
    hash_name = _signer.hash_for_curve(s.curve)
    signature = s.sign(placeholder.cert.tbs_certificate_bytes, hash_name)
    return x509.Certificate(_splice_signature(placeholder.cert, signature))
