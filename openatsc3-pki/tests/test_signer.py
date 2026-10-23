"""Signing-backend seam: local and KMS signers are interchangeable.

The KMS backend is exercised against a fake client that mimics the two boto3
calls used by :class:`openatsc3_pki.signer.KmsSigner`; that keeps the test free
of AWS while proving the seam carries X.509 issuance, CMS and OCSP end to end.
"""

import datetime as dt

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509 import ocsp as crypto_ocsp

from openatsc3_pki import cdt, keys, ocsp, signer, verify
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_ca,
    issue_ocsp_responder,
    issue_root,
    issue_signaling_signer,
)

_BSID = 540

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
    """A minimal boto3 KMS stand-in backed by a local key."""

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


def kms(curve: str) -> signer.KmsSigner:
    return signer.KmsSigner("test-key", FakeKmsClient(keys.generate(curve).private))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def test_as_signer_wraps_keypair():
    pair = keys.generate()
    wrapped = signer.as_signer(pair)
    assert isinstance(wrapped, signer.LocalSigner)
    assert wrapped.curve == pair.curve
    message = b"data"
    signature = wrapped.sign(message, "sha256")
    pair.public.verify(signature, message, ec.ECDSA(hashes.SHA256()))


def test_as_signer_passes_a_signer_through():
    s = kms("secp256r1")
    assert signer.as_signer(s) is s


def test_kms_signer_derives_curve_from_key_spec():
    s = kms("secp384r1")
    assert s.curve == "secp384r1"
    assert isinstance(s.public, ec.EllipticCurvePublicKey)


def test_kms_signer_signs_with_digest():
    s = kms("secp256r1")
    message = b"a signaling message"
    signature = s.sign(message, "sha256")
    s.public.verify(signature, message, ec.ECDSA(hashes.SHA256()))


def test_kms_signer_rejects_mismatched_hash():
    with pytest.raises(ValueError):
        kms("secp384r1").sign(b"x", "sha256")


def test_kms_for_key_accepts_injected_client():
    client = FakeKmsClient(keys.generate("secp256r1").private)
    s = signer.KmsSigner.for_key("arn:aws:kms:...:key/1234", client=client)
    assert s.key_id == "arn:aws:kms:...:key/1234"
    assert s.curve == "secp256r1"


class FakeEnvelopeClient:
    def __init__(self, private):
        self._private = private

    def decrypt(self, KeyId, CiphertextBlob, EncryptionContext=None):
        return {"Plaintext": self._private.private.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())}


def test_envelope_signer_signs_with_the_wrapped_key():
    private = keys.generate("secp256r1")
    client = FakeEnvelopeClient(private)
    public_der = private.public.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    s = signer.EnvelopeSigner(
        b"ciphertext", "arn:aws:kms:...:key/wrap", client, public_der)
    assert s.curve == "secp256r1"
    message = b"a signaling message"
    signature = s.sign(message, "sha256")
    private.public.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    assert "ciphertext" not in repr(s)


def test_kms_signed_ocsp_and_revocation():
    now = _now()
    root_signer = kms(keys.ROOT_CURVE)
    root = issue_root(_subject("Root", "OpenATSC3"), root_signer)
    ca_signer = kms(keys.CA_CURVE)
    ca = issue_ca(_subject("Issuing", "OpenATSC3"), ca_signer,
                  root, root_signer)
    leaf = issue_signaling_signer(
        _subject("WHUT-LLS-Signer", "WHUT"), kms(keys.SIGNER_CURVE),
        ca, ca_signer, (_BSID,))
    responder_signer = kms(keys.SIGNER_CURVE)
    responder = issue_ocsp_responder(
        _subject("OCSP", "OpenATSC3"), responder_signer, ca, ca_signer)

    store = ocsp.StatusStore()
    good = ocsp.respond(leaf, ca, responder, responder_signer, store,
                        this_update=now, next_update=now + dt.timedelta(days=1))
    loaded = crypto_ocsp.load_der_ocsp_response(good)
    assert loaded.certificate_status is crypto_ocsp.OCSPCertStatus.GOOD
    assert loaded.serial_number == leaf.cert.serial_number
    responder.cert.public_key().verify(
        loaded.signature, loaded.tbs_response_bytes,
        ec.ECDSA(loaded.signature_hash_algorithm))

    store.mark_revoked(leaf.cert.serial_number, now)
    revoked = ocsp.respond(leaf, ca, responder, responder_signer, store,
                           this_update=now, next_update=now + dt.timedelta(days=1))
    loaded = crypto_ocsp.load_der_ocsp_response(revoked)
    assert loaded.certificate_status is crypto_ocsp.OCSPCertStatus.REVOKED


def test_external_signer_end_to_end():
    now = _now()
    root_signer = kms(keys.ROOT_CURVE)
    root = issue_root(_subject("KMS Root", "OpenATSC3"), root_signer)
    ca_signer = kms(keys.CA_CURVE)
    ca = issue_ca(_subject("KMS Issuing CA", "OpenATSC3"), ca_signer,
                  root, root_signer)
    leaf_signer = kms(keys.SIGNER_CURVE)
    leaf = issue_signaling_signer(
        _subject("WHUT-LLS-Signer", "WHUT"), leaf_signer, ca, ca_signer,
        (_BSID,))
    cdt_signer = kms(keys.SIGNER_CURVE)
    cdt_cert = issue_signaling_signer(
        _subject("WHUT-CDT-Signer", "WHUT"), cdt_signer, ca, ca_signer,
        (_BSID,))
    responder_signer = kms(keys.SIGNER_CURVE)
    responder = issue_ocsp_responder(
        _subject("KMS OCSP", "OpenATSC3"), responder_signer, ca, ca_signer)
    root_responder_signer = kms(keys.SIGNER_CURVE)
    root_responder = issue_ocsp_responder(
        _subject("KMS Root OCSP", "OpenATSC3"), root_responder_signer,
        root, root_signer)

    assert leaf.cert.public_key().public_numbers() == \
        leaf_signer.public.public_numbers()

    store = ocsp.StatusStore()

    def response(cert, issuer, responder, responder_signer):
        return ocsp.respond(cert, issuer, responder, responder_signer, store,
                            this_update=now,
                            next_update=now + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[ca.der()], signing_cert=leaf, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_signer,
        ocsp_responses=[response(ca, root, root_responder, root_responder_signer),
                        response(leaf, ca, responder, responder_signer),
                        response(cdt_cert, ca, responder, responder_signer)],
        signing_time=now)
    parsed = cdt.parse_certification_data(table.gzip_bytes())
    verdict, certified = verify.verify_certification_data(parsed, [root], now=now)
    assert verdict.ok, verdict.reason

    body = cdt.signed_multitable(
        [cdt.LlsTable(0x01, 13, b'<SLT bsid="540"/>')], leaf, leaf_signer,
        signing_time=now)
    message = cdt.parse_signed_multitable(body)
    verdict = verify.verify_signed_message(message.cms, message.signed_extent,
                                           certified, [_BSID], now=now)
    assert verdict.ok, verdict.reason


def _subject(common_name: str, organization: str) -> SubjectInfo:
    return SubjectInfo(common_name=common_name,
                       organizational_unit="ATSC", organization=organization)
