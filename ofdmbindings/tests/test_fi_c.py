"""Unit tests for the compiled ofdmbindings frequency interleaver (API/edge cases)."""

import numpy as np
import pytest

ofdmbindings = pytest.importorskip("ofdmbindings")

from ofdmbindings import _fi, available, generate_addresses  # noqa: E402


def _p(fft=8192):
    from atsc3lib.frequency_interleaver import _PARAMS
    return _PARAMS[fft]


class TestSurface:
    def test_available(self):
        assert available()

    def test_exposed(self):
        assert hasattr(_fi, "generate_addresses")


class TestEdgeCases:
    def test_returns_int64_of_length(self):
        p = _p(8192)
        out = generate_addresses(0, 100, p.pn_degree, p.pn_mask, p.max_states,
                                  p.logic, p.logic2, p.bitperm, p.bitperm_odd)
        assert out.dtype == np.int64
        assert out.shape == (100,)

    def test_values_in_range(self):
        p = _p(8192)
        n = 4096
        out = generate_addresses(5, n, p.pn_degree, p.pn_mask, p.max_states,
                                  p.logic, p.logic2, p.bitperm, p.bitperm_odd)
        assert out.min() >= 0
        assert out.max() < n

    def test_deterministic(self):
        p = _p(16384)
        a = generate_addresses(9, 2000, p.pn_degree, p.pn_mask, p.max_states,
                                p.logic, p.logic2, p.bitperm, p.bitperm_odd)
        b = generate_addresses(9, 2000, p.pn_degree, p.pn_mask, p.max_states,
                                p.logic, p.logic2, p.bitperm, p.bitperm_odd)
        assert np.array_equal(a, b)

    def test_accepts_lists(self):
        p = _p(8192)
        out = generate_addresses(1, 50, p.pn_degree, p.pn_mask, p.max_states,
                                  list(p.logic), list(p.logic2),
                                  list(p.bitperm), list(p.bitperm_odd))
        assert out.shape == (50,)

    def test_rejects_bad_pn_degree(self):
        p = _p(8192)
        with pytest.raises(ValueError):
            generate_addresses(0, 10, 0, p.pn_mask, p.max_states,
                                p.logic, p.logic2, p.bitperm, p.bitperm_odd)

    def test_rejects_impossible_n_data(self):
        # Asking for more addresses than the LFSR can produce must raise
        # rather than silently under-fill.
        p = _p(8192)
        with pytest.raises(RuntimeError):
            generate_addresses(0, p.max_states + 1, p.pn_degree, p.pn_mask,
                                p.max_states, p.logic, p.logic2, p.bitperm,
                                p.bitperm_odd)


class TestRoundTrip:
    def test_interleave_then_deinterleave_identity(self):
        from atsc3lib import frequency_interleaver as fi
        rng = np.random.default_rng(0)
        cells = rng.standard_normal(3000).astype(np.complex64)
        inter = fi.interleave(cells, 4, 8192)
        back = fi.deinterleave(inter, 4, 8192)
        assert np.allclose(back, cells)
