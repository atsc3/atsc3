"""CRL build/sign tests (RFC 5280), local and external-signer paths.

A/360 revocation is OCSP-based; this pins the optional CRL artifact and, like
``x509``/``ocsp``, proves the signing seam carries it.
"""

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from openatsc3_pki import crl, keys, signer


class _FakeKms:
    def __init__(self, private):
        self._private = private

    def get_public_key(self, KeyId):
        return {
            "KeySpec": {"secp256r1": "ECC_NIST_P256"}.get(
                self._private.curve.name, "ECC_NIST_P384"),
            "PublicKey": self._private.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo),
        }

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        algorithm = {"ECDSA_SHA_256": hashes.SHA256,
                     "ECDSA_SHA_384": hashes.SHA384}[SigningAlgorithm]()
        if MessageType == "DIGEST":
            signature = self._private.sign(Message, ec.ECDSA(utils.Prehashed(algorithm)))
        else:
            signature = self._private.sign(Message, ec.ECDSA(algorithm))
        return {"Signature": signature}


def test_crl_lists_revoked_leaf_and_verifies(authority, now):
    issuing = authority.load_issuing()
    leaf = authority.load_broadcaster("WHUT")

    der = crl.build(issuing, authority.load_issuing_key(),
                    [(leaf.cert.serial_number, now, "key-compromise")],
                    this_update=now, next_update=now + dt.timedelta(days=7))

    parsed = x509.load_der_x509_crl(der)
    assert parsed.issuer == issuing.subject
    revoked = parsed.get_revoked_certificate_by_serial_number(
        leaf.cert.serial_number)
    assert revoked is not None
    reason = revoked.extensions.get_extension_for_class(x509.CRLReason).value
    assert reason.reason == x509.ReasonFlags.key_compromise
    issuing.cert.public_key().verify(
        parsed.signature, parsed.tbs_certlist_bytes,
        ec.ECDSA(parsed.signature_hash_algorithm))


def test_crl_pem_roundtrip(authority, now):
    der = crl.build(authority.load_issuing(), authority.load_issuing_key(), [],
                    this_update=now)
    assert crl.pem(der).startswith(b"-----BEGIN X509 CRL-----")


def test_crl_signed_by_external_signer(authority, now):
    """The CRL path works through the Signer seam (the KMS splice branch)."""
    issuing = authority.load_issuing()
    leaf = authority.load_broadcaster("WHUT")
    backing = keys.generate("secp384r1")
    backend = signer.KmsSigner("test-key", _FakeKms(backing.private),
                               curve="secp384r1")

    der = crl.build(issuing, backend, [(leaf.cert.serial_number, now, "unspecified")],
                    this_update=now)
    parsed = x509.load_der_x509_crl(der)
    # Verify against the backing public key (the issuer's key is unrelated).
    backing.public.verify(parsed.signature, parsed.tbs_certlist_bytes,
                          ec.ECDSA(parsed.signature_hash_algorithm))
    assert parsed.get_revoked_certificate_by_serial_number(
        leaf.cert.serial_number) is not None
