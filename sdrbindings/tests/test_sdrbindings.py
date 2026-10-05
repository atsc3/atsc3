"""Hardware-free tests for the sdrbindings module.

The live device path (open/stream) is exercised separately on air; these tests
only cover the pure functions and the module surface so they run anywhere.
"""

import array

import pytest

import sdrbindings
from sdrbindings import DeviceInfo, _core


def test_module_constants():
    assert sdrbindings.CS16 == "CS16"
    assert sdrbindings.CS8 == "CS8"
    assert sdrbindings.CF32 == "CF32"
    assert sdrbindings.RX != sdrbindings.TX


def test_format_to_size():
    assert sdrbindings.format_to_size("CS16") == 4
    assert sdrbindings.format_to_size("CS8") == 2
    assert sdrbindings.format_to_size("CF32") == 8


def test_enumerate_returns_device_infos():
    result = sdrbindings.enumerate()
    assert isinstance(result, list)
    for entry in result:
        assert isinstance(entry, DeviceInfo)


def test_device_info_from_mapping():
    info = DeviceInfo.from_mapping(
        {"driver": "sdrplay", "label": "RSP1B", "serial": "abc",
         "antenna": "RX"})
    assert info.driver == "sdrplay"
    assert info.label == "RSP1B"
    assert info.serial == "abc"
    assert info.extra == (("antenna", "RX"),)


def test_probe_returns_bool():
    assert isinstance(sdrbindings.probe("sdrplay"), bool)


def test_cs16_to_cs8_matches_shift():
    values = array.array("h", [0x1234, -0x1234, 0x7FFF, -0x8000, 256, -256])
    buf = values.tobytes()
    out = sdrbindings._cs16_to_cs8(buf)
    got = array.array("b")
    got.frombytes(out)
    assert list(got) == [(v >> 8) for v in values]


def test_cs16_to_cs8_length():
    buf = b"\x00" * (4 * 1000)
    assert len(sdrbindings._cs16_to_cs8(buf)) == 2 * 1000


def test_device_type_exposed():
    assert hasattr(_core, "Device")
    assert _core.Device.__name__ == "Device"


def test_capture_iq_rejects_missing_device(monkeypatch):
    """capture_iq must raise, not segfault, when no device is present."""
    if sdrbindings.probe("sdrplay"):
        pytest.skip("SDRplay present; cannot test the absent-device path")
    with pytest.raises(RuntimeError):
        sdrbindings.capture_iq(587e6, "/tmp/none.iq", duration_sec=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
