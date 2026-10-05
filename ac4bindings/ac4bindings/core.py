"""Core MDCT render helpers for the A-SPX chain (TS 103 190-1 5.1.3).

The compiled kernels give a channel's ``sf_data`` (lines, scale factors,
framing) and its filterbank.  This module is the glue between them, in the
order the standard applies them:

  * :func:`packed_spectrum` -- dequantise into the bitstream-order spectrum
    (clause 5.1.3.2: ``rec_spec = sign(q)*|q|^(4/3)``,
    ``sf_gain = 2^(0.25*(sf-100))``);
  * :func:`unmix` -- undo the MDCT stereo processing (Table 113);
  * :func:`ungroup_float` -- Pseudocode 25 on the dequantised spectrum;
  * :func:`render` -- ungroup -> filterbank -> PCM.

:func:`ungroup_float` mirrors the compiled ``ungroup`` kernel but on the
dequantised (float) spectrum, which is what the A-SPX QMF analysis consumes;
the two are differentially tested against each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import synthesise as _synthesise
from .tables import SFB_OFFSET

#: Pseudocode 21 constants.
QUANT_EXP = 4.0 / 3.0
SF_OFFSET = 100
SF_DIVISOR = 4.0
SF_ABSENT = np.iinfo(np.int32).min

#: Table 104: short transform lengths by transform-length index.
SHORT_LEN = {0: 96, 1: 192, 2: 384, 3: 768}
LONG_LEN = 1536

#: Table 113: stereo processing modes.
SAP_NONE = 0
SAP_MID_SIDE_ALL = 2


@dataclass(frozen=True)
class FramingView:
    """A readable view of the C kernel's packed framing array."""
    b_long: int
    tl: tuple
    different: int
    num_windows: int
    num_groups: int
    max_sfb: tuple
    w2g: tuple
    nwin: tuple

    def _idx(self, g: int) -> int:
        """Pseudocode 5: which half of the frame group ``g`` belongs to."""
        if not self.b_long and self.different:
            nw0 = 1 << (3 - self.tl[0])
            return 1 if g >= self.w2g[nw0] else 0
        return 0

    def length_g(self, g: int) -> int:
        """The transform length of window group ``g`` (Table 104)."""
        if self.b_long:
            return LONG_LEN
        i = min(self._idx(g), len(self.tl) - 1)
        return SHORT_LEN[self.tl[i]]

    def max_sfb_g(self, g: int) -> int:
        """``max_sfb`` for group ``g``; only the first two transform lengths
        carry a value, so groups beyond them reuse the second."""
        return int(self.max_sfb[min(self._idx(g), len(self.max_sfb) - 1)])


def framing_view(packed) -> FramingView:
    from . import unpack_framing
    d = unpack_framing(packed)
    return FramingView(b_long=d["b_long"], tl=tuple(d["tl"]),
                       different=d["different"],
                       num_windows=d["num_windows"], num_groups=d["num_groups"],
                       max_sfb=tuple(d["max_sfb"]), w2g=tuple(d["w2g"]),
                       nwin=tuple(d["nwin"]))


#: Must match AC4_SF_MAX_SFB in _ac4.c: ``offsets_all``/``sfs`` rows.
SF_MAX_SFB = 64


def _group_offsets(ch, fr: FramingView):
    """``sect_sfb_offset`` per group, sliced from the flat row array."""
    off = np.asarray(ch["offsets_all"], dtype=np.int64)
    return [off[g * SF_MAX_SFB:(g + 1) * SF_MAX_SFB]
            for g in range(fr.num_groups)]


