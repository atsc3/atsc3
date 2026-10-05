"""Own content-protection scheme: CENC round trip, boxes, A/331 descriptor."""

import uuid

import pytest

from openatsc3_pki import content


def test_ctr_roundtrip_byte_exact():
    key = content.generate_key()
    iv = b"\x00\x01\x02\x03\x04\x05\x06\x07"
    sample = bytes(range(256)) * 8
    ct = content.encrypt_sample(sample, key, iv)
    assert ct != sample
    assert content.decrypt_sample(ct, key, iv) == sample


def test_ctr_16byte_iv_roundtrip():
    key = content.generate_key()
    iv = content.generate_kid()
    sample = b"a" * 1000
    assert content.decrypt_sample(content.encrypt_sample(sample, key, iv),
                                  key, iv) == sample


def test_wrong_key_or_iv_fails():
    key = content.generate_key()
    iv = content.generate_kid()
    sample = b"payload" * 10
    ct = content.encrypt_sample(sample, key, iv)
    assert content.decrypt_sample(ct, content.generate_key(), iv) != sample
    other_iv = content.generate_kid()
    assert content.decrypt_sample(ct, key, other_iv) != sample


def test_keystore():
    ks = content.KeyStore()
    key = content.generate_key()
    kid = content.generate_kid()
    ks.add(kid, key)
    assert ks.key_for(kid) == key
    assert ks.require(kid) == key
    with pytest.raises(KeyError):
        ks.require(content.generate_kid())


def test_keystore_rejects_bad_lengths():
    ks = content.KeyStore()
    with pytest.raises(ValueError):
        ks.add(b"short", content.generate_key())
    with pytest.raises(ValueError):
        ks.add(content.generate_kid(), b"short")


def test_pssh_roundtrip():
    kid = content.generate_kid()
    box = content.build_pssh(kid)
    sid, kids, data = content.parse_pssh(box)
    assert sid == content.OPENATSC3_DRM_UUID.bytes
    assert kids == [kid]
    assert data == b""


def test_pssh_v1_with_data_roundtrip():
    kid = content.generate_kid()
    box = content.build_pssh(kid, data=b"drm-specific")
    sid, kids, data = content.parse_pssh(box)
    assert kids == [kid]
    assert data == b"drm-specific"


def test_tenc_roundtrip():
    kid = content.generate_kid()
    box = content.build_tenc(kid)
    default_kid, iv_size, is_protected, version = content.parse_tenc(box)
    assert default_kid == kid
    assert iv_size == 8
    assert is_protected == 1
    assert version == 1


def test_schm_roundtrip():
    box = content.build_schm(content.SCHEME_CENC)
    scheme, version = content.parse_schm(box)
    assert scheme == b"cenc"
    assert version == 0x00010000


def test_senc_roundtrip_default_kid():
    ivs = [b"\x00" * 8, b"\x01" * 8]
    box = content.build_senc(ivs)
    parsed = content.parse_senc(box)
    assert parsed == [(ivs[0], None), (ivs[1], None)]


def test_senc_roundtrip_per_sample_kid():
    kid0, kid1 = content.generate_kid(), content.generate_kid()
    ivs = [b"\x00" * 8, b"\x01" * 8]
    box = content.build_senc(ivs, kids=[kid0, kid1], flags=0x2)
    parsed = content.parse_senc(box)
    assert parsed == [(ivs[0], kid0), (ivs[1], kid1)]


def test_security_properties_descriptor_roundtrip():
    kid = content.generate_kid()
    assets = [
        content.ProtectedAsset(asset_id=b"\x00\x01", scheme_code=b"cenc",
                               default_kid=kid),
        content.ProtectedAsset(asset_id=b"\x00\x02"),  # defaults -> scheme cenc
    ]
    der = content.security_properties_descriptor(assets)
    parsed = content.parse_security_properties_descriptor(der)
    assert parsed[0].asset_id == b"\x00\x01"
    assert parsed[0].scheme_code == b"cenc"
    assert parsed[0].default_kid == kid
    assert parsed[1].asset_id == b"\x00\x02"
    assert parsed[1].scheme_code is None
    assert parsed[1].default_kid is None


def test_security_properties_descriptor_tag():
    der = content.security_properties_descriptor(
        [content.ProtectedAsset(asset_id=b"x", default_kid=content.generate_kid())])
    assert der[:2] == b"\x00\x0c"


def test_mpd_content_protection_names_our_uuid():
    kid = content.generate_kid()
    el = content.content_protection_mpd(kid)
    assert f"urn:uuid:{content.OPENATSC3_DRM_UUID}" in el
    assert "cenc" in el
    assert str(uuid.UUID(bytes=kid)) in el
