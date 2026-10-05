"""Differential tests: the C frequency interleaver must match the Python reference."""

import numpy as np
import pytest

_ofdm = pytest.importorskip("atsc3lib._bindings.ofdm")

from atsc3lib._bindings.ofdm import generate_addresses  # noqa: E402


def _params(fft_size):
    from atsc3lib.frequency_interleaver import _PARAMS
    return _PARAMS[fft_size]


@pytest.mark.parametrize("fft_size", [8192, 16384, 32768])
@pytest.mark.parametrize("symbol_index", [0, 1, 2, 7, 64, 513])
def test_matches_python_reference(fft_size, symbol_index):
    from atsc3lib.frequency_interleaver import _generate_addresses_numpy
    p = _params(fft_size)
    n_data = min(6900, fft_size)
    ref = _generate_addresses_numpy(symbol_index, fft_size, n_data)
    got = generate_addresses(symbol_index, n_data, p.pn_degree, p.pn_mask,
                             p.max_states, p.logic, p.logic2, p.bitperm,
                             p.bitperm_odd)
    assert np.array_equal(got, ref)
    assert got.dtype == np.int64


def test_full_length_sequence():
    from atsc3lib.frequency_interleaver import _generate_addresses_numpy
    p = _params(8192)
    ref = _generate_addresses_numpy(3, 8192, 8192)
    got = generate_addresses(3, 8192, p.pn_degree, p.pn_mask, p.max_states,
                              p.logic, p.logic2, p.bitperm, p.bitperm_odd)
    assert np.array_equal(got, ref)
