"""Differential tests: the C demapper must match the NumPy reference exactly."""

import numpy as np
import pytest

demodbindings = pytest.importorskip("demodbindings")

from demodbindings import demap_llr  # noqa: E402


@pytest.mark.parametrize("name", ["QPSK", "16QAM", "64QAM", "256QAM"])
@pytest.mark.parametrize("rate", [2, 11])
def test_matches_numpy_reference(name, rate):
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS[name]
    pts = nuc.points(mod_order, rate)
    rng = np.random.default_rng(hash((name, rate)) & 0xFFFF)
    cells = (rng.standard_normal(500)
             + 1j * rng.standard_normal(500)).astype(np.complex128)

    ref = nuc.demap_llr(cells, mod_order, rate)
    got = demap_llr(cells, pts, mod_order)
    assert got.shape == ref.shape
    assert np.allclose(got, ref, rtol=0, atol=1e-9)


def test_matches_on_actual_constellation_points():
    """On exact constellation points the LLRs match to relative precision.

    Here sigma2 hits its 1e-9 floor (the min distance is 0), so LLRs are ~1e8
    and a relative comparison is the only meaningful one.
    """
    from atsc3lib import nuc
    mod_order = nuc.MODULATION_BITS["64QAM"]
    pts = nuc.points(mod_order, 11)
    cells = pts.copy()
    ref = nuc.demap_llr(cells, mod_order, 11)
    got = demap_llr(cells, pts, mod_order)
    assert np.allclose(got, ref, rtol=1e-9, atol=1e-3)
