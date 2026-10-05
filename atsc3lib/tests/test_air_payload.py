"""End-to-end payload decode from a real ATSC 3.0 broadcast.

A 1.3 Msample 10 MS/s slice of the RF33 (587 MHz) capture, saved around the
bootstrap.  This exercises the full receive path including the bootstrap
fractional-CFO correction and the A/322 guarded-interval mapping that must be
right for OFDM symbols to line up:

    raw IQ -> resample 6.144M -> bootstrap -> fine CFO -> resample 6.912M
           -> Preamble -> L1-Basic/L1-Detail -> subframe cell pool
           -> QPSK 2/15 PLP-16 -> LDPC/BCH/descramble -> Baseband Packet

The decoded PLP-16 Baseband Packet must equal the independent receiver's.
"""

import os

import numpy as np
import pytest

from atsc3lib.receiver import decode_signaling, decode_plp_payload
from atsc3lib.payload import decode_streams

_DATA = os.path.join(os.path.dirname(__file__), 'data')
_SLICE = os.path.join(_DATA, 'rf33_acquire_slice.npy')
_BB = os.path.join(_DATA, 'rf33_plp16_bb.bin')

pytestmark = pytest.mark.skipif(
    not os.path.exists(_SLICE), reason="real-air capture slice not present")


def test_live_plp16_payload_matches_oracle():
    iq = np.load(_SLICE)
    result = decode_signaling(iq, 10e6)
    assert result.l1_basic_ok and result.l1_detail_ok
    result, payload = decode_plp_payload(iq, 10e6, result=result)
    assert payload is not None
    assert payload.plp_id == 16
    assert payload.n_converged == 1
    packets = payload.baseband_packets
    assert len(packets) == 1
    if os.path.exists(_BB):
        assert packets[0] == open(_BB, 'rb').read()


class TestFramePeriod:
    """``frame_samples`` must sum every subframe, not just subframe 0.

    On RF33 (subframe 0 = 8192/1536, subframe 1 = 16384/1536) the true
    bootstrap-to-bootstrap period is 1,708,032 main samples = 247.1 ms,
    measured on air.  A version summing only subframe 0 returns 364,032 —
    the start of subframe 1 — which desynchronises frame stepping and made
    multi-frame MPU assembly impossible.
    """

    def test_frame_period_includes_subframe1(self):
        from atsc3lib.receiver import decode_signaling, frame_samples
        result = decode_signaling(np.load(_SLICE), 10e6)
        assert len(result.l1_detail.subframes) == 2
        assert frame_samples(result) == 1708032


class TestAcquiredMainGuard:
    """decode_plp_frame must reject a raw (unacquired) main stream.

    A raw `resample_iq(iq, MAIN_RATE_HZ)` is not frame-aligned or CFO-corrected
    and silently decodes to 0/N; the guard turns that into a clear error.  The
    acquired slice (the supported path) must still decode.
    """

    def test_acquired_slice_passes_guard(self):
        from atsc3lib.receiver import bootstrap_lock_metric
        from atsc3lib.receiver import _bootstrap_to_preamble
        sl = np.load(_SLICE)
        _, main, _, _ = _bootstrap_to_preamble(sl, 10e6)
        assert bootstrap_lock_metric(main) > 0.5

    def test_raw_resample_is_rejected(self):
        from atsc3lib.receiver import (
            _bootstrap_to_preamble, _require_bootstrap_aligned, select_plp,
            decode_signaling, decode_plp_frame)
        from atsc3lib.frontend import resample_iq
        from atsc3lib import spec
        sl = np.load(_SLICE)
        result = decode_signaling(sl, 10e6)
        _, _, structure, _ = _bootstrap_to_preamble(sl, 10e6)
        target = select_plp(result, 16, 0)
        raw_main = resample_iq(sl, 10e6, spec.MAIN_RATE_HZ)
        with pytest.raises(ValueError):
            _require_bootstrap_aligned(raw_main)
        with pytest.raises(ValueError):
            decode_plp_frame(result, structure, raw_main, target, 0)

    def test_guard_metric_separates_noise(self):
        from atsc3lib.receiver import bootstrap_lock_metric, _BOOTSTRAP_LOCK_MIN
        rng = np.random.default_rng(0)
        noise = (rng.normal(0, 1, 200000)
                 + 1j * rng.normal(0, 1, 200000)).astype(np.complex64)
        assert bootstrap_lock_metric(noise) < _BOOTSTRAP_LOCK_MIN


def test_live_plp16_bbp_header_and_streams():
    # PLP-16's one Baseband Packet is padding-only (no ALP starts): this gates
    # the A/322 5.2.2 header parse and the A/330 ALP walk on real air.
    iq = np.load(_SLICE)
    result = decode_signaling(iq, 10e6)
    result, payload = decode_plp_payload(iq, 10e6, result=result)
    assert payload is not None
    streams = decode_streams(payload)
    assert streams.packets == []
    assert streams.datagrams == []
    assert streams.alp_stats.resync == 0
    assert streams.alp_stats.single == 0
