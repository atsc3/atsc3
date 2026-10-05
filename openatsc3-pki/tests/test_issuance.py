"""Certificate issuance and A/360 profile tests."""

import datetime as dt

import pytest

from openatsc3_pki import ca, keys
from openatsc3_pki.oids import (
    ID_ATSC_KP_SIGNALING_SIGNING,
    ID_ATSC_SDATTR_BSID,
)
from openatsc3_pki.sda import decode_bsid_sda
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_root,
    issue_signaling_signer,
)


def test_root_profile(tmp_path):
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    root = a.init_root(common_name="Test Root")
    bc = root.cert.extensions.get_extension_for_class(
        __import__("cryptography.x509", fromlist=["BasicConstraints"])
        .BasicConstraints).value
    assert bc.ca is True
    assert root.cert.issuer == root.cert.subject
    assert root.cert.public_key().curve.name == keys.ROOT_CURVE


def test_issuing_ca_profile(tmp_path):
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    root = a.init_root()
    issuing = a.issue_issuing()
    from cryptography import x509
    bc = issuing.cert.extensions.get_extension_for_class(
        x509.BasicConstraints).value
    assert bc.ca is True
    assert issuing.cert.issuer == root.cert.subject


def test_signaling_signer_profile(tmp_path):
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root()
    a.issue_issuing()
    cert = a.issue_broadcaster("WHUT", (540, 541))
    from cryptography import x509
    ku = cert.cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.digital_signature
    assert not ku.content_commitment
    assert not ku.key_encipherment
    assert not ku.key_cert_sign
    eku = cert.cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert eku.critical is True
    assert x509.ObjectIdentifier(ID_ATSC_KP_SIGNALING_SIGNING) in eku.value
    assert cert.bsids() == (540, 541)
    assert cert.signaling_eku()


def test_sda_roundtrip():
    from openatsc3_pki.sda import encode_bsid_sda
    for bsids in [(33,), (540,), (540, 541), (1, 2, 3, 65000)]:
        der = encode_bsid_sda(bsids)
        assert decode_bsid_sda(der) == tuple(sorted(bsids))


def test_sda_rejects_empty_and_wide():
    from openatsc3_pki.sda import encode_bsid_sda
    with pytest.raises(ValueError):
        encode_bsid_sda([])
    with pytest.raises(ValueError):
        encode_bsid_sda([70000])


def test_private_key_permissions(tmp_path):
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root()
    import os
    mode = os.stat(a.root_key_path).st_mode & 0o777
    assert mode == 0o600


def test_published_atsc_cdt_bsid_decodes():
    """The published ATSC example CDT carries bsid 33 (RC1-CA1-LC3)."""
    import base64
    import os
    import xml.etree.ElementTree as ET

    from openatsc3_pki.x509 import load_der
    path = os.path.join(os.path.dirname(__file__), "data", "cdt_example.xml")
    if not os.path.exists(path):
        pytest.skip("published CDT example not present")
    root = ET.parse(path).getroot()
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "Certificates" and (elem.text or "").strip():
            der = base64.b64decode("".join(elem.text.split()))
            cert = load_der(der)
            if cert.bsids():
                assert cert.bsids() == (33,)
                assert cert.signaling_eku()
                return
    pytest.fail("no bsid-bearing certificate found in the example")
