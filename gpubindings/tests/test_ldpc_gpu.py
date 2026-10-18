"""Differential tests: the CUDA LDPC decoder must match the C kernel exactly.

The gate is stricter than the demapper's: LDPC output is hard bits, so the CUDA
kernel has to reproduce the reference's *decisions*, not just get close.  The
reference accumulates each variable's edge messages in float64 in
check-ascending order and rounds once to float32; ``gpubindings.ldpc`` rebuilds
the adjacency in that same order, so bits and the ``converged`` flag are
asserted equal with no tolerance at all.

Covers three regimes because they exercise different code paths:
  * a clean codeword -- the initial syndrome passes, zero iterations run
  * a noisy but decodable channel -- converges partway through the loop
  * a garbage channel -- exhausts ``max_iter`` and both must agree anyway
"""

import numpy as np
import pytest

ldpc_mod = pytest.importorskip("gpubindings.ldpc")

if not ldpc_mod.available():
    pytest.skip("gpubindings._gpu_ldpc not built", allow_module_level=True)

from gpubindings.ldpc import LDPCDecoder  # noqa: E402

from atsc3lib._bindings.fec import decode_ldpc as c_decode  # noqa: E402
from atsc3lib.ldpc_exact import ATSC3LDPCExact  # noqa: E402

_CASES = [(11, 64800), (2, 64800), (11, 16200), (2, 16200)]


def _codec(rate, n):
    return ATSC3LDPCExact(rate=rate, n=n)


def _clean_channel(codec, rng):
    info = rng.integers(0, 2, codec.K, dtype=np.uint8)
    cw = np.asarray(codec.encode(info), dtype=np.uint8)
    # Library LLR convention (llr > 0 => bit 1), which is what
    # fec.decode_ldpc takes; both backends negate internally.
    return ((2.0 * cw - 1.0) * 4.0).astype(np.float32), cw


@pytest.mark.parametrize("rate,n", _CASES)
def test_clean_codeword_zero_iterations(rate, n):
    codec = _codec(rate, n)
    rng = np.random.default_rng(rate * 1000 + n)
    chan, cw = _clean_channel(codec, rng)
    dec = LDPCDecoder(codec.check_idx, codec.check_mask, codec.n, codec.edge_var)

    h_c, conv_c = c_decode(chan, codec.check_idx, codec.check_mask,
                           codec.edge_var, 50, 0.75)
    h_g, conv_g = dec.decode(chan, 50, 0.75)
    assert bool(conv_c) and bool(conv_g)
    assert np.array_equal(h_c, h_g)
    assert np.array_equal(h_g, cw)


@pytest.mark.parametrize("rate,n", _CASES)
def test_noisy_codeword_converges_identically(rate, n):
    codec = _codec(rate, n)
    rng = np.random.default_rng(rate * 7 + n)
    chan, cw = _clean_channel(codec, rng)
    noisy = (chan + rng.normal(0.0, 2.0, chan.size)).astype(np.float32)
    dec = LDPCDecoder(codec.check_idx, codec.check_mask, codec.n, codec.edge_var)

    h_c, conv_c = c_decode(noisy, codec.check_idx, codec.check_mask,
                           codec.edge_var, 50, 0.75)
    h_g, conv_g = dec.decode(noisy, 50, 0.75)
    assert bool(conv_c) == bool(conv_g)
    assert np.array_equal(h_c, h_g)


@pytest.mark.parametrize("rate,n", [(11, 64800), (2, 16200)])
def test_non_converging_channel_agrees(rate, n):
    """Exhausting max_iter must still produce identical decisions."""
    codec = _codec(rate, n)
    rng = np.random.default_rng(99 + rate + n)
    chan = rng.normal(0.0, 0.05, codec.n).astype(np.float32)
    dec = LDPCDecoder(codec.check_idx, codec.check_mask, codec.n, codec.edge_var)

    h_c, conv_c = c_decode(chan, codec.check_idx, codec.check_mask,
                           codec.edge_var, 12, 0.75)
    h_g, conv_g = dec.decode(chan, 12, 0.75)
    assert bool(conv_c) == bool(conv_g)
    assert np.array_equal(h_c, h_g)


@pytest.mark.parametrize("rate,n", [(11, 64800), (2, 16200)])
def test_decode_many_matches_c(rate, n):
    codec = _codec(rate, n)
    rng = np.random.default_rng(rate * 31 + n)
    base, _cw = _clean_channel(codec, rng)
    batch = np.stack([
        (base + rng.normal(0.0, 2.0, base.size)).astype(np.float32)
        for _ in range(4)])
    dec = LDPCDecoder(codec.check_idx, codec.check_mask, codec.n,
                      codec.edge_var)
    hard, conv, unsat = dec.decode_many(batch, 50, 0.75)
    for i in range(batch.shape[0]):
        h_c, conv_c = c_decode(batch[i], codec.check_idx, codec.check_mask,
                               codec.edge_var, 50, 0.75)
        assert np.array_equal(hard[i], h_c)
        assert bool(conv[i]) == bool(conv_c)
        assert int(unsat[i]) == int(codec._syndrome_unsatisfied(h_c).sum())


def test_channel_length_validated():
    codec = _codec(11, 16200)
    dec = LDPCDecoder(codec.check_idx, codec.check_mask, codec.n, codec.edge_var)
    with pytest.raises(ValueError):
        dec.decode(np.zeros(codec.n - 1, dtype=np.float32), 5, 0.75)
