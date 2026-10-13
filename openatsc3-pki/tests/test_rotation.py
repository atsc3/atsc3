"""Ceremony minutes and root-rotation bundles."""

import hashlib
import json

import pytest
from cryptography.exceptions import InvalidSignature

from openatsc3_pki import ca, cli, keys, rotation, x509
from openatsc3_pki.x509 import SubjectInfo


def _subject():
    return SubjectInfo(common_name="OpenATSC3 Root CA",
                       organizational_unit="ATSC Trust Authority",
                       organization="OpenATSC3")


def _roots(tmp_path):
    old = ca.CertificateAuthority(str(tmp_path / "old"))
    old.init_root()
    new = ca.CertificateAuthority(str(tmp_path / "new"))
    new.init_root(common_name="OpenATSC3 Root CA 2027")
    return old, new


def test_minutes_bind_artifacts_and_detect_tampering():
    record = rotation.build_minutes(
        "sign-request", artifacts={"request": b"placeholder"},
        certs={"result": x509.issue_root(_subject(), keys.generate(keys.ROOT_CURVE))},
        witnesses=["A", "B"], note="tenant acme")
    rotation.verify_minutes(record)
    assert record["artifacts"]["request"] == \
        hashlib.sha256(b"placeholder").hexdigest()

    tampered = dict(record, note="something else")
    with pytest.raises(ValueError):
        rotation.verify_minutes(tampered)


def test_signed_minutes_need_the_issuer():
    key = keys.generate(keys.ROOT_CURVE)
    issuer = x509.issue_root(_subject(), key)
    signed = rotation.sign_minutes(rotation.build_minutes("sign-request"), key)

    rotation.verify_minutes(signed, issuer)
    with pytest.raises(ValueError):
        rotation.verify_minutes(signed)  # signature present, no issuer
    other = x509.issue_root(_subject(), keys.generate(keys.ROOT_CURVE))
    with pytest.raises(InvalidSignature):
        rotation.verify_minutes(signed, other)


def test_rotation_bundle_is_self_described(tmp_path):
    old, new = _roots(tmp_path)
    cross = x509.cross_sign_root(new.load_root(), old.load_root(),
                                 old.load_root_key())
    files = rotation.rotation_bundle(new.load_root(), cross, old.load_root(),
                                     witnesses=["W1"])
    assert set(files) == {"new-root.cert.pem", "new-root-cross.pem",
                          "old-root.cert.pem", rotation.MANIFEST_NAME}
    manifest = json.loads(files[rotation.MANIFEST_NAME])
    rotation.verify_minutes(manifest)
    assert manifest["op"] == "root-rotation"
    assert manifest["artifacts"]["new-root.cert.pem"] == \
        hashlib.sha256(files["new-root.cert.pem"]).hexdigest()
    assert manifest["certificates"]["cross"]["issuer"] == \
        old.load_root().subject.rfc4514_string()


def test_cli_rotation_bundle_and_minutes(tmp_path):
    old, new = _roots(tmp_path)
    cross = tmp_path / "cross.pem"
    assert cli.main(["cross-sign", "--new-root", new.root_cert_path,
                     "--old-root", old.root_cert_path,
                     "--key", old.root_key_path, "--out", str(cross)]) == 0

    bundle = tmp_path / "bundle"
    assert cli.main(["rotation-bundle", "--new-root", new.root_cert_path,
                     "--cross", str(cross), "--old-root", old.root_cert_path,
                     "--witness", "A", "--out-dir", str(bundle)]) == 0
    manifest = bundle / rotation.MANIFEST_NAME
    assert manifest.exists()
    assert cli.main(["verify-minutes", "--minutes", str(manifest)]) == 0

    minutes = tmp_path / "minutes.json"
    assert cli.main(["minutes", "--op", "root-rotation",
                     "--artifact", f"cross={cross}",
                     "--cert", f"new={new.root_cert_path}",
                     "--key", old.root_key_path, "--witness", "A",
                     "--out", str(minutes)]) == 0
    assert cli.main(["verify-minutes", "--minutes", str(minutes),
                     "--issuer", old.root_cert_path]) == 0

    record = json.loads(minutes.read_text())
    record["witnesses"].append("forged")
    minutes_lie = tmp_path / "minutes-lie.json"
    minutes_lie.write_text(json.dumps(record))
    assert cli.main(["verify-minutes", "--minutes", str(minutes_lie),
                     "--issuer", old.root_cert_path]) == 2