def packed_spectrum(ch, fr: FramingView = None) -> np.ndarray:
    """Dequantise one channel into the PACKED (bitstream-order) spectrum."""
    if fr is None:
        fr = framing_view(ch["framing"])
    offs = _group_offsets(ch, fr)
    total = 0
    for g in range(fr.num_groups):
        m = min(fr.max_sfb_g(g), len(offs[g]) - 1)
        total = max(total, int(offs[g][m]))
    spec = np.zeros(total)
    lines = np.asarray(ch["lines"])
    sfs_all = np.asarray(ch["sfs"])
    for g in range(fr.num_groups):
        sfs = sfs_all[g * SF_MAX_SFB:(g + 1) * SF_MAX_SFB]
        og = offs[g]
        for sfb in range(min(fr.max_sfb_g(g), len(sfs))):
            sv = int(sfs[sfb])
            if sv == SF_ABSENT:
                continue
            lo, hi = int(og[sfb]), min(int(og[sfb + 1]), total)
            if hi > lo:
                spec[lo:hi] = 2.0 ** ((sv - SF_OFFSET) / SF_DIVISOR)
    n = min(len(lines), total)
    if n > 0:
        q = np.asarray(lines[:n], dtype=np.float64)
        spec[:n] = np.sign(q) * np.abs(q) ** QUANT_EXP * spec[:n]
    return spec


def group_offsets(fr: FramingView, offsets_all) -> list:
    """``sect_sfb_offset`` per group from the kernel's flat row array."""
    off = np.asarray(offsets_all, dtype=np.int64)
    return [off[g * SF_MAX_SFB:(g + 1) * SF_MAX_SFB]
            for g in range(fr.num_groups)]


def unmix(m, s, st, fr: FramingView, offsets_all):
    """Table 113: undo the MDCT stereo processing on the packed spectrum."""
    mode = st.get("sap_mode")
    if mode == SAP_MID_SIDE_ALL:
        return m + s, m - s
    if mode == 1 and st.get("ms_used"):
        l, r = m.copy(), s.copy()
        used = st["ms_used"]
        offs = group_offsets(fr, offsets_all)
        for g in range(min(fr.num_groups, len(used))):
            off = offs[g]
            for sfb, on in enumerate(used[g]):
                if not on or sfb + 1 >= len(off):
                    continue
                lo, hi = int(off[sfb]), min(int(off[sfb + 1]), len(m))
                if hi > lo:
                    l[lo:hi] = m[lo:hi] + s[lo:hi]
                    r[lo:hi] = m[lo:hi] - s[lo:hi]
        return l, r
    return m, s


def ungroup_float(packed, fr: FramingView, n_full=LONG_LEN):
    """Pseudocode 25 on a dequantised packed spectrum.

    Returns ``(num_windows, n_full)`` float64, one row per transform window.
    Mirrors the compiled ``ungroup`` kernel; the two are differentially
    tested.  Grouped short blocks interleave their windows band by band, so
    this undoes that into per-window spectra.
    """
    lengths = [fr.length_g(fr.w2g[w]) for w in range(fr.num_windows)]
    out = np.zeros((fr.num_windows, n_full))
    k = 0
    win = 0
    for g in range(fr.num_groups):
        row = np.asarray(SFB_OFFSET[fr.length_g(g)])
        nwin = fr.nwin[g]
        for sfb in range(min(fr.max_sfb_g(g), len(row) - 1)):
            lo, hi = int(row[sfb]), int(row[sfb + 1])
            n = hi - lo
            for w in range(nwin):
                if k + n > len(packed):
                    return out, lengths
                out[win + w, lo:hi] = packed[k:k + n]
                k += n
        win += nwin
    return out, lengths


def render(ch, overlap=None, n_prev=None):
    """One channel -> ``(pcm, n_prev)`` through ungroup -> filterbank.

    The overlap buffer is updated in place by the compiled filterbank; pass
    the same array back on the next frame to carry the MDCT overlap.
    """
    fr = framing_view(ch["framing"])
    spec = packed_spectrum(ch, fr)
    wins, lengths = ungroup_float(spec, fr)
    if overlap is None:
        overlap = np.zeros(LONG_LEN, dtype=np.float64)
    if n_prev is None:
        n_prev = lengths[0]
    return _synthesise(wins, np.asarray(lengths, dtype=np.int32), LONG_LEN,
                       overlap=overlap, n_prev=n_prev)
