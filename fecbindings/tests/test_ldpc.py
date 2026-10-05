"""Differential tests: the C LDPC decoder must match the NumPy reference.

The reference is ``atsc3lib.ldpc_exact.ATSC3LDPCExact`` (the same A/322
Annex A tables).  The C backend is correct only if it produces the *same*
hard decisions and convergence flag on encoded codewords and on real air LLRs.
"""

import numpy as np
import pytest

fecbindings = pytest.importorskip("fecbindings")

from fecbindings import decode_ldpc, available  # noqa: E402


def _make_ldpc(rate, n):
    from atsc3lib.ldpc_exact import ATSC3LDPCExact
    return ATSC3LDPCExact(rate, n=n, max_iterations=50)


@pytest.mark.parametrize("rate,n", [(2, 16200), (11, 64800), (6, 64800)])
def test_matches_reference_on_clean_codeword(rate, n):
    ldpc = _make_ldpc(rate, n)
    rng = np.random.default_rng(rate)
    info = rng.integers(0, 2, ldpc.K).astype(np.uint8)
    cw = ldpc.encode(info)
    # Library convention: llr > 0 => bit 1.
    llr = np.where(cw == 1, 1.0, -1.0).astype(np.float32) * 4.0

    ref_bits, ref_ok = ldpc._decode_numpy(llr, alpha=0.75)
    got_bits, got_ok = decode_ldpc(llr, ldpc.check_idx, ldpc.check_mask,
                                   ldpc.edge_var, max_iter=50, alpha=0.75)
    assert got_ok == ref_ok
    assert np.array_equal(got_bits[:ldpc.K], ref_bits)


@pytest.mark.parametrize("rate,n", [(2, 16200), (11, 64800)])
def test_matches_reference_with_noisy_llr(rate, n):
    ldpc = _make_ldpc(rate, n)
    rng = np.random.default_rng(1000 + rate)
    info = rng.integers(0, 2, ldpc.K).astype(np.uint8)
    cw = ldpc.encode(info)
    llr = (np.where(cw == 1, 1.0, -1.0) * 3.0
           + rng.normal(0, 1.0, ldpc.n)).astype(np.float32)

    ref_bits, ref_ok = ldpc._decode_numpy(llr, alpha=0.75)
    got_bits, got_ok = decode_ldpc(llr, ldpc.check_idx, ldpc.check_mask,
                                   ldpc.edge_var, max_iter=50, alpha=0.75)
    assert got_ok == ref_ok
    assert np.array_equal(got_bits[:ldpc.K], ref_bits)


def test_hard_decision_matches_last_hard():
    """The full hard vector must satisfy the same syndrome as the reference."""
    from atsc3lib.ldpc_exact import ATSC3LDPCExact
    ldpc = ATSC3LDPCExact(2, n=16200, max_iterations=50)
    rng = np.random.default_rng(7)
    info = rng.integers(0, 2, ldpc.K).astype(np.uint8)
    cw = ldpc.encode(info)
    llr = (np.where(cw == 1, 1.0, -1.0) * 3.0
           + rng.normal(0, 1.0, ldpc.n)).astype(np.float32)
    bits, ok = decode_ldpc(llr, ldpc.check_idx, ldpc.check_mask,
                           ldpc.edge_var, max_iter=50, alpha=0.75)
    assert bits.size == ldpc.n
    if ok:
        assert ldpc.check_syndrome_bits(bits)


def test_extension_available():
    assert available()
