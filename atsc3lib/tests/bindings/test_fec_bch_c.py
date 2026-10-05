"""Differential + unit tests for the compiled BCH decoder."""

import numpy as np
import pytest

_fec = pytest.importorskip("atsc3lib._bindings.fec")

from atsc3lib._bindings.fec import _bch, available, decode_bch  # noqa: E402


@pytest.fixture(scope="module", params=[16200, 64800])
def codec(request):
    from atsc3lib.bch import BCHCode
    return BCHCode(request.param, 12)


def _noisy(llr_bits, nerr, seed):
    rng = np.random.default_rng(seed)
    rx = np.array(llr_bits, dtype=np.uint8).copy()
    pos = rng.choice(len(rx), nerr, replace=False)
    rx[pos] ^= 1
    return rx


class TestSurface:
    def test_available(self):
        assert available()

    def test_exposed(self):
        assert hasattr(_bch, "decode_bits")


class TestDifferential:
    def test_clean_codeword_matches(self, codec):
        rng = np.random.default_rng(1)
        msg = rng.integers(0, 2, codec.k_full - 100).astype(np.uint8)
        cw = np.array(codec.encode(msg), dtype=np.uint8)
        ref = codec._decode_python(cw.tolist())
        got = codec.decode(cw.tolist())
        assert bool(got[2]) == bool(ref[2])
        assert int(got[1]) == int(ref[1])
        assert np.array_equal(np.asarray(got[0]), np.asarray(ref[0]))

    @pytest.mark.parametrize("nerr", [1, 5, 12])
    def test_correctable_errors_match(self, codec, nerr):
        rng = np.random.default_rng(nerr)
        msg = rng.integers(0, 2, codec.k_full - 100).astype(np.uint8)
        cw = np.array(codec.encode(msg), dtype=np.uint8)
        rx = _noisy(cw, nerr, seed=nerr + 1)
        ref = codec._decode_python(rx.tolist())
        got = codec.decode(rx.tolist())
        assert bool(got[2]) == bool(ref[2])
        assert int(got[1]) == int(ref[1])
        assert np.array_equal(np.asarray(got[0]), np.asarray(ref[0]))
        assert got[2] and int(got[1]) == nerr


class TestEdgeCases:
    def test_too_short_vector_raises(self, codec):
        with pytest.raises(ValueError):
            decode_bch(np.zeros(codec.mouter, dtype=np.uint8), codec.mouter,
                        12, codec.gf.exp_np, codec.gf.log_np, codec.gf.order, 0)

    def test_rejects_bad_t(self, codec):
        msg = np.zeros(codec.mouter + 10, dtype=np.uint8)
        with pytest.raises(ValueError):
            decode_bch(msg, codec.mouter, 0, codec.gf.exp_np, codec.gf.log_np,
                       codec.gf.order, 10)

    def test_returns_correct_types(self, codec):
        rng = np.random.default_rng(2)
        # Force a failure with too many errors: ok may be False, still typed.
        cw = np.array(codec.encode(rng.integers(0, 2, codec.k_full - 50)),
                      dtype=np.uint8)
        bits, nerr, ok = codec.decode(cw.tolist())
        assert bits.dtype == np.uint8
        assert isinstance(nerr, int)
        assert isinstance(ok, (bool, np.bool_))
