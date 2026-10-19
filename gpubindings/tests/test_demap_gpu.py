"""Differential tests: the CUDA demapper must match the NumPy reference.

Same gate and the same tolerance as the C kernel's differential
(``tests/bindings/test_demod_demap_ref.py``): the only legitimate disagreement
is the order the per-block min distances are summed for ``sigma2``.  The GPU
kernel sums them on the host in ascending cell order -- the same order the C
kernel uses -- so it is in practice bit-identical; the tolerance stays at 1e-9
rather than asserting bitwise, so a future reordering of that reduction cannot
pass silently.
"""

import numpy as np
import pytest

demap_mod = pytest.importorskip("gpubindings.demap")

if not demap_mod.available():
    pytest.skip("gpubindings._gpu_demap not built", allow_module_level=True)

from gpubindings.demap import demap_llr  # noqa: E402

_ATOL = 1e-9


def _cells(n, seed):
    """Deterministic complex normal cells (hash() is salted per process)."""
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(
        np.complex128
    )


@pytest.mark.parametrize("name", ["QPSK", "16QAM", "64QAM", "256QAM", "4096QAM"])
@pytest.mark.parametrize("rate", [2, 11])
def test_matches_numpy_reference(name, rate):
    from atsc3lib import nuc

    if name not in nuc.MODULATION_BITS:
        pytest.skip(f"{name} not defined in nuc")
    mod_order = nuc.MODULATION_BITS[name]
    try:
        pts = np.asarray(nuc.points(mod_order, rate))
    except NotImplementedError:
        # MODULATION_BITS carries 4096QAM but nuc has no NUC table for it yet.
        pytest.skip(f"no NUC table for mod_order {mod_order}")
    cells = _cells(500, mod_order * 100 + rate)

    ref = nuc._demap_llr_numpy(cells, mod_order, rate)
    got = demap_llr(cells, pts, mod_order)
    assert got.shape == ref.shape
    assert np.allclose(got, ref, rtol=0, atol=_ATOL)


@pytest.mark.parametrize("name", ["64QAM", "256QAM"])
def test_matches_numpy_reference_full_fec_block(name):
    """The real per-block shape: 8100 cells at 11/15."""
    from atsc3lib import nuc

    mod_order = nuc.MODULATION_BITS[name]
    cells = _cells(8100, 1000 + mod_order)
    ref = nuc._demap_llr_numpy(cells, mod_order, 11)
    got = demap_llr(cells, np.asarray(nuc.points(mod_order, 11)), mod_order)
    assert np.allclose(got, ref, rtol=0, atol=_ATOL)


@pytest.mark.parametrize("name,rate", [("64QAM", 11), ("256QAM", 11)])
def test_block_scale_matches_separate_calls(name, rate):
    """One call with ``block=N`` equals separate per-block calls (C parity)."""
    from atsc3lib import nuc

    mod_order = nuc.MODULATION_BITS[name]
    pts = np.asarray(nuc.points(mod_order, rate))
    ncell = 400
    a = _cells(ncell, 11)
    b = _cells(ncell, 22)

    joined = demap_llr(np.concatenate([a, b]), pts, mod_order, block=ncell)
    sep = np.concatenate([demap_llr(a, pts, mod_order),
                          demap_llr(b, pts, mod_order)])
    assert np.allclose(joined, sep, rtol=0, atol=_ATOL)


@pytest.mark.parametrize("name", ["QPSK", "16QAM", "64QAM", "256QAM"])
def test_matches_c_kernel(name):
    """GPU against the shipped C kernel, not just the NumPy reference."""
    from atsc3lib import nuc
    from atsc3lib._bindings.demod import demap_llr as c_demap_llr

    mod_order = nuc.MODULATION_BITS[name]
    pts = np.asarray(nuc.points(mod_order, 11))
    cells = _cells(8100, 2000 + mod_order)

    assert np.allclose(demap_llr(cells, pts, mod_order),
                       c_demap_llr(cells, pts, mod_order),
                       rtol=0, atol=_ATOL)


def test_empty():
    out = demap_llr(np.zeros(0, dtype=np.complex128), np.zeros(4), 2)
    assert out.shape == (0,)
