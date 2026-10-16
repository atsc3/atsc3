"""ATSC 3.0 front-end: resampling and bootstrap-based frame acquisition.

Bridges raw SDR captures to the bootstrap/L1 decoders.

Sample rates (A/322 Annex N.2.2):
- Bootstrap signaling is defined at 6.144 MHz.
- The main OFDM signal uses 0.384 * (bsr_coefficient + 16) MHz; for the
  default bsr_coefficient = 2 this is 6.912 MHz.

A capture taken at an arbitrary SDR rate (e.g. 10 MHz for the SDRplay) must be
resampled to 6.144 MHz before bootstrap detection, then resampled again /
decimated to 6.912 MHz for OFDM demodulation.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Tuple

import numpy as np
from scipy.signal import firwin, resample_poly, upfirdn

from . import spec
from .bootstrap import (
    detect_bootstrap, decode_preamble_structure, BootstrapDetection,
    BootstrapParams, C_SIZE, BOOTSTRAP_FFT_SIZE, B_SIZE, NUM_BOOTSTRAP_SYMBOLS,
)

BOOTSTRAP_RATE_HZ = spec.BOOTSTRAP_RATE_HZ
MAIN_RATE_HZ = spec.MAIN_RATE_HZ
BOOTSTRAP_SAMPLES_NATIVE = spec.BOOTSTRAP_TOTAL_SAMPLES


def resample_iq(iq: np.ndarray, fs_in: float, fs_out: float) -> np.ndarray:
    """Rational polyphase resample complex IQ from fs_in to fs_out."""
    if abs(fs_in - fs_out) < 1e-6:
        return iq
    from fractions import Fraction
    ratio = Fraction(fs_out / fs_in).limit_denominator(1000)
    return resample_poly(iq, ratio.numerator, ratio.denominator).astype(np.complex64)


class StreamingResampler:
    """Phase-exact streaming rational resampler (the stateful polyphase form).

    A live receiver must resample an SDR stream window by window and still keep
    every window on the same sample grid — a per-window ``resample_poly``
    restarts the polyphase phase at each window, so frames drift.  This carries
    the filter history across ``feed`` calls and emits the *same* samples the
    single-shot transform would, delayed by the fixed filter group delay: the
    emitted stream equals ``upfirdn(h, whole_input, up, down)`` exactly (gated in
    ``tests/test_frontend.py``).  The input buffer is bounded — history is
    dropped in multiples of ``up*down`` input samples, which keeps the polyphase
    phase identical — so a live run cannot grow it without limit.
    """

    def __init__(self, fs_in: float, fs_out: float,
                 window: tuple = ('kaiser', 5.0)):
        ratio = Fraction(fs_out / fs_in).limit_denominator(1000)
        self.up = ratio.numerator
        self.down = ratio.denominator
        self.identity = (self.up == 1 and self.down == 1)
        if not self.identity:
            max_rate = max(self.up, self.down)
            self.h = firwin(2 * 10 * max_rate + 1, 1.0 / max_rate,
                            window=window).astype(np.complex128) * self.up
            self.Lh = self.h.size
            #: History-dropping granularity: the polyphase phase repeats every
            #: lcm(up, down) = up*down input samples (they are coprime).
            self.lcm = self.up * self.down
        self.reset()

    def reset(self):
        """Forget all history; the next :meth:`feed` starts a fresh stream."""
        self._buf = np.zeros(0, dtype=np.complex128)
        self._base = 0
        self._m_next = 0

    def feed(self, x: np.ndarray) -> np.ndarray:
        """Resample the next input chunk; return the newly available output."""
        x = np.asarray(x)
        if self.identity:
            return x.astype(np.complex64, copy=False)
        if x.size == 0:
            return np.zeros(0, dtype=np.complex64)
        self._buf = np.concatenate([self._buf, x.astype(np.complex128)])
        last = self._base + self._buf.size - 1
        # Largest output index m whose newest contributing input (floor(m*down/up))
        # is within the buffer.
        m_max = ((last + 1) * self.up - 1) // self.down
        if m_max < self._m_next:
            return np.zeros(0, dtype=np.complex64)
        y = upfirdn(self.h, self._buf, self.up, self.down)
        shift = self._base * self.up // self.down
        out = y[self._m_next - shift:m_max - shift + 1].copy()
        self._m_next = m_max + 1
        # Keep only the history the next output needs, rounded down to a whole
        # phase period so the polyphase phase (and thus the output) is unchanged.
        n_min = max(0, (self._m_next * self.down - (self.Lh - 1)
                        + self.up - 1) // self.up)
        new_base = (n_min // self.lcm) * self.lcm
        if new_base > self._base:
            self._buf = self._buf[new_base - self._base:]
            self._base = new_base
        return out.astype(np.complex64)

    @property
    def delay(self) -> int:
        """Output samples of group delay relative to the trimmed ``resample_poly``."""
        if self.identity:
            return 0
        half_len = (self.Lh - 1) // 2
        n_pre_pad = self.down - half_len % self.down
        return (half_len + n_pre_pad) // self.down


def read_hackrf_iq(path: str, limit: Optional[int] = None) -> np.ndarray:
    """Read an int8 interleaved IQ file (SDRplay/HackRF CS8) into complex64."""
    data = np.fromfile(path, dtype=np.int8)
    if limit is not None:
        data = data[:limit * 2]
    return (data[::2].astype(np.float32) +
            1j * data[1::2].astype(np.float32)) / 128.0


def read_cs8_iq(path: str, limit: Optional[int] = None) -> np.ndarray:
    """Read an interleaved int8 (CS8) IQ capture into complex64."""
    return read_hackrf_iq(path, limit)


@dataclass
class FrameInfo:
    """Acquired frame parameters."""
    bootstrap: BootstrapDetection
    params: BootstrapParams
    bootstrap_main_start: int
    symbol_size: int  # FFT + guard, at the main sample rate

    @property
    def fft_size(self) -> int:
        return self.params.fft_size

    @property
    def guard_interval(self) -> int:
        return self.params.guard_interval


def acquire_frame(iq_main: np.ndarray, fs_main: float,
                  search_limit: int = 4_000_000) -> FrameInfo:
    """Resample, detect the bootstrap, and compute frame timing.

    Args:
        iq_main: Complex IQ at the capture sample rate.
        fs_main: Capture sample rate in Hz.
        search_limit: Max main-rate samples to resample for the search.

    Returns:
        FrameInfo with bootstrap detection and OFDM symbol geometry.
    """
    iq_a = iq_main if search_limit is None else iq_main[:search_limit]

    iq_boot = resample_iq(iq_a, fs_main, BOOTSTRAP_RATE_HZ)
    det = detect_bootstrap(iq_boot)
    params = decode_preamble_structure(det.structure)

    # Map the bootstrap start into the main-rate sample index.
    bootstrap_main_start = int(round(det.start * MAIN_RATE_HZ / BOOTSTRAP_RATE_HZ))
    symbol_size = params.fft_size + params.guard_interval

    return FrameInfo(
        bootstrap=det, params=params,
        bootstrap_main_start=bootstrap_main_start,
        symbol_size=symbol_size,
    )


def bootstrap_span_main_rate() -> int:
    """Length of the bootstrap in main-rate samples (9/8 of native)."""
    return int(round(BOOTSTRAP_SAMPLES_NATIVE * MAIN_RATE_HZ / BOOTSTRAP_RATE_HZ))
