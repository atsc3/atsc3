"""CertificationData (0x06) and SignedMultiTable (0x07) structural tests."""

import datetime as dt

from openatsc3_pki import cdt


def test_cdt_is_gzipped_xml(signed_cdt):
    body = signed_cdt["cdt"].gzip_bytes()
    assert body[:2] == b"\x1f\x8b"
    xml = signed_cdt["cdt"].xml()
    assert xml.startswith("<?xml")
    assert "CertificationData" in xml
    assert 'OCSPRefresh="PT20H"' in xml


def test_cdt_parse_roundtrip(signed_cdt):
    parsed = cdt.parse_certification_data(signed_cdt["cdt"].gzip_bytes())
    assert parsed.certificates == signed_cdt["cdt"].certificates
    assert parsed.current_cert == signed_cdt["cdt"].current_cert
    assert len(parsed.ocsp_responses) == 3
    # tobesigned is recomputed deterministically from the parsed fields
    assert parsed.tobesigned_bytes() == signed_cdt["cdt"].tobesigned


def test_cdt_root_not_included(signed_cdt):
    from openatsc3_pki.x509 import load_der
    subjects = {load_der(d).subject for d in signed_cdt["cdt"].certificates}
    assert signed_cdt["root"].subject not in subjects


def test_signed_multitable_roundtrip(signed_smt):
    assert len(signed_smt.tables) == 1
    table = signed_smt.tables[0]
    assert table.table_id == 0x01
    assert table.version == 13
    assert table.payload == b'<SLT bsid="540"/>'


def test_signed_multitable_multiple_tables(signed_cdt):
    from openatsc3_pki.cdt import (
        LlsTable,
        parse_signed_multitable,
        signed_multitable,
    )
    body = signed_multitable(
        [LlsTable(0x01, 1, b"slt"), LlsTable(0x02, 2, b"rrt")],
        signed_cdt["signer"], signed_cdt["signer_key"],
        signing_time=signed_cdt["now"])
    parsed = parse_signed_multitable(body)
    assert [t.table_id for t in parsed.tables] == [0x01, 0x02]
    assert [t.payload for t in parsed.tables] == [b"slt", b"rrt"]


def test_signed_multitable_truncated_raises(signed_smt):
    import pytest
    with pytest.raises(ValueError):
        cdt.parse_signed_multitable(b"\x01\x01")
