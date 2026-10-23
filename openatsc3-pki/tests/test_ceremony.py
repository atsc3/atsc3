"""Offline-root ceremony: build a request online, sign it with the CA key.

No root key is needed to build a request (public material only); the finished
certificate is identical to one issued in-process.
"""

from cryptography import x509 as cx
from cryptography.hazmat.primitives.asymmetric import ec

from openatsc3_pki import ceremony, keys, x509
from openatsc3_pki.x509 import SubjectInfo


def _subject(cn, ou, org):
    return SubjectInfo(common_name=cn, organizational_unit=ou, organization=org)


def test_tenant_ca_request_signed_by_root(authority):
    root = authority.load_root()
    subject_key = keys.generate(keys.CA_CURVE)
    public = ceremony.PublicKey.from_signer(subject_key)
    subject = _subject("Acme Intermediate CA", "ATSC Trust Authority", "OpenATSC3")

    request = ceremony.ca_request(subject, public, root, days=3650)
    # The request carries the subject's public key, not the throwaway's.
    placeholder = cx.load_der_x509_certificate(request)
    assert placeholder.public_key().public_numbers() == \
        subject_key.public.public_numbers()

    signed = ceremony.sign_request(request, authority.load_root_key())
    assert signed.subject == subject.name()
    assert signed.issuer == root.subject
    assert signed.cert.public_key().public_numbers() == \
        subject_key.public.public_numbers()
    assert signed.cert.extensions.get_extension_for_class(
        cx.BasicConstraints).value.ca is True
    root.cert.public_key().verify(signed.cert.signature, signed.cert.tbs_certificate_bytes,
                       ec.ECDSA(signed.cert.signature_hash_algorithm))


def test_signaling_signer_request(authority):
    issuing = authority.load_issuing()
    subject_key = keys.generate(keys.SIGNER_CURVE)
    subject = _subject("WHUT-New", "ATSC Broadcast Signaling Signer", "WHUT")

    request = ceremony.signaling_signer_request(
        subject, ceremony.PublicKey.from_signer(subject_key), issuing, (540,))
    signed = ceremony.sign_request(request, authority.load_issuing_key())

    assert signed.bsids() == (540,)
    assert signed.signaling_eku()
    issuing.cert.public_key().verify(signed.cert.signature, signed.cert.tbs_certificate_bytes,
                          ec.ECDSA(signed.cert.signature_hash_algorithm))


def test_request_uses_the_issuer_curve(tmp_path):
    key = keys.generate("secp521r1")
    root = x509.issue_root(
        _subject("P-521 Root", "ATSC Trust Authority", "OpenATSC3"), key)
    subject_key = keys.generate(keys.CA_CURVE)
    request = ceremony.ca_request(
        _subject("Acme Intermediate CA", "ATSC Trust Authority", "OpenATSC3"),
        ceremony.PublicKey.from_signer(subject_key), root)
    signed = ceremony.sign_request(request, key, issuer=root)
    root.cert.public_key().verify(
        signed.cert.signature, signed.cert.tbs_certificate_bytes,
        ec.ECDSA(signed.cert.signature_hash_algorithm))
