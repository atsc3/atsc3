"""Unit tests for the ATSC 3.0 front-end (resampling + acquisition)."""

import numpy as np
import pytest

from atsc3lib.bootstrap import generate_bootstrap
from atsc3lib.frontend import (
    resample_iq, acquire_frame, bootstrap_span_main_rate,
    StreamingResampler,
    BOOTSTRAP_RATE_HZ, MAIN_RATE_HZ,
)
from scipy.signal import upfirdn


class TestStreamingResampler:
    """The live path's phase-exact streaming resampler."""

    def test_matches_whole_stream_upfirdn(self):
        # Chunked output must equal the single-shot polyphase transform of the
        # whole input exactly (up to its constant group delay): no per-window
        # phase restart.
        rng = np.random.default_rng(0)
        x = (rng.standard_normal(300000)
             + 1j * rng.standard_normal(300000)).astype(np.complex128)
        sr = StreamingResampler(10e6, MAIN_RATE_HZ)
        out = np.concatenate([sr.feed(x[i:i + 7000])
                              for i in range(0, x.size, 7000)])
        whole = upfirdn(sr.h, x, sr.up, sr.down)
        # The stream emits every output whose input window is fully available;
        # the single-shot transform also carries the right-edge filter tail.
        assert out.size == x.size * sr.up // sr.down
        assert np.array_equal(out, whole[:out.size].astype(np.complex64))

    def test_identity_rate_is_passthrough(self):
        sr = StreamingResampler(MAIN_RATE_HZ, MAIN_RATE_HZ)
        x = np.arange(10, dtype=np.complex64)
        assert np.array_equal(sr.feed(x), x)

    def test_history_is_bounded(self):
        sr = StreamingResampler(10e6, MAIN_RATE_HZ)
        x = np.zeros(50000, dtype=np.complex64)
        for i in range(0, x.size, 5000):
            sr.feed(x[i:i + 5000])
        # Buffer never exceeds a phase period plus the latest chunk.
        assert sr._buf.size <= sr.lcm + 5000


class TestResample:
    def test_identity(self):
        iq = (np.random.default_rng(0).normal(size=1000) +
              1j * np.random.default_rng(1).normal(size=1000)).astype(np.complex64)
        out = resample_iq(iq, BOOTSTRAP_RATE_HZ, BOOTSTRAP_RATE_HZ)
        assert np.array_equal(out, iq)

    def test_upsample_length(self):
        iq = np.ones(1000, dtype=np.complex64)
        out = resample_iq(iq, 1_000_000, 2_000_000)
        assert abs(len(out) - 2000) < 5

    def test_downsample_length(self):
        iq = np.ones(2000, dtype=np.complex64)
        out = resample_iq(iq, 2_000_000, 1_000_000)
        assert abs(len(out) - 1000) < 5


class TestBootstrapSpan:
    def test_span_ratio(self):
        # Native bootstrap is 12288 samples at 6.144 MHz -> 9/8 at 6.912 MHz.
        assert bootstrap_span_main_rate() == 13824


class TestAcquireFrame:
    @pytest.mark.parametrize('structure', [6, 10, 30])
    def test_acquire_from_10mhz(self, structure):
        wf = generate_bootstrap(structure, major=0, minor=0)
        iq_fs = resample_iq(wf, BOOTSTRAP_RATE_HZ, 10e6).astype(np.complex64)
        rng = np.random.default_rng(structure)
        noise = (rng.normal(0, 0.05, len(iq_fs)) +
                 1j * rng.normal(0, 0.05, len(iq_fs))).astype(np.complex64)
        pre = (rng.normal(0, 0.05, 30000) +
               1j * rng.normal(0, 0.05, 30000)).astype(np.complex64)
        rx = np.concatenate([pre, (iq_fs + noise).astype(np.complex64)])

        info = acquire_frame(rx, 10e6)
        assert info.bootstrap.structure == structure
        assert info.fft_size in (8192, 16384, 32768)
        assert info.symbol_size == info.fft_size + info.guard_interval
        assert info.bootstrap_main_start >= 0
