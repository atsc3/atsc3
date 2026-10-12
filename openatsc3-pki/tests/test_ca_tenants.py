"""Per-tenant intermediate CAs, with local and external (KMS) signers.

The tenant CA is the certificate whose revocation disables one customer, so
each customer must hang off its own intermediate, not a shared one.  These
tests cover the on-disk :class:`~openatsc3_pki.ca.CertificateAuthority` layout
and that a tenant CA can be signed with an external signer (no key file).
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from openatsc3_pki import ca, keys
from openatsc3_pki import signer as _signer

_SPEC = {
    "secp256r1": "ECC_NIST_P256",
    "secp384r1": "ECC_NIST_P384",
    "secp521r1": "ECC_NIST_P521",
}
_ALGORITHM = {
    "ECDSA_SHA_256": hashes.SHA256,
    "ECDSA_SHA_384": hashes.SHA384,
    "ECDSA_SHA_512": hashes.SHA512,
}


class FakeKmsClient:
    """A minimal boto3 KMS stand-in backed by a local key (test only)."""

    def __init__(self, private: ec.EllipticCurvePrivateKey) -> None:
        self._private = private

    def get_public_key(self, KeyId):
        return {
            "KeySpec": _SPEC[self._private.curve.name],
            "PublicKey": self._private.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo),
        }

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        hash_algorithm = _ALGORITHM[SigningAlgorithm]()
        if MessageType == "DIGEST":
            signature = self._private.sign(
                Message, ec.ECDSA(utils.Prehashed(hash_algorithm)))
        else:
            signature = self._private.sign(Message, ec.ECDSA(hash_algorithm))
        return {"Signature": signature}


def kms(curve: str) -> _signer.KmsSigner:
    return _signer.KmsSigner("test-key", FakeKmsClient(keys.generate(curve).private))


def test_authority_issues_tenant_ca_with_local_key(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path))
    root = authority.init_root()
    tenant = authority.issue_tenant_ca("acme")

    assert tenant.issuer == root.subject
    assert tenant.cert.public_key().curve.name == keys.CA_CURVE
    assert os.path.exists(authority.tenant_key_path("acme"))
    assert os.path.exists(authority.tenant_cert_path("acme"))


def test_authority_tenant_ca_with_external_signer_writes_no_key(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path))
    authority.init_root()
    tenant_signer = kms(keys.CA_CURVE)
    tenant = authority.issue_tenant_ca("acme", key=tenant_signer)

    assert tenant.cert.public_key().public_numbers() == \
        tenant_signer.public.public_numbers()
    assert not os.path.exists(authority.tenant_key_path("acme"))


def test_broadcaster_hangs_off_its_own_tenant_intermediate(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path))
    authority.init_root()
    authority.issue_tenant_ca("acme")
    authority.issue_tenant_ca("beta")

    leaf = authority.issue_broadcaster("ACME-TV", (540,), tenant="acme")

    assert leaf.issuer == authority.load_tenant("acme").subject
    assert leaf.issuer != authority.load_tenant("beta").subject
    assert authority.load_broadcaster("ACME-TV", tenant="acme").issuer == leaf.issuer


def test_legacy_default_issuing_still_available(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path))
    authority.init_root()
    authority.issue_issuing()
    leaf = authority.issue_broadcaster("WHUT", (540,))

    assert leaf.issuer == authority.load_issuing().subject
    assert os.path.exists(authority.issuing_key_path)
