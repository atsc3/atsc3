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


def test_quorum_reshare_and_verify(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    shares_dir = tmp_path / "shares"
    cli.main(["quorum-split", "--key", authority.root_key_path,
              "--threshold", "3", "--shares", "5", "--out-dir", str(shares_dir)])
    old = [str(shares_dir / f"share-{i}") for i in (1, 2, 3)]

    new_dir = tmp_path / "new"
    assert cli.main(["quorum-reshare", "--shares", *old,
                     "--threshold", "3", "--shares-out", "5",
                     "--out-dir", str(new_dir)]) == 0
    fresh = [str(new_dir / f"share-{i}") for i in (1, 3, 5)]
    assert cli.main(["quorum-verify", "--shares", *fresh]) == 0

    # the fresh shares sign a request that verifies under the same root
    req_path, _ = _request(authority, tmp_path)
    out = tmp_path / "r.pem"
    assert cli.main(["sign-request", "--request", str(req_path),
                     "--shares", *fresh, "--out", str(out)]) == 0
    signed = x509.load_pem(out.read_bytes())
    authority.load_root().cert.public_key().verify(
        signed.cert.signature, signed.cert.tbs_certificate_bytes,
        ec.ECDSA(signed.cert.signature_hash_algorithm))


def test_quorum_verify_rejects_wrong_expected_key(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    shares_dir = tmp_path / "shares"
    assert cli.main(["quorum-split", "--key", authority.root_key_path,
                     "--threshold", "3", "--shares", "5",
                     "--out-dir", str(shares_dir)]) == 0
    shares = [str(shares_dir / f"share-{i}") for i in (1, 2, 3)]
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root(common_name="Other Root")
    assert cli.main(["quorum-verify", "--shares", *shares,
                     "--expect-cert", other.root_cert_path]) == 2


def test_quorum_split_rejects_non_key_input(tmp_path):
    bad = tmp_path / "not-a-key.pem"
    bad.write_bytes(b"not a private key")
    out = tmp_path / "shares"
    assert cli.main(["quorum-split", "--key", str(bad), "--threshold", "3",
                     "--shares", "5", "--out-dir", str(out)]) == 2
    assert not out.exists()


def test_quorum_combine_rejects_bad_shares(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    shares_dir = tmp_path / "shares"
    cli.main(["quorum-split", "--key", authority.root_key_path,
              "--threshold", "3", "--shares", "5", "--out-dir", str(shares_dir)])
    shares = [str(shares_dir / f"share-{i}") for i in (1, 2, 3)]
    out = tmp_path / "combined.pem"
    assert cli.main(["quorum-combine", "--shares", *shares,
                     "--out", str(out)]) == 0
    assert out.exists()
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root(common_name="Other Root")
    assert cli.main(["quorum-combine", "--shares", *shares,
                     "--out", str(tmp_path / "wrong.pem"),
                     "--expect-cert", other.root_cert_path]) == 2
    assert not (tmp_path / "wrong.pem").exists()


def test_quorum_reshare_below_threshold_writes_nothing(tmp_path):
    authority = ca.CertificateAuthority(str(tmp_path / "ca"))
    authority.init_root()
    shares_dir = tmp_path / "shares"
    cli.main(["quorum-split", "--key", authority.root_key_path,
              "--threshold", "3", "--shares", "5", "--out-dir", str(shares_dir)])
    two = [str(shares_dir / f"share-{i}") for i in (1, 2)]

    new_dir = tmp_path / "new"
    assert cli.main(["quorum-reshare", "--shares", *two,
                     "--threshold", "3", "--shares-out", "5",
                     "--out-dir", str(new_dir)]) == 2
    assert not new_dir.exists()


def test_cli_cross_sign_rotates_to_a_new_root(tmp_path):
    old = ca.CertificateAuthority(str(tmp_path / "old"))
    old.init_root()
    new = ca.CertificateAuthority(str(tmp_path / "new"))
    new.init_root(common_name="OpenATSC3 Root CA 2027")

    out = tmp_path / "cross.pem"
    assert cli.main(["cross-sign", "--new-root", new.root_cert_path,
                     "--old-root", old.root_cert_path,
                     "--key", old.root_key_path, "--out", str(out)]) == 0

    cross = x509.load_pem(out.read_bytes())
    new_root = new.load_root()
    old_root = old.load_root()
    assert cross.subject == new_root.subject
    assert cross.issuer == old_root.subject
    assert cross.cert.public_key().public_numbers() == \
        new_root.cert.public_key().public_numbers()
    old_root.cert.public_key().verify(
        cross.cert.signature, cross.cert.tbs_certificate_bytes,
        ec.ECDSA(cross.cert.signature_hash_algorithm))


def test_cross_signed_root_bridges_old_and_new(tmp_path):
    old = ca.CertificateAuthority(str(tmp_path / "old"))
    old.init_root()
    new = ca.CertificateAuthority(str(tmp_path / "new"))
    new.init_root()
    cross = x509.cross_sign_root(new.load_root(), old.load_root(),
                                 old.load_root_key())

    leaf = x509.issue_ca(_subject(), keys.generate(keys.CA_CURVE),
                         new.load_root(), new.load_root_key())
    # a verifier holding only the old anchor bridges old -> cross -> new-root leaf
    old.load_root().cert.public_key().verify(
        cross.cert.signature, cross.cert.tbs_certificate_bytes,
        ec.ECDSA(cross.cert.signature_hash_algorithm))
    cross.cert.public_key().verify(
        leaf.cert.signature, leaf.cert.tbs_certificate_bytes,
        ec.ECDSA(leaf.cert.signature_hash_algorithm))
