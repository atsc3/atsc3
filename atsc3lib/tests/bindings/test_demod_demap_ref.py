"""Differential tests: the C demapper must match the NumPy reference exactly."""

import numpy as np
import pytest

_demod = pytest.importorskip("atsc3lib._bindings.demod")

from atsc3lib._bindings.demod import demap_llr  # noqa: E402

#: The C kernel is gated against the true NumPy reference ``_demap_llr_numpy``
#: (``nuc.demap_llr`` with no sigma2 dispatches straight back to the C kernel,
#: so it cannot serve as the reference).  The only disagreement is the order in
#: which the per-block min distances are summed for sigma2 -- pocketfft-free,
#: plain additions whose order np.mean fixes; both are exact selections.  The
#: tolerance is therefore tight (1e-9) but not bitwise.
_ATOL = 1e-9


@pytest.mark.parametrize("name", ["QPSK", "16QAM", "64QAM", "256QAM"])
@pytest.mark.parametrize("rate", [2, 11])
def test_matches_numpy_reference(name, rate):
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS[name]
    rng = np.random.default_rng(hash((name, rate)) & 0xFFFF)
    cells = (rng.standard_normal(500)
             + 1j * rng.standard_normal(500)).astype(np.complex128)

    ref = nuc._demap_llr_numpy(cells, mod_order, rate)
    got = demap_llr(cells, np.asarray(nuc.points(mod_order, rate)), mod_order)
    assert got.shape == ref.shape
    assert np.allclose(got, ref, rtol=0, atol=_ATOL)


@pytest.mark.parametrize("name", ["64QAM", "256QAM"])
def test_matches_numpy_reference_large_block(name):
    """The fused single-pass path over a full FEC block (A1 of the demapper)."""
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS[name]
    rate, ncell = 11, 8100
    pts = nuc.points(mod_order, rate)
    rng = np.random.default_rng(1234 + mod_order)
    cells = (rng.standard_normal(ncell) + 1j * rng.standard_normal(ncell)) \
        .astype(np.complex128)
    ref = nuc._demap_llr_numpy(cells, mod_order, rate)
    got = demap_llr(cells, pts, mod_order)
    assert np.allclose(got, ref, rtol=0, atol=_ATOL)


@pytest.mark.parametrize("name,rate", [("64QAM", 11), ("256QAM", 11)])
def test_block_scale_matches_separate_calls(name, rate):
    """``block=N`` over concatenated blocks == one call per block exactly."""
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS[name]
    pts = nuc.points(mod_order, rate)
    ncell, nblk = 600, 3
    rng = np.random.default_rng(99)
    cells = (rng.standard_normal(ncell * nblk)
             + 1j * rng.standard_normal(ncell * nblk)).astype(np.complex128)

    batched = demap_llr(cells, pts, mod_order, block=ncell)
    separate = np.concatenate([
        demap_llr(cells[j * ncell:(j + 1) * ncell], pts, mod_order)
        for j in range(nblk)])
    assert np.array_equal(batched, separate)


def test_block_scale_ragged_tail():
    """The final, short block gets its own scale (not a whole-array one)."""
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS["16QAM"]
    pts = nuc.points(mod_order, 11)
    rng = np.random.default_rng(3)
    cells = (rng.standard_normal(101) + 1j * rng.standard_normal(101)) \
        .astype(np.complex128)
    batched = demap_llr(cells, pts, mod_order, block=40)
    separate = np.concatenate([
        demap_llr(cells[a:b], pts, mod_order) for a, b in ((0, 40), (40, 80),
                                                           (80, 101))])
    assert np.array_equal(batched, separate)


def test_matches_on_actual_constellation_points():
    """On exact constellation points the LLRs match to relative precision.

    Here sigma2 hits its 1e-9 floor (the min distance is 0), so LLRs are ~1e8
    and a relative comparison is the only meaningful one.
    """
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS["64QAM"]
    pts = nuc.points(mod_order, 11)
    cells = pts.copy()
    ref = nuc._demap_llr_numpy(cells, mod_order, 11)
    got = demap_llr(cells, pts, mod_order)
    assert np.allclose(got, ref, rtol=1e-9, atol=1e-3)

