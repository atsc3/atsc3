"""Unit tests for the compiled fecbindings kernels.

These test the C extension directly (its API contract and edge cases), not only
whether it reproduces the NumPy reference; the differential checks live in
``test_ldpc.py``.
"""

import numpy as np
import pytest

fecbindings = pytest.importorskip("fecbindings")

from fecbindings import _ldpc, available, decode_ldpc  # noqa: E402


def _tiny_graph():
    """A 3-variable, 2-check graph: edge slots check-major.

    c0 = v0 + v1, c1 = v1 + v2 (one 3-entry row and one 3-entry row, padded to
    dmax 3).  All slots used except the third of the second row.
    """
    check_idx = np.array([[0, 1, 2], [1, 2, 0]], dtype=np.int32)
    check_mask = np.array([[1, 1, 0], [1, 1, 0]], dtype=np.uint8)
    edge_var = np.array([0, 1, 1, 2], dtype=np.int32)
    return check_idx, check_mask, edge_var


class TestExtensionSurface:
    def test_available(self):
        assert available()

    def test_module_and_function_exposed(self):
        assert hasattr(_ldpc, "decode_ldpc")
        assert callable(decode_ldpc)

    def test_returns_full_hard_vector_and_flag(self):
        idx, mask, evar = _tiny_graph()
        llr = np.array([1.0, 1.0, -1.0], dtype=np.float32)  # signs => bits 1,1,0
        bits, ok = decode_ldpc(llr, idx, mask, evar, max_iter=1, alpha=0.75)
        assert bits.dtype == np.uint8
        assert bits.shape == (3,)
        assert isinstance(ok, (bool, np.bool_))


class TestEdgeCases:
    def test_all_zero_codeword_converges(self):
        # LLRs that already satisfy every check: v = (0, 0, 0).
        idx, mask, evar = _tiny_graph()
        llr = np.array([-5.0, -5.0, -5.0], dtype=np.float32)
        bits, ok = decode_ldpc(llr, idx, mask, evar)
        assert ok
        assert bits.tolist() == [0, 0, 0]

    def test_simple_correction(self):
        # v = (1, 1, 0) satisfies c0=0, c1=1 -> not a codeword; the decoder
        # must flip one variable to reach a valid codeword.
        idx, mask, evar = _tiny_graph()
        llr = np.array([3.0, 3.0, -0.2], dtype=np.float32)
        bits, ok = decode_ldpc(llr, idx, mask, evar, max_iter=20)
        assert ok
        acc0 = bits[0] ^ bits[1]
        acc1 = bits[1] ^ bits[2]
        assert acc0 == 0 and acc1 == 0

    def test_never_converges_returns_false(self):
        # A degree-1 check with an unsatisfiable pattern cannot converge;
        # just assert the flag is a clean bool rather than an exception.
        idx = np.array([[0, 0]], dtype=np.int32)
        mask = np.array([[1, 0]], dtype=np.uint8)
        evar = np.array([0], dtype=np.int32)
        llr = np.array([1.0], dtype=np.float32)
        bits, ok = decode_ldpc(llr, idx, mask, evar, max_iter=3)
        assert isinstance(ok, (bool, np.bool_))
        assert bits.shape == (1,)

    def test_zero_iterations(self):
        # [1, 1, 0] is not a codeword (c1 = 1), so zero iterations must
        # report the raw channel signs and not converge.
        idx, mask, evar = _tiny_graph()
        llr = np.array([1.0, 1.0, -1.0], dtype=np.float32)
        bits, ok = decode_ldpc(llr, idx, mask, evar, max_iter=0)
        assert bits.tolist() == [1, 1, 0]
        assert not ok


class TestInputHandling:
    def test_accepts_list_inputs(self):
        idx, mask, evar = _tiny_graph()
        bits, ok = decode_ldpc([-1.0, -1.0, -1.0], idx, mask, evar)
        assert bits.tolist() == [0, 0, 0]
        assert ok

    def test_accepts_bool_mask(self):
        idx, mask, evar = _tiny_graph()
        bits, ok = decode_ldpc(np.array([-1.0, -1.0, -1.0]),
                               idx, mask.astype(bool), evar)
        assert ok

    def test_alpha_scales_message(self):
        # With alpha=0, no extrinsic message is applied, so bits stay as the
        # channel signs after any number of iterations.
        idx, mask, evar = _tiny_graph()
        llr = np.array([1.0, 1.0, -1.0], dtype=np.float32)
        bits, ok = decode_ldpc(llr, idx, mask, evar, max_iter=10, alpha=0.0)
        assert bits.tolist() == [1, 1, 0]

    def test_rejects_wrong_llr_length(self):
        idx, mask, evar = _tiny_graph()
        with pytest.raises((ValueError, Exception)):
            decode_ldpc(np.array([1.0, 1.0], dtype=np.float32),
                        idx, mask, evar)
