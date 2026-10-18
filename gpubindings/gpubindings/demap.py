"""CUDA max-log demapper (``gpubindings.demap``).

Drop-in for ``atsc3lib._bindings.demod.demap_llr``: identical arguments,
identical output, A/322 6.3.3 max-log LLRs in q-stream order with
``llr > 0`` meaning bit 0.
"""

import numpy as np

try:
    from . import _gpu_demap
except ImportError:
    _gpu_demap = None

__all__ = ["demap_llr", "available"]


def available() -> bool:
    return _gpu_demap is not None


def demap_llr(cells, points, mod_order, block=None):
    """Max-log LLRs for ``cells`` against ``points`` on the GPU (A/322 6.3.3).

    Args:
        cells: complex samples (any shape; flattened).
        points: constellation of ``2**mod_order`` points, index = label (y0 MSB).
        mod_order: bits per symbol.
        block: when given, the ``sigma2`` scale is recomputed every ``block``
            cells instead of once over the whole array, matching the C kernel.

    Returns:
        ``float64`` array of length ``len(cells) * mod_order``.
    """
    if _gpu_demap is None:
        raise RuntimeError(
            "gpubindings.demap is not built; install it with "
            "`make install-gpu` on a host with the CUDA toolkit"
        )
    z = np.ascontiguousarray(cells, dtype=np.complex128).ravel()
    pts = np.ascontiguousarray(points, dtype=np.complex128).ravel()
    if block is None:
        return _gpu_demap.demap_llr(z, pts, int(mod_order))
    return _gpu_demap.demap_llr(z, pts, int(mod_order), int(block))
