"""Tests for the customer helper ``tools/openatsc3-smt-fetch.py`` (stdlib only)."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "openatsc3-smt-fetch.py"


def _load():
    spec = importlib.util.spec_from_file_location("openatsc3_smt_fetch", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smt_fetch = _load()


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_lls_wrap_header_and_body():
    body = b"\x01\x02\x03"
    wrapped = smt_fetch.lls_wrap(0x06, body)
    assert wrapped[:4] == bytes([0x06, 0x00, 0x00, 0x01])
    assert wrapped[4:] == body
    assert smt_fetch.lls_wrap(0x07, body, version=3)[:4] == bytes([0x07, 0, 0, 3])


def test_build_request_base64_encodes_tables():
    request = smt_fetch.build_request("WHUT", [(0x01, 1, b"sl"), (0x03, 2, b"app")])
    assert request["broadcaster"] == "WHUT"
    assert request["tables"][0] == {
        "table_id": 0x01, "version": 1,
        "payload_b64": base64.b64encode(b"sl").decode("ascii")}
    assert request["tables"][1]["version"] == 2


def test_decode_response_keeps_cdt_body_gzipped():
    cdt = b"\x1f\x8b\x08gzipped"
    smt = b"smt-cms"
    decoded = smt_fetch.decode_response({
        "cdt_b64": base64.b64encode(cdt).decode(),
        "smt_b64": base64.b64encode(smt).decode()})
    assert decoded["cdt"] == cdt          # NOT decompressed: it is the 0x06 body
    assert decoded["smt"] == smt
    assert smt_fetch.decode_response(
        {"cdt_b64": base64.b64encode(cdt).decode()})["smt"] is None
    with pytest.raises(ValueError):
        smt_fetch.decode_response({})


def test_parse_table_with_and_without_version():
    assert smt_fetch._parse_table("0x01:slt.bin") == (1, 1, Path("slt.bin"))
    assert smt_fetch._parse_table("6:2:cdt.bin") == (6, 2, Path("cdt.bin"))
    with pytest.raises(ValueError):
        smt_fetch._parse_table("only-one-part")


def test_post_smt_sends_auth_and_decodes(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return _FakeResponse(json.dumps({
            "cdt_b64": base64.b64encode(b"cdt").decode(),
            "smt_b64": base64.b64encode(b"smt").decode()}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = smt_fetch.fetch("https://ca.example.com/", "tok", "WHUT",
                             [(0x01, 1, b"tbl")])
    assert seen["url"] == "https://ca.example.com/api/v1/smt/"
    assert seen["auth"] == "Token tok"
    assert seen["body"]["broadcaster"] == "WHUT"
    assert result == {"cdt": b"cdt", "smt": b"smt"}


def test_post_smt_http_error_is_clear(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", None,
            io.BytesIO(b'{"error": "bad token"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        smt_fetch.post_smt("https://ca.example.com", "tok", {"tables": []})


def test_main_writes_lls_bodies(tmp_path, monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({
            "cdt_b64": base64.b64encode(b"cdt-body").decode(),
            "smt_b64": base64.b64encode(b"smt-body").decode()}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    table = tmp_path / "slt.bin"
    table.write_bytes(b"slt")
    out = tmp_path / "out"

    code = smt_fetch.main([
        "--base-url", "https://ca.example.com", "--token", "tok",
        "--broadcaster", "WHUT", "--table", f"0x01:{table}",
        "--out-dir", str(out)])

    assert code == 0
    assert (out / "cdt.xml.gz").read_bytes() == b"cdt-body"
    assert (out / "lls-0x06.bin").read_bytes() == bytes([6, 0, 0, 1]) + b"cdt-body"
    assert (out / "lls-0x07.bin").read_bytes() == bytes([7, 0, 0, 1]) + b"smt-body"


def test_main_requires_a_table(tmp_path):
    with pytest.raises(SystemExit):
        smt_fetch.main(["--base-url", "https://x", "--token", "t",
                        "--broadcaster", "WHUT", "--out-dir", str(tmp_path)])
