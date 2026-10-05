"""demodbindings: compiled soft-demodulation kernels for the ATSC 3.0 receiver.

The max-log NUC/QAM demapper is O(cells x 2**mod_order); in NumPy it forms a
full distance matrix and dominates high-order frames (256QAM ~20 s/frame for
PLP-1).  ``demap_llr`` computes identical LLRs in one pass with no large
temporaries, so it is a drop-in for ``atsc3lib.nuc.demap_llr``.
"""

import numpy as np

from . import _demap

__all__ = ["demap_llr", "available"]

__version__ = "0.1.0"


def available() -> bool:
    return _demap is not None


def demap_llr(cells, points, mod_order):
    """Max-log LLRs for ``cells`` against ``points`` (A/322 6.3.3).

    Args:
        cells: complex samples (any shape; flattened).
        points: constellation of ``2**mod_order`` points, index = label (y0 MSB).
        mod_order: bits per symbol.

    Returns:
        ``float64`` array of length ``len(cells) * mod_order``, q-stream order,
        ``llr > 0`` => bit 0 (same convention as ``atsc3lib.nuc.demap_llr``).
    """
    z = np.ascontiguousarray(cells, dtype=np.complex128).ravel()
    pts = np.ascontiguousarray(points, dtype=np.complex128).ravel()
    return _demap.demap_llr(z, pts, int(mod_order))
