"""Unit tests for the compiled demodbindings kernel (API contract/edge cases)."""

import numpy as np
import pytest

demodbindings = pytest.importorskip("demodbindings")

from demodbindings import _demap, available, demap_llr  # noqa: E402


class TestSurface:
    def test_available(self):
        assert available()

    def test_exposed(self):
        assert hasattr(_demap, "demap_llr")


class TestEdgeCases:
    def test_empty_cells(self):
        pts = np.array([1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]) / np.sqrt(2)
        out = demap_llr(np.array([], dtype=np.complex128), pts, 2)
        assert out.shape == (0,)

    def test_length_and_dtype(self):
        pts = np.array([1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]) / np.sqrt(2)
        cells = np.zeros(7, dtype=np.complex128)
        out = demap_llr(cells, pts, 2)
        assert out.dtype == np.float64
        assert out.shape == (14,)

    def test_rejects_wrong_point_count(self):
        pts = np.array([1 + 1j, -1 + 1j], dtype=np.complex128)
        with pytest.raises(ValueError):
            demap_llr(np.zeros(4, dtype=np.complex128), pts, 2)

    def test_accepts_list_inputs(self):
        pts = [1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]
        out = demap_llr([0.9 + 0.9j, -0.9 + 0.9j], pts, 2)
        assert out.shape == (4,)

    def test_sigma2_floor_on_noiseless_point(self):
        # A cell exactly on a constellation point: d2.min == 0 would divide by
        # zero, so the 1e-9 floor must apply and produce finite LLRs.
        pts = np.array([1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]) / np.sqrt(2)
        out = demap_llr(pts[:1], pts, 2)
        assert np.all(np.isfinite(out))

    def test_llr_sign_convention(self):
        # A point labelled 0 (index 0 => all bits 0) must give llr > 0 for
        # every bit level (llr > 0 => bit 0).
        pts = np.array([1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]) / np.sqrt(2)
        out = demap_llr(pts[:1], pts, 2)
        assert out[0] > 0


class TestQpskKnown:
    def test_antipodal_llr(self):
        # Two points on the real axis (1 bit): label 0 at +0.5, label 1 at
        # -0.5.  A cell at +0.5 is closest to the bit-0 point, so
        # llr = (min1 - min0)/sigma2 = (1 - 0)/sigma2 > 0 (llr > 0 => bit 0).
        pts = np.array([0.5 + 0j, -0.5 + 0j], dtype=np.complex128)
        out = demap_llr(np.array([0.5 + 0j]), pts, 1)
        assert out[0] > 0
        # The mirror cell at -0.5 flips the sign.
        out2 = demap_llr(np.array([-0.5 + 0j]), pts, 1)
        assert out2[0] < 0
