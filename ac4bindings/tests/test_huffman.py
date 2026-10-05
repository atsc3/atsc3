"""Differential + unit tests for the compiled AC-4 Huffman decoder.

The gate is the independent reference receiver's ``Huff`` decoder (a
(length, codeword) prefix walk written from the same standard), run over
bitstreams assembled from the committed Annex A codebooks.  Because the C is
compared against a separately written decoder, a bug in the C prefix walk or in
the table plumbing shows as a mismatch, not as a self-consistency pass.
"""

import random

import numpy as np
import pytest

from ac4bindings import _ac4, huffman, huff_decode, tables


class _RefHuff:
    """Reference decoder: a dict from (length, codeword) to symbol."""

    def __init__(self, lens, words):
        self.map = {}
        self.maxlen = 0
        for i, (length, word) in enumerate(zip(lens, words)):
            if length <= 0:
                continue
            self.map[(int(length), int(word) & ((1 << int(length)) - 1))] = i
            self.maxlen = max(self.maxlen, int(length))

    def decode(self, bits, pos):
        code = 0
        for length in range(1, self.maxlen + 1):
            if pos >= len(bits):
                raise EOFError
            code = (code << 1) | bits[pos]
            pos += 1
            if (length, code) in self.map:
                return self.map[(length, code)], pos
        raise ValueError("no codeword matched")


