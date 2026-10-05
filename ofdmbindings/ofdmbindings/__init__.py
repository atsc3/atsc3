"""ofdmbindings: compiled OFDM kernels for the ATSC 3.0 receiver.

Currently the frequency interleaver address generator (A/322 7.3), a scalar
LFSR recurrence that NumPy cannot vectorise and that costs ~25 ms per OFDM
symbol in pure Python.
"""

import numpy as np

from . import _fi

__all__ = ["generate_addresses", "available"]

__version__ = "0.1.0"


def available() -> bool:
    return _fi is not None


def generate_addresses(symbol_index, n_data, pn_degree, pn_mask, max_states,
                       logic, logic2, bitperm, bitperm_odd):
    """Frequency-interleaver addresses H_l(p) (A/322 7.3).

    Parameters are the per-FFT-size constants from
    ``atsc3lib.frequency_interleaver._PARAMS``; the recurrence matches
    ``atsc3lib.frequency_interleaver.generate_addresses`` exactly.
    """
    return _fi.generate_addresses(
        int(symbol_index), int(n_data), int(pn_degree), int(pn_mask),
        int(max_states),
        np.ascontiguousarray(logic, dtype=np.int32),
        np.ascontiguousarray(logic2, dtype=np.int32),
        np.ascontiguousarray(bitperm, dtype=np.int32),
        np.ascontiguousarray(bitperm_odd, dtype=np.int32))
