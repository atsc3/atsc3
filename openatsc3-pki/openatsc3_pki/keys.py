"""ECDSA key generation and PEM I/O for the OpenATSC3 CA.

A/360 5.3.1: all ATSC 3.0 certificates use RSA (>= 2048) or ECDSA keys.  This
toolkit uses ECDSA throughout: roots and issuing CAs on P-384, end-entity
signers on P-256 (A/360 5.3.1.2-5.3.1.6 minimums).  Private keys are written
with owner-only permissions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

#: Curve names used per profile (A/360 5.3.1.2-5.3.1.6).
ROOT_CURVE = "secp384r1"
CA_CURVE = "secp384r1"
SIGNER_CURVE = "secp256r1"

_CURVES = {
    "secp256r1": ec.SECP256R1,
    "secp384r1": ec.SECP384R1,
    "secp521r1": ec.SECP521R1,
}


@dataclass(frozen=True)
class KeyPair:
    """An ECDSA private key and its public key."""
    private: ec.EllipticCurvePrivateKey

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        return self.private.public_key()

    @property
    def curve(self) -> str:
        return self.private.curve.name


def generate(curve: str = SIGNER_CURVE) -> KeyPair:
    """Generate a fresh ECDSA key pair on ``curve`` (A/360 5.3.1)."""
    if curve not in _CURVES:
        raise ValueError(f"unsupported curve {curve!r}")
    return KeyPair(ec.generate_private_key(_CURVES[curve]()))


def private_bytes(key: KeyPair, password: Optional[bytes] = None) -> bytes:
    """Serialize a private key to PKCS#8 PEM, encrypted when ``password``."""
    enc = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return key.private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )


def load_private(path: str, password: Optional[bytes] = None) -> KeyPair:
    """Load an ECDSA private key from a PKCS#8 PEM file."""
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=password)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError(f"{path} is not an ECDSA private key")
    return KeyPair(key)


def public_bytes(key: KeyPair) -> bytes:
    """Serialize the public key to SubjectPublicKeyInfo PEM."""
    return key.public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def write_private(path: str, key: KeyPair, password: Optional[bytes] = None) -> None:
    """Write a private key with owner-only permissions (0600)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = private_bytes(key, password)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
