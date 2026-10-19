"""CUDA normalized min-sum LDPC decoder (``gpubindings.ldpc``).

Drop-in for ``atsc3lib._bindings.fec.decode_ldpc`` / ``_gpu_ldpc`` in output:
same hard bits, same ``converged`` flag, at 11/15 and every other rate.

The Tanner graph is uploaded **once** through :class:`LDPCDecoder` and reused
for every FEC block of a frame (117 for 256QAM), because the graph is fixed for
the life of an L1 lock.  The one-shot :func:`decode_ldpc` helper rebuilds the
handle each call and is meant for tests, not the receive path.

The variable-node adjacency is rebuilt here in check-ascending order because
the reference accumulates each variable's edge messages in ``float64`` in that
exact order before rounding once to ``float32``.  Preserving the order is what
makes the result bit-identical rather than merely close.
"""

import numpy as np

try:
    from . import _gpu_ldpc
except ImportError:
    _gpu_ldpc = None

__all__ = ["LDPCDecoder", "decode_ldpc", "available"]


def available() -> bool:
    return _gpu_ldpc is not None


def _var_major(check_idx: np.ndarray, check_mask: np.ndarray, n: int):
    """Var-major adjacency in check-ascending order.

    Returns ``(var_start int32[n+1], var_m int32[n_edges])`` where ``var_m``
    holds indices into the check-major message array ``M[c * dmax + j]``.
    """
    idx = np.ascontiguousarray(check_idx, dtype=np.int32)
    msk = np.ascontiguousarray(check_mask, dtype=np.uint8)
    dmax = idx.shape[1]

    c, j = np.nonzero(msk)
    var = idx[c, j]
    if var.size and (var.min() < 0 or var.max() >= n):
        raise ValueError("check_idx column out of range for n")

    # Primary key var, then check, then slot: for a given variable this is the
    # order the C kernel adds its edge messages (c ascending, j ascending).
    order = np.lexsort((j, c, var))
    var_m = (c[order].astype(np.int64) * dmax + j[order]).astype(np.int32)
    counts = np.bincount(var[order], minlength=n)
    var_start = np.zeros(n + 1, dtype=np.int32)
    var_start[1:] = np.cumsum(counts).astype(np.int32)
    return np.ascontiguousarray(var_start), np.ascontiguousarray(var_m)


class LDPCDecoder:
    """A Tanner graph resident on the GPU.

    Args:
        check_idx: ``(n_checks, dmax)`` int32, variable column per edge slot.
        check_mask: ``(n_checks, dmax)`` uint8, 1 on a real slot.
        n: number of variable (code) bits.
        edge_var: accepted for call-compatibility with the C signature and
            ignored -- the graph is walked through ``check_idx``/``check_mask``
            exactly as the C kernel does.
    """

    def __init__(self, check_idx, check_mask, n, edge_var=None):
        if _gpu_ldpc is None:
            raise RuntimeError(
                "gpubindings.ldpc is not built; install it with "
                "`pip install ./gpubindings` on a host with the CUDA toolkit"
            )
        self.n = int(n)
        self.check_idx = np.ascontiguousarray(check_idx, dtype=np.int32)
        self.check_mask = np.ascontiguousarray(check_mask, dtype=np.uint8)
        self._var_start, self._var_m = _var_major(
            self.check_idx, self.check_mask, self.n
        )
        self._handle = _gpu_ldpc.create(
            self.check_idx, self.check_mask, self._var_start, self._var_m
        )

    def decode(self, channel, max_iter=50, alpha=0.75):
        """Decode ``channel`` in the library LLR convention.

        Args:
            channel: ``n`` channel LLRs where ``llr > 0`` => bit 1 (the same
                convention as ``atsc3lib._bindings.fec.decode_ldpc``).  It is
                negated internally to the L' domain the kernel works in.
            max_iter: belief-propagation iteration cap.
            alpha: normalized min-sum scaling (0 < alpha <= 1).

        Returns:
            ``(uint8[n] hard bits, converged: bool)``.
        """
        chan = -np.ascontiguousarray(channel, dtype=np.float32).ravel()
        if chan.size != self.n:
            raise ValueError(
                f"channel length {chan.size} != graph n {self.n}"
            )
        return _gpu_ldpc.decode(self._handle, chan, int(max_iter), float(alpha))

    def __del__(self):
        handle = getattr(self, "_handle", None)
        if handle is not None and _gpu_ldpc is not None:
            _gpu_ldpc.destroy(handle)
            self._handle = None


def decode_ldpc(channel, check_idx, check_mask, edge_var=None,
                max_iter=50, alpha=0.75):
    """One-shot decode; rebuilds the graph handle each call (tests only)."""
    chan = np.asarray(channel, dtype=np.float32).ravel()
    dec = LDPCDecoder(check_idx, check_mask, chan.size, edge_var=edge_var)
    try:
        return dec.decode(chan, max_iter=max_iter, alpha=alpha)
    finally:
        del dec
