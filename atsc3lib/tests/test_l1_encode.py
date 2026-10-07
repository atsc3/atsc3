"""L1 signalling serialiser + TX cell mapping (A/322 Tables 9.2/9.8).

The receive chain parses L1 signalling and FEC-decodes it; the transmitter
needs the inverse.  These gates pin both directions against the real RF33
fixtures: a parsed block, serialised and re-parsed, must be field-for-field
identical with a matching CRC, and a parsed block, re-serialised, encoded to
cells and FEC-decoded back, must reproduce the same info bits.
"""

import os

import numpy as np
import pytest

from atsc3lib import spec
from atsc3lib.l1_basic import L1BasicCodec
from atsc3lib.l1_detail import (
    L1DetailCodec, preamble_block_deinterleave, preamble_block_interleave)
from atsc3lib.l1_signaling import parse_l1_basic, parse_l1_detail
from atsc3lib.l1_signaling_encode import (
    BitWriter, l1_basic_to_bits, l1_detail_to_bits)

_DATA = os.path.join(os.path.dirname(__file__), 'data')
_L1B = os.path.join(_DATA, 'rf33_l1basic_info.npy')
_L1D = os.path.join(_DATA, 'rf33_l1detail_info.npy')


def _fixtures():
    if not (os.path.exists(_L1B) and os.path.exists(_L1D)):
        pytest.skip("real-air L1 fixtures not present")
    lb = parse_l1_basic(np.load(_L1B))
    ld = parse_l1_detail(np.load(_L1D), lb)
    return lb, ld


class TestBitWriter:
    def test_msb_first(self):
        assert list(BitWriter().write(0b101, 3).bits()) == [1, 0, 1]
        assert list(BitWriter().write(1, 1).write(0, 1).bits()) == [1, 0]

    def test_width_check(self):
        with pytest.raises(ValueError):
            BitWriter().write(4, 2)

    def test_crc_matches_reference(self):
        from atsc3lib.crc import crc32_ok
        w = BitWriter().write(0xABCD, 16)
        w.crc32_bits()
        assert len(w.bits()) == 48
        assert crc32_ok(w.bits())


class TestL1BasicSerialize:
    def test_round_trip_air(self):
        lb, _ = _fixtures()
        bits = l1_basic_to_bits(lb)
        assert len(bits) == 200
        assert lb.raw['L1B_reserved'] == (1 << 48) - 1
        lb2 = parse_l1_basic(bits)
        assert lb2.raw == lb.raw
        assert lb2.crc_ok
        assert lb2 == lb

    def test_identical_to_air_fixture(self):
        lb, _ = _fixtures()
        air = np.load(_L1B)
        assert np.array_equal(l1_basic_to_bits(lb), air)


class TestL1DetailSerialize:
    def test_round_trip_air(self):
        lb, ld = _fixtures()
        bits = l1_detail_to_bits(ld, lb)
        assert len(bits) == lb.l1_detail_size_bytes * 8
        ld2 = parse_l1_detail(bits, lb)
        assert ld2.crc_ok
        assert ld2.bsid == ld.bsid
        assert ld2.reserved_len == ld.reserved_len
        assert ld2.reserved_all_ones == ld.reserved_all_ones
        assert len(ld2.subframes) == len(ld.subframes)
        for s1, s2 in zip(ld.subframes, ld2.subframes):
            assert {k: v for k, v in s1.items() if k != 'plps'} == \
                   {k: v for k, v in s2.items() if k != 'plps'}
            for p1, p2 in zip(s1['plps'], s2['plps']):
                assert p1 == p2

    def test_identical_to_air_fixture(self):
        lb, ld = _fixtures()
        air = np.load(_L1D)
        assert np.array_equal(l1_detail_to_bits(ld, lb), air)


class TestTxCellMapping:
    def test_l1_basic_bits_to_cells_round_trip(self):
        lb, _ = _fixtures()
        codec = L1BasicCodec(spec.PREAMBLE_STRUCTURE[27].l1b_mode)
        tx = codec.encode(l1_basic_to_bits(lb))
        cells = codec.bits_to_cells(tx)
        out, ok = codec.decode_cells(cells)
        assert ok
        assert np.array_equal(out, l1_basic_to_bits(lb)[:codec.n_tx])

    def test_l1_detail_bits_to_cells_round_trip(self):
        lb, ld = _fixtures()
        codec = L1DetailCodec(lb.l1_detail_fec_type + 1,
                              lb.l1_detail_size_bytes * 8)
        tx = codec.encode(l1_detail_to_bits(ld, lb))
        cells = codec.bits_to_cells(tx)
        out, bch_ok, crc_ok = codec.decode_cells(cells)
        assert bch_ok and crc_ok
        assert np.array_equal(out, l1_detail_to_bits(ld, lb))

    @pytest.mark.parametrize('n_symbols', [2, 3, 4])
    def test_preamble_block_interleave_round_trip(self, n_symbols):
        rng = np.random.default_rng(n_symbols)
        m = rng.integers(0, 4, size=37, dtype=np.uint8)
        y = preamble_block_interleave(m, n_symbols)
        assert np.array_equal(preamble_block_deinterleave(y, n_symbols), m)
