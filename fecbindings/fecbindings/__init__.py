"""fecbindings: compiled FEC kernels for the ATSC 3.0 receive chain.

The pure-Python/NumPy LDPC decoder dominates a PLP-0 frame (~3.9 s of ~5 s for
78 codewords), so it lives here as a C extension.  ``decode_ldpc`` implements
exactly the normalized min-sum belief propagation of
``atsc3lib.ldpc_exact.ATSC3LDPCExact.decode`` — same ordering, same float32
arithmetic — so callers can swap it in without changing results.

The Tanner graph is described by three arrays the caller precomputes:
``check_idx`` (n_checks x dmax variable columns), ``check_mask`` (valid slots)
and ``edge_var`` (variable per edge, check-major).  This keeps the binding a
pure numerical kernel: all A/322 table logic stays in ``atsc3lib``.
"""

import numpy as np

from . import _bch
from . import _ldpc

__all__ = ["decode_ldpc", "decode_bch", "available"]

__version__ = "0.1.0"


def available() -> bool:
    """True when the compiled extension is importable."""
    return _ldpc is not None and _bch is not None


def decode_bch(rx, mouter, t, exp, log, order, kpayload):
    """BCH-decode a shortened codeword (A/322 6.1.2.1).

    Args:
        rx: ``n`` received bits (MSB first, 0/1).
        mouter: BCH parity length.
        t: correctable errors (12 for ATSC 3.0).
        exp, log: GF(2^m) tables from ``atsc3lib.bch._GF``.
        order: ``2^m - 1``.
        kpayload: information-bit count.

    Returns:
        ``(corrected_message_bits uint8[kpayload], nerr int, ok bool)``.
    """
    return _bch.decode_bits(
        np.ascontiguousarray(rx, dtype=np.uint8), int(mouter), int(t),
        np.ascontiguousarray(exp, dtype=np.int32),
        np.ascontiguousarray(log, dtype=np.int32), int(order),
        int(kpayload))


def decode_ldpc(channel_llr, check_idx, check_mask, edge_var,
                max_iter=100, alpha=0.75):
    """Decode one codeword with normalized min-sum (A/322 6.1.3).

    Args:
        channel_llr: ``n`` channel LLRs in the library convention
            (``llr > 0`` => bit 1).  Negated internally to the L' domain.
        check_idx: ``(n_checks, dmax)`` int32 variable column per edge slot.
        check_mask: ``(n_checks, dmax)`` bool/uint8, 1 on a real slot.
        edge_var: ``(n_edges,)`` int32 variable per edge, check-major order.
        max_iter: belief-propagation iteration cap.
        alpha: normalized min-sum scaling (0 < alpha <= 1).

    Returns:
        ``(hard_bits uint8[n], converged bool)`` — the caller slices ``[:K]``.
    """
    channel = -np.ascontiguousarray(channel_llr, dtype=np.float32)
    idx = np.ascontiguousarray(check_idx, dtype=np.int32)
    mask = np.ascontiguousarray(check_mask, dtype=np.uint8)
    evar = np.ascontiguousarray(edge_var, dtype=np.int32)
    return _ldpc.decode_ldpc(channel, idx, mask, evar,
                             int(max_iter), float(alpha))


def decode_ldpc_exact(ldpc, llrs, max_iter=None, alpha=0.75):
    """Run :func:`decode_ldpc` using a prebuilt ``ATSC3LDPCExact`` instance.

    The instance supplies the packed Tanner-graph arrays (built once) and the
    default iteration cap; this mirrors the ``ATSC3LDPCExact.decode`` signature
    so the receive chain can use either backend (same return shape).
    """
    iters = max_iter if max_iter is not None else ldpc.max_iterations
    return decode_ldpc(llrs, ldpc.check_idx, ldpc.check_mask, ldpc.edge_var,
                       max_iter=iters, alpha=alpha)
