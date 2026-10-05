"""sdrbindings: CPython bindings for the SoapySDR C API.

A thin, dependency-free wrapper around the SoapySDR ``Device.h`` C API so
Python can open an SDR (the SDRplay RSP1B in this project), configure its
front end and stream IQ samples without shelling out to a helper program.

The low-level API mirrors the C calls::

    import sdrbindings

    sdrbindings.probe("sdrplay")
    for info in sdrbindings.enumerate("sdrplay"):
        print(info)

    with sdrbindings.Device("sdrplay", index=0) as dev:
        dev.set_sample_rate(10e6)
        dev.set_frequency(587e6)
        dev.set_bandwidth(8e6)
        dev.set_antenna("RX")
        dev.set_gain_element("IFGR", 45)
        dev.set_gain_element("RFGR", 3)
        dev.setup_stream("CS16")
        dev.activate_stream()
        buf = dev.read(timeout_us=1_000_000)   # bytes, or None on timeout
        dev.deactivate_stream()

``capture_iq`` is a convenience wrapper that streams for a duration and writes
interleaved samples to a file.  ``"CS16"`` writes native little-endian int16
IQ (4 bytes per sample); ``cs8=True`` writes int8 IQ (2 bytes per sample).
"""

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from . import _core
from ._core import CF32, CS8, CS16, Device, RX, TX, format_to_size

__all__ = [
    "Device", "DeviceInfo", "enumerate", "probe", "capture_iq",
    "format_to_size", "RX", "TX", "CS8", "CS16", "CF32",
]

__version__ = "0.1.0"

DEFAULT_DRIVER = "sdrplay"
DEFAULT_FORMAT = CS16
DEFAULT_TIMEOUT_US = 1_000_000
IFGR = "IFGR"
RFGR = "RFGR"
RX_ANTENNA = "RX"


@dataclass(frozen=True)
class DeviceInfo:
    """One enumerated SoapySDR device.

    The SoapySDR argument map is an open key/value set, so the two keys this
    project relies on are named (``driver``, ``label``) and the remainder is
    kept verbatim in ``extra``.
    """
    driver: str
    label: str
    serial: str = ""
    extra: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, args: Mapping[str, str]) -> "DeviceInfo":
        known = {"driver", "label", "serial"}
        return cls(
            driver=args.get("driver", ""),
            label=args.get("label", ""),
            serial=args.get("serial", ""),
            extra=tuple(sorted((k, v) for k, v in args.items()
                               if k not in known)),
        )


def enumerate(driver=None):
    """Return the :class:`DeviceInfo` list for ``driver``."""
    return [DeviceInfo.from_mapping(a) for a in _core.enumerate(driver)]


def probe(driver=DEFAULT_DRIVER):
    """True when at least one device of ``driver`` is present."""
    return _core.probe(driver)


#: Bytes per complex sample for a CS16 wire stream (int16 I + int16 Q).
CS16_SAMPLE_BYTES = 4
#: Bytes per complex sample written when ``cs8=True`` (int8 I + int8 Q).
CS8_SAMPLE_BYTES = 2


def _cs16_to_cs8(buf):
    """Down-convert interleaved int16 IQ bytes to int8 with ``>> 8``.

    Matches the legacy ``soapy_capture --cs8`` conversion exactly: on a
    little-endian host the high byte of each int16, reinterpreted as a signed
    int8, equals ``(int16 >> 8)`` cast to ``int8``.
    """
    import array
    out = array.array("b")
    out.frombytes(buf[1::2])
    return out.tobytes()


def capture_iq(
    freq_hz,
    out_path,
    rate_hz=10e6,
    bandwidth_hz=8e6,
    duration_sec=10.0,
    ifgr=40,
    rfgr=4,
    driver=DEFAULT_DRIVER,
    index=0,
    cs8=False,
    antenna=RX_ANTENNA,
    timeout_us=DEFAULT_TIMEOUT_US,
):
    """Stream IQ from an SDRplay into ``out_path``.

    Writes native interleaved int16 IQ unless ``cs8`` is set, in which case
    each int16 sample is shifted down to int8 (``>> 8``) so the on-disk layout
    matches the legacy 8-bit captures.  Returns the number of complex samples
    written.
    """
    with Device(driver, index=index) as dev:
        dev.set_sample_rate(rate_hz)
        dev.set_frequency(freq_hz)
        dev.set_bandwidth(bandwidth_hz)
        dev.set_antenna(antenna)
        if dev.has_gain_mode():
            dev.set_gain_mode(False)
        dev.set_gain_element(IFGR, float(ifgr))
        dev.set_gain_element(RFGR, float(rfgr))
        dev.setup_stream(CS16)
        dev.activate_stream()

        target = int(rate_hz * float(duration_sec))
        got = 0
        with open(out_path, "wb") as fp:
            while got < target:
                want = min(dev.get_stream_mtu(), target - got)
                try:
                    buf = dev.read(num_elems=want, timeout_us=timeout_us)
                except BufferError:
                    continue
                if buf is None:
                    continue
                fp.write(_cs16_to_cs8(buf) if cs8 else buf)
                got += len(buf) // CS16_SAMPLE_BYTES
        dev.deactivate_stream()
    return got


def main(argv=None):
    """Command-line capture, a drop-in for the old soapy_capture binary."""
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Capture IQ from a SoapySDR device (SDRplay RSP1B).")
    parser.add_argument("--freq", type=float, required=False,
                        help="Center frequency in Hz")
    parser.add_argument("--rate", type=float, default=10e6,
                        help="Sample rate in Hz")
    parser.add_argument("--bw", type=float, default=8e6,
                        help="Bandwidth in Hz")
    parser.add_argument("--ifgr", type=float, default=40, help="IF gain")
    parser.add_argument("--rfgr", type=float, default=4, help="RF gain")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Capture duration in seconds")
    parser.add_argument("--out", help="Output file")
    parser.add_argument("--driver", default=DEFAULT_DRIVER)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--cs8", action="store_true",
                        help="Write int8 IQ instead of native int16")
    parser.add_argument("--probe", action="store_true",
                        help="Enumerate devices and exit")
    args = parser.parse_args(argv)

    if args.probe:
        for info in enumerate(args.driver):
            print(info)
        return 0 if probe(args.driver) else 1

    if args.out is None or args.freq is None:
        parser.error("--freq and --out are required")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    got = capture_iq(
        args.freq, args.out, rate_hz=args.rate, bandwidth_hz=args.bw,
        duration_sec=args.duration, ifgr=args.ifgr, rfgr=args.rfgr,
        driver=args.driver, index=args.index, cs8=args.cs8)
    print("captured %d samples -> %s" % (got, args.out), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