def _pack(bits):
    b = bytearray((len(bits) + 7) // 8)
    for k, bit in enumerate(bits):
        if bit:
            b[k >> 3] |= 0x80 >> (k & 7)
    return bytes(b)


def _bits_for_codebook(lens, words, n, rng):
    bits = []
    for _ in range(n):
        i = rng.randrange(len(lens))
        length = int(lens[i])
        word = int(words[i]) & ((1 << length) - 1)
        bits += [(word >> (length - 1 - k)) & 1 for k in range(length)]
    return bits


class TestSurface:
    def test_exposed(self):
        assert hasattr(_ac4, "huff_decode")


class TestContract:
    def test_rejects_unmatched(self):
        # A deliberately incomplete codebook: only codeword '111' (length 3).
        # Reading '000...' runs to maxlen without a match and must fail.
        lens = np.array([3], dtype=np.int32)
        words = np.array([0b111], dtype=np.int32)
        with pytest.raises(ValueError):
            huff_decode(b"\x00\x00", lens, words)

    def test_offset_is_respected(self):
        lens = huffman.ASF_HCB_1_LEN
        words = huffman.ASF_HCB_1_CW
        # The symbol whose codeword is '1' (length 1) is index 40 (mid value).
        idx = next(i for i, (L, w) in enumerate(zip(lens, words)) if L == 1)
        # place it at bit offset 3
        bits = [1, 0, 1] + [(int(words[idx]) >> (int(lens[idx]) - 1 - k)) & 1
                            for k in range(int(lens[idx]))]
        data = _pack(bits)
        symbol, consumed = huff_decode(data, lens, words, offset=3)
        assert symbol == idx
        assert consumed == int(lens[idx])


class TestDifferential:
    @pytest.mark.parametrize("cb", list(range(1, 12)))
    def test_matches_reference(self, cb):
        rng = random.Random(cb)
        lens = getattr(huffman, f"ASF_HCB_{cb}_LEN")
        words = getattr(huffman, f"ASF_HCB_{cb}_CW")
        ref = _RefHuff(lens, words)
        bits = _bits_for_codebook(lens, words, 400, rng)
        data = _pack(bits)
        pos = 0
        checked = 0
        while pos < len(bits) - 32:
            symbol, new_pos = ref.decode(bits, pos)
            length = new_pos - pos
            got_symbol, got_len = huff_decode(data, lens, words, offset=pos)
            assert (got_symbol, got_len) == (symbol, length)
            pos = new_pos
            checked += 1
        assert checked > 50

    def test_scalefac_and_snf_codebooks(self):
        rng = random.Random(99)
        for name in ("ASF_HCB_SCALEFAC", "ASF_HCB_SNF"):
            lens = getattr(huffman, name + "_LEN")
            words = getattr(huffman, name + "_CW")
            ref = _RefHuff(lens, words)
            bits = _bits_for_codebook(lens, words, 400, rng)
            data = _pack(bits)
            pos = 0
            while pos < len(bits) - 32:
                symbol, new_pos = ref.decode(bits, pos)
                got = huff_decode(data, lens, words, offset=pos)
                assert got == (symbol, new_pos - pos)
                pos = new_pos


class TestSpectralDifferential:
    """``asf_spectral_data`` (TS 103 190-1 4.2.8.4) against the reference.

    The fixture holds every spectral call the reference receiver made while
    decoding the RF33 audio: the bitstream bytes, the starting bit position,
    the section list, the sfb offsets and the decoded quantized lines.  The C
    kernel must reproduce both the lines and the final bit position.
    """

    @staticmethod
    def _load():
        import base64
        import gzip
        import json
        from pathlib import Path
        path = Path(__file__).parent / "data" / "ac4_spectral_ref.json.gz"
        calls = json.loads(gzip.decompress(path.read_bytes()))
        return calls

    def test_matches_reference(self):
        import numpy as np
        from ac4bindings import build_huff_tree, huffman, spectral
        trees = {
            cb: build_huff_tree(list(getattr(huffman, f"ASF_HCB_{cb}_LEN")),
                                list(getattr(huffman, f"ASF_HCB_{cb}_CW")))
            for cb in range(1, 12)}
        import base64
        calls = self._load()
        assert len(calls) > 400
        checked = 0
        for call in calls:
            data = base64.b64decode(call["data"])
            lines, pos = spectral(data, call["sects"], call["offsets"], trees,
                                  offset=call["p0"])
            assert pos == call["p1"], call["p0"]
            assert np.array_equal(np.asarray(lines),
                                  np.asarray(call["ref"], dtype=np.int32))
            checked += 1
        assert checked == len(calls)


def _load_trees():
    from ac4bindings import build_huff_tree
    spectrum = {
        cb: build_huff_tree(list(getattr(huffman, f"ASF_HCB_{cb}_LEN")),
                            list(getattr(huffman, f"ASF_HCB_{cb}_CW")))
        for cb in range(1, 12)}
    sf = build_huff_tree(list(huffman.ASF_HCB_SCALEFAC_LEN),
                         list(huffman.ASF_HCB_SCALEFAC_CW))
    snf = build_huff_tree(list(huffman.ASF_HCB_SNF_LEN),
                          list(huffman.ASF_HCB_SNF_CW))
    return spectrum, sf, snf


class TestSfDifferential:
    """``sf_info`` + ``sf_data`` (TS 103 190-1 4.2.7) against the reference.

    Each record holds a frame's bitstream bytes, where its ``sf_info`` and
    ``sf_data`` begin, the parsed framing, and the reference's decoded lines,
    scale factors, noise fill and final bit position.  The C must reproduce all
    of them, including the framing fields and ``ref``.
    """

    @staticmethod
    def _load():
        import gzip
        import json
        from pathlib import Path
        path = Path(__file__).parent / "data" / "ac4_sf_ref.json.gz"
        return json.loads(gzip.decompress(path.read_bytes()))

    def test_matches_reference(self):
        import base64
        from ac4bindings import framing, sf, unpack_framing, _SF_MAX_SFB
        calls = self._load()
        assert len(calls) > 300
        spectrum, sf_tree, snf_tree = _load_trees()
        checked = 0
        for c in calls:
            data = base64.b64decode(c["data"])
            packed, fpos = framing(data, offset=c["framing_start"],
                                   b_lfe=c["lfe"])
            uf = unpack_framing(packed)
            f = c["framing"]
            assert uf["b_long"] == f["b_long"]
            assert uf["num_groups"] == f["num_groups"]
            assert uf["w2g"] == f["w2g"]
            assert list(uf["max_sfb"][:len(f["max_sfb"])]) == f["max_sfb"]
            got = sf(data, packed, spectrum, sf_tree, snf_tree, offset=c["sf_start"])
            assert got["bitpos"] == c["bitpos"], c["sf_start"]
            assert got["groups"] == c["groups"]
            assert [int(x) for x in got["max_sfb"]] == c["max_sfb"]
            assert np.array_equal(np.asarray(got["lines"]),
                                  np.asarray(c["lines"], dtype=np.int32))
            assert got["ref"] == c["ref"]
            sfs = got["sfs"]
            for g, ref_row in enumerate(c["sfs"]):
                for sfb, ref in enumerate(ref_row):
                    v = int(sfs[g * _SF_MAX_SFB + sfb])
                    want = None if ref is None else ref
                    assert (None if v == np.iinfo(np.int32).min else v) == want
            if c["snf"] is None:
                assert got["has_snf"] == 0
            else:
                assert got["has_snf"] == 1
                snf = got["snf"]
                for g, ref_row in enumerate(c["snf"]):
                    for sfb, ref in enumerate(ref_row):
                        v = int(snf[g * _SF_MAX_SFB + sfb])
                        want = None if ref is None else ref
                        assert (None if v == np.iinfo(np.int32).min else v) == want
            for g, ref_off in enumerate(c["ref_offsets"]):
                off = got["offsets_all"][g * _SF_MAX_SFB:
                                         g * _SF_MAX_SFB + len(ref_off)]
                assert [int(x) for x in off] == ref_off[:len(off)]
            checked += 1
        assert checked == len(calls)

    def test_dequant_matches_reference(self):
        import base64
        from ac4bindings import dequant, framing, sf
        calls = self._load()
        spectrum, sf_tree, snf_tree = _load_trees()
        checked = 0
        for c in calls:
            data = base64.b64decode(c["data"])
            packed, _ = framing(data, offset=c["framing_start"], b_lfe=c["lfe"])
            got = sf(data, packed, spectrum, sf_tree, snf_tree,
                     offset=c["sf_start"])
            out = dequant(got["lines"], got["offsets_all"], got["n_off_g"],
                          got["sfs"], got["max_sfb"], 1536)
            ref = np.asarray(c["spectra"], dtype=np.float32)
            assert out.shape == ref.shape
            assert np.allclose(out, ref, atol=1e-4, rtol=1e-5), c["sf_start"]
            checked += 1
        assert checked == len(calls)

    def test_ungroup_matches_reference(self):
        import base64
        from ac4bindings import framing, sf, ungroup
        calls = self._load()
        spectrum, sf_tree, snf_tree = _load_trees()
        checked = 0
        for c in calls:
            data = base64.b64decode(c["data"])
            packed, _ = framing(data, offset=c["framing_start"], b_lfe=c["lfe"])
            got = sf(data, packed, spectrum, sf_tree, snf_tree,
                     offset=c["sf_start"])
            ug = ungroup(got["lines"], got["offsets_all"], got["max_sfb"],
                         got["framing"], 1536)
            for w, length in enumerate(c["lengths"]):
                ref = np.asarray(c["ungrouped"][w], dtype=np.float32)
                assert np.array_equal(ug[w][:length], ref), (c["sf_start"], w)
            checked += 1
        assert checked == len(calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
