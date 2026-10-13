"""CLI: quorum custody end to end (split the root, sign a request with 3-of-5)."""

import os

from cryptography.hazmat.primitives.asymmetric import ec

from openatsc3_pki import ca, ceremony, cli, keys, x509
from openatsc3_pki.x509 import SubjectInfo


def _subject():
    return SubjectInfo(common_name="Acme Intermediate CA",
                       organizational_unit="ATSC Trust Authority",
                       organization="OpenATSC3")


def _request(authority, tmp_path):
    subject_key = keys.generate(keys.CA_CURVE)
    request = ceremony.ca_request(
        _subject(), ceremony.PublicKey.from_signer(subject_key), authority.load_root())
    path = tmp_path / "acme.req.der"
    path.write_bytes(request)
    return path, subject_key


def test_quorum_split_and_sign_request(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    req_path, subject_key = _request(authority, tmp_path)

    shares_dir = tmp_path / "shares"
    assert cli.main(["quorum-split", "--key", authority.root_key_path,
                     "--threshold", "3", "--shares", "5",
                     "--out-dir", str(shares_dir)]) == 0
    paths = [str(shares_dir / f"share-{i}") for i in (1, 3, 5)]
    assert all(os.path.exists(p) for p in paths)

    out = tmp_path / "acme.pem"
    assert cli.main(["sign-request", "--request", str(req_path),
                     "--shares", *paths, "--out", str(out)]) == 0

    signed = x509.load_pem(out.read_bytes())
    assert signed.subject == _subject().name()
    assert signed.cert.public_key().public_numbers() == \
        subject_key.public.public_numbers()
    authority.load_root().cert.public_key().verify(
        signed.cert.signature, signed.cert.tbs_certificate_bytes,
        ec.ECDSA(signed.cert.signature_hash_algorithm))


def test_below_threshold_shares_fail(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    req_path, _ = _request(authority, tmp_path)

    shares_dir = tmp_path / "shares"
    cli.main(["quorum-split", "--key", authority.root_key_path,
              "--threshold", "3", "--shares", "5", "--out-dir", str(shares_dir)])
    paths = [str(shares_dir / f"share-{i}") for i in (1, 2)]  # only 2 of 5

    out = tmp_path / "acme.pem"
    assert cli.main(["sign-request", "--request", str(req_path),
                     "--shares", *paths, "--out", str(out)]) == 2
    assert not out.exists()
