"""Optional CUDA accelerators for the ATSC 3.0 receive chain.

The compiled C kernels in :mod:`atsc3lib._bindings` remain the required path.
:mod:`gpubindings` is a monorepo workspace member but an *optional* install:
without the CUDA toolkit the build ships only its Python wrappers and
``available()`` is False, so a machine with no GPU still runs the CPU path
unchanged.  Select it with ``uv sync --package atsc3lib --extra gpu`` (or
``make install-gpu``).

Selection follows ``ATSC3LIB_GPU``:

* ``auto`` (default) -- use the GPU when ``gpubindings`` imports and reports a
  CUDA device.
* ``off`` / ``0`` / ``cpu`` -- never import ``gpubindings``.
* ``on`` / ``1`` / ``gpu`` -- require it; :func:`require` raises when absent.

The accelerated kernels are differentially gated against the C kernels in
``gpubindings/tests`` and against the RF33 air fixture in ``atsc3lib/tests``,
so enabling the GPU cannot change decoded output.
"""

import os
from functools import lru_cache

import numpy as np

#: Environment variable selecting the backend.
GPU_ENV = "ATSC3LIB_GPU"

_OFF = ("off", "0", "false", "no", "cpu")
_ON = ("on", "1", "true", "yes", "gpu")


def mode() -> str:
    """The requested backend: ``auto``, ``on`` or ``off``."""
    raw = os.environ.get(GPU_ENV, "auto").strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _ON:
        return "on"
    return "auto"


@lru_cache(maxsize=8)
def _probe(requested: str) -> bool:
    if requested == "off":
        return False
    try:
        from gpubindings import demap, ldpc
    except ImportError:
        return False
    return bool(demap.available() and ldpc.available()
                and ldpc.device_count() > 0)


def available() -> bool:
    """True when a usable GPU backend is selected and importable."""
    return _probe(mode())


def require() -> None:
    """Raise when the GPU was explicitly requested but is not usable."""
    if mode() == "on" and not available():
        raise RuntimeError(
            "ATSC3LIB_GPU=on but gpubindings is not importable or no CUDA "
            "device is visible; build it with `make install-gpu` (see "
            "meta/HARDWARE-live-rig.md)"
        )


def demap_llr(cells: np.ndarray, points: np.ndarray, mod_order: int,
              block: int = None) -> np.ndarray:
    """Drop-in for :func:`atsc3lib.nuc.demap_llr` on the GPU."""
    require()
    from gpubindings.demap import demap_llr as _gpu_demap

    if block is None:
        return _gpu_demap(cells, points, mod_order)
    return _gpu_demap(cells, points, mod_order, block=block)


def ldpc_decoder(check_idx: np.ndarray, check_mask: np.ndarray, n: int,
                 edge_var: np.ndarray = None):
    """A resident GPU Tanner graph, or None when the GPU is unavailable.

    The handle is single-stream and not thread-safe; callers that batch decode
    use it from one thread (the batched receive path does).
    """
    require()
    if not available():
        return None
    from gpubindings.ldpc import LDPCDecoder

    return LDPCDecoder(check_idx, check_mask, n, edge_var=edge_var)
