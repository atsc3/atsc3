"""The security provider seam: registration, selection, and swappability.

Proves the receiver is not hard-wired to one CA/DRM: a stub provider can be
registered and selected, and the report types are shared across providers.
"""

from atsc3lib import security
from atsc3lib.security import base


class _StubProvider:
    """A minimal provider that records which table it was asked to verify."""

    name = "stub-test"

    def __init__(self):
        self.cdt_calls = 0
        self.signed_calls = 0

    def load_trust_anchors(self, paths):
        return ["stub-anchor"]

    def verify_certification_data(self, table, anchors, now=None):
        self.cdt_calls += 1
        verdict = base.TableVerdict(table.table_id, table.name, True, "stub ok")
        return verdict, "stub-handle"

    def verify_signed_table(self, table, handle, *, expected_bsids=None,
                            now=None, previous_signing_time=None):
        self.signed_calls += 1
        assert handle == "stub-handle"
        return base.TableVerdict(table.table_id, table.name, True, "stub ok")


def test_builtin_provider_is_registered():
    assert "openatsc3" in security.available()
    assert security.get_provider("openatsc3").name == "openatsc3"


def test_set_and_get_provider_by_name():
    stub = _StubProvider()
    security.set_provider(stub)
    try:
        assert security.get_provider().name == "stub-test"
        assert security.get_provider("stub-test") is stub
        assert "stub-test" in security.available()
    finally:
        security.set_provider("openatsc3")


def test_unknown_provider_raises():
    import pytest
    with pytest.raises(KeyError):
        security.get_provider("does-not-exist")


def test_verify_streams_uses_selected_provider():
    stub = _StubProvider()
    security.set_provider(stub)
    try:
        # Minimal duck-typed streams: a CDT (0x06) and a signed table (0x07).
        cdt = security.SignableTable(0x06, "CertificationData", b"cdt-bytes")
        smt = security.SignableTable(0x07, "SignedMultiTable", b"smt-bytes")

        class _T:
            def __init__(self, t):
                self.table_id = t.table_id
                self.name = t.name
                self.data = t.data
                self.table_version = 0

        streams = type("S", (), {"lls": [_T(cdt), _T(smt)], "slt": None})()
        report = security.verify_streams(streams, ["x"])
        assert report.ok
        assert report.provider == "stub-test"
        assert stub.cdt_calls == 1
        assert stub.signed_calls == 1
        assert streams.verification is report
    finally:
        security.set_provider("openatsc3")


def test_environment_selects_provider(monkeypatch):
    stub = _StubProvider()
    security.register(stub)
    # Reset the active provider so the environment is consulted again.
    base._ACTIVE = None
    monkeypatch.setenv("ATSC3LIB_SECURITY_PROVIDER", "stub-test")
    try:
        assert security.get_provider().name == "stub-test"
    finally:
        monkeypatch.delenv("ATSC3LIB_SECURITY_PROVIDER", raising=False)
        base._ACTIVE = None
        security.set_provider("openatsc3")
