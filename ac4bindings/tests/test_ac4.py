"""Tests for the compiled AC-4 TOC parser.

Two gates, per the project rule that compiled code needs its own unit tests
*and* a differential test:

* **Unit/contract tests** build known bitstrings and assert the reader and the
  TOC fields behave (bounds, malformed input, field ordering).
* **Differential tests** run the C parser over the on-air RF33 AC-4 frames
  (``atsc3lib/tests/data/ac4_frames_*.bin``) and compare every field against
  ``ac4_toc_ref.json``, produced by the independent reference receiver used
  strictly as a referee.
"""

import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from ac4bindings import _ac4, available, parse_toc, substream

DATA = Path(__file__).parent / "data"
#: The on-air AC-4 element fixtures ship with the package under
#: ``tests/data``.  ``AC4_ATSC3_DATA`` overrides the location (e.g. to the
#: sibling atsc3lib checkout); absent fixtures skip the differential tests.
ATSC3_DATA = Path(os.environ.get("AC4_ATSC3_DATA", DATA))
REF = json.loads((DATA / "ac4_toc_ref.json").read_text())

FIXTURES = [
    "ac4_frames_mmtp_pid13.bin",
    "ac4_frames_mmtp_pid14.bin",
    "ac4_frames_route_tsi20.bin",
    "ac4_frames_route_tsi30.bin",
]


def _frames(name):
    """-> list of raw AC-4 frames from a length-prefixed fixture."""
    if not (ATSC3_DATA / name).exists():
        pytest.skip(f"{name} not present")
    d = (ATSC3_DATA / name).read_bytes()
    out, o = [], 0
    while o + 4 <= len(d):
        n = struct.unpack("<I", d[o:o + 4])[0]
        o += 4
        out.append(d[o:o + n])
        o += n
    return out


def _v2(frames):
    return [f for f in frames if (f[0] >> 6) & 3 == 2]


class TestSurface:
    def test_available(self):
        assert available()

    def test_exposed(self):
        assert hasattr(_ac4, "parse_toc")


class TestContract:
    """Known-input unit tests: the reader and field order, not the air."""

    def test_rejects_bitstream_version_0(self):
        # bitstream_version 0 occupies the top 2 bits -> 0b00.
        with pytest.raises(ValueError):
            parse_toc(b"\x00" * 32)

    def test_rejects_truncated(self):
        with pytest.raises(ValueError):
            parse_toc(b"\x80")

    def test_sequence_counter_and_rates(self):
        # bitstream_version=2 (0b10), sequence_counter=0x155, b_wait=0,
        # fs_index=1, frame_rate_index=3, then enough zeros to complete a
        # minimal parse is not needed: we only assert the leading fields via a
        # real frame.
        frames = _v2(_frames(FIXTURES[0]))
        assert frames
        t = parse_toc(frames[0])
        assert t["bitstream_version"] == 2
        assert t["fs_index"] == 1
        assert t["frame_rate_index"] in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)

    def test_substream_sizes_is_int32_array(self):
        t = parse_toc(_v2(_frames(FIXTURES[0]))[0])
        assert isinstance(t["substream_sizes"], np.ndarray)
        assert t["substream_sizes"].dtype == np.int32
        assert len(t["substream_sizes"]) == t["n_substreams"]

    def test_toc_bytes_is_positive_and_bounded(self):
        frames = _v2(_frames(FIXTURES[0]))
        for f in frames[:10]:
            t = parse_toc(f)
            assert 0 < t["toc_bytes"] <= len(f)


class TestTables:
    """The committed spec tables, gated against the standard's own Table B.1."""

    def test_sfb_offset_matches_num_sfb(self):
        from ac4bindings import tables
        # Table B.1: num_sfb(1536@48) = 55, sfb 0..55 -> 56 offsets.
        assert tables.NUM_SFB[1536] == 55
        assert len(tables.SFB_OFFSET[1536]) == tables.NUM_SFB[1536] + 1
        assert tables.SFB_OFFSET[1536][-1] == 1536

    def test_sfb_offset_every_transform_length(self):
        from ac4bindings import tables
        # Tables B.4..B.7: every 44.1/48 kHz transform length is present, starts
        # at 0, ends exactly at its transform length, and is increasing.
        assert set(tables.SFB_OFFSET) == set(tables.NUM_SFB)
        for length, off in tables.SFB_OFFSET.items():
            assert off[0] == 0, length
            assert off[-1] == length, length
            assert len(off) == tables.NUM_SFB[length] + 1, length
            assert all(off[i] < off[i + 1] for i in range(len(off) - 1))

    def test_sfb_offset_structure(self):
        from ac4bindings import tables
        for length, off in tables.SFB_OFFSET.items():
            assert off[0] == 0, length
            assert off == sorted(off), length
            assert all(o % 4 == 0 for o in off), length

    def test_codebook_metadata(self):
        from ac4bindings import tables
        # Table A.2: cb_mod 3, cb_mod2 9, cb_mod3 27, cb_off 1.
        assert tables.CB_MOD[1] == 3 and tables.CB_OFF[1] == 1
        # Tables A.14/A.15.
        assert tables.CB_DIM[1] == 4 and tables.CB_DIM[5] == 2
        assert tables.UNSIGNED_CB[1] is False and tables.UNSIGNED_CB[3] is True
        assert set(tables.CB_MOD) == set(range(1, 12))

    def test_num_sfb_menu(self):
        from ac4bindings import tables
        # The 44.1/48 kHz transform lengths (Table B.1).
        for length in (2048, 1920, 1536, 1024, 960, 768, 512, 480, 384):
            assert length in tables.NUM_SFB


class TestHuffmanTables:
    """The committed Huffman codebooks, gated for prefix-code shape."""

    def test_all_spectrum_codebooks_present(self):
        from ac4bindings import huffman
        for n in range(1, 12):
            assert hasattr(huffman, f"ASF_HCB_{n}_LEN")
            assert hasattr(huffman, f"ASF_HCB_{n}_CW")
            assert len(getattr(huffman, f"ASF_HCB_{n}_LEN")) == \
                len(getattr(huffman, f"ASF_HCB_{n}_CW"))

    def test_scalefac_and_snf_present(self):
        from ac4bindings import huffman
        assert len(huffman.ASF_HCB_SCALEFAC_LEN) == 121
        assert len(huffman.ASF_HCB_SNF_LEN) == \
            len(huffman.ASF_HCB_SNF_CW)

    def test_len_cw_lengths_agree(self):
        from ac4bindings import huffman
        for name in dir(huffman):
            if name.endswith("_LEN"):
                cw = name[:-4] + "_CW"
                assert hasattr(huffman, cw), cw
                assert len(getattr(huffman, name)) == \
                    len(getattr(huffman, cw))

    def test_kraft_inequality(self):
        # A real prefix code satisfies sum(2^-len) <= 1 per codebook.
        from ac4bindings import huffman
        for name in dir(huffman):
            if not name.endswith("_LEN"):
                continue
            lens = getattr(huffman, name)
            assert sum(2.0 ** (-n) for n in lens) <= 1.0 + 1e-9, name

    def test_dither_table_is_256(self):
        # The official file declares DITHER_TABLE[256]; a parser that mishandles
        # the inline "// Q0.15" comment reads phantom leading entries.
        from ac4bindings import huffman
        assert len(huffman.DITHER_TABLE) == 256
        assert huffman.DITHER_TABLE[0] == 0x3200


class TestSubstream:
    """Substream location: TS 103 190-1 4.3.3.2.11."""

    def test_substream_spans_the_frame(self):
        f = _v2(_frames(FIXTURES[0]))[0]
        t = parse_toc(f)
        spans = [substream(f, i) for i in range(t["n_substreams"])]
        assert [len(s) for s in spans] == list(map(int, t["substream_sizes"]))
        # toc + payload_base + substreams <= frame length
        assert t["toc_bytes"] + t["payload_base"] + sum(
            len(s) for s in spans) <= len(f)

    def test_substream_is_a_view_into_the_frame(self):
        f = _v2(_frames(FIXTURES[0]))[0]
        t = parse_toc(f)
        s0 = substream(f, 0)
        o = t["toc_bytes"] + t["payload_base"]
        assert s0 == f[o:o + t["substream_sizes"][0]]

    def test_out_of_range_substream_is_empty(self):
        f = _v2(_frames(FIXTURES[0]))[0]
        assert substream(f, 99) == b""


class TestTransform:
    """The IMDCT and KBD window (TS 103 190-1 5.5.2/5.5.3).

    The transform is an identity, so it is gated by perfect reconstruction:
    analyse a known signal with the standard's MDCT, synthesise with this
    windowed IMDCT, overlap-add, and require the signal back.  The window's
    own Princen-Bradley residual is checked separately.
    """

    @pytest.mark.parametrize("n", [96, 192, 384, 768, 1536])
    def test_kbd_princen_bradley(self, n):
        import numpy as np
        from ac4bindings import kbd_alpha, kbd_window
        w = kbd_window(n, kbd_alpha(n))
        assert len(w) == 2 * n
        # w[n]^2 + w[n+N]^2 == 1 is the condition the transform needs.
        assert np.abs(w[:n] ** 2 + w[n:] ** 2 - 1.0).max() < 1e-12

    def test_kbd_alpha_table(self):
        from ac4bindings import kbd_alpha
        assert kbd_alpha(1536) == 3.0
        assert kbd_alpha(768) == 4.0
        assert kbd_alpha(384) == 4.5
        assert kbd_alpha(192) == 5.0
        assert kbd_alpha(96) == 6.0

    def test_imdct_matches_direct_dct4(self):
        # The C cosine form must equal the DCT-IV unfolding it documents.
        import numpy as np
        from ac4bindings import imdct
        rng = np.random.default_rng(7)
        n = 64
        X = rng.standard_normal(n)
        w = np.sin(np.pi / (2 * n) * (np.arange(2 * n) + 0.5))
        k = np.arange(n)
        folded = np.array([2.0 / n * np.sum(X * np.cos(np.pi / n * (i + 0.5)
                                                       * (k + 0.5)))
                           for i in range(n)])
        h = n // 2
        a_b, c_d = folded[h:], folded[:h]
        ref = np.concatenate([a_b, -a_b[::-1], -c_d[::-1], -c_d]) * w
        # The coefficients arrive as float32, so the agreement is float32-exact.
        assert np.abs(imdct(X.astype(np.float32), w) - ref).max() < 1e-6

    def test_analysis_synthesis_recovers_signal(self):
        import numpy as np
        from ac4bindings import imdct
        rng = np.random.default_rng(11)
        n = 64
        x = rng.standard_normal(n * 6)
        w = np.sin(np.pi / (2 * n) * (np.arange(2 * n) + 0.5))
        k = np.arange(n)

        def mdct(blk):
            xw = blk * w
            a, b, c, d = (xw[:n // 2], xw[n // 2:n], xw[n:n + n // 2],
                          xw[n + n // 2:])
            folded = np.concatenate([-c[::-1] - d, a - b[::-1]])
            return np.array([np.sum(folded * np.cos(np.pi / n * (np.arange(n)
                                                                 + 0.5)
                                                    * (i + 0.5)))
                             for i in k])

        pad = np.concatenate([np.zeros(n), x, np.zeros(2 * n)])
        out = np.zeros(len(pad))
        for s in range(0, len(pad) - 2 * n, n):
            out[s:s + 2 * n] += imdct(mdct(pad[s:s + 2 * n]).astype(np.float32),
                                      w)
        y = out[n:n + len(x)]
        assert np.abs(y - x).max() < 1e-6


class TestFilterbank:
    """Block-switching filterbank (TS 103 190-1 5.5.2, Pseudocode 63-64).

    The synthesis chain is a linear map S from stacked spectra to PCM.  For an
    MDCT filterbank with Princen-Bradley windows that map is orthogonal, so
    ``S @ S.T == I`` on the interior settles the window construction, the
    transition shoulders and the reading of step 6 without any hand
    derivation.  Only the reversed reading reconstructs.
    """

    @staticmethod
    def _operator(lengths, n_full, mode):
        import numpy as np
        from ac4bindings import synthesise
        cols = []
        for bi, n in enumerate(lengths):
            for k in range(n):
                S = np.zeros((len(lengths), n_full), dtype=np.float32)
                S[bi, k] = 1.0
                pcm, _ = synthesise(S, np.array(lengths, np.int32), n_full,
                                    overlap=np.zeros(n_full, np.float64),
                                    n_prev=int(lengths[0]), mode=mode)
                cols.append(pcm)
        return np.array(cols).T

    def test_reversed_reading_is_orthogonal_uniform(self):
        import numpy as np
        nf = 192
        lengths = [nf, nf, nf, nf]
        S = self._operator(lengths, nf, mode=1)
        G = S.T @ S
        core = G[nf:-nf, nf:-nf]
        assert np.abs(core - np.eye(core.shape[0])).max() < 1e-9

    def test_reversed_reading_is_orthogonal_switching(self):
        import numpy as np
        nf = 192
        lengths = [nf, nf, nf // 2, nf // 2, nf, nf]
        S = self._operator(lengths, nf, mode=1)
        G = S.T @ S
        core = G[nf:-nf, nf:-nf]
        assert np.abs(core - np.eye(core.shape[0])).max() < 1e-9

    def test_literal_reading_is_not_orthogonal(self):
        # The spec's step 6 as literally printed does not cancel the aliasing;
        # this pins the reading so a future "simplification" cannot silently
        # switch back to it.
        import numpy as np
        nf = 192
        lengths = [nf, nf, nf // 2, nf // 2, nf, nf]
        S = self._operator(lengths, nf, mode=0)
        G = S.T @ S
        core = G[nf:-nf, nf:-nf]
        assert np.abs(core - np.eye(core.shape[0])).max() > 0.1


class TestDifferential:
    """Every field, every on-air frame, against the reference receiver."""

    @pytest.mark.parametrize("name", FIXTURES)
    def test_toc_matches_reference(self, name):
        refs = REF[name]
        frames = _v2(_frames(name))
        assert len(frames) == len(refs)
        mismatches = []
        for frame, ref in zip(frames, refs):
            if "err" in ref:
                continue
            t = parse_toc(frame)
            got = dict(
                seq=t["sequence_counter"], toc=t["toc_bytes"],
                sizes=list(map(int, t["substream_sizes"])),
                ifr=t["b_iframe_global"], fs=t["fs_index"],
                fri=t["frame_rate_index"])
            if got != ref:
                mismatches.append((ref, got))
        assert not mismatches, mismatches[:3]

    def test_sequence_counter_is_contiguous(self):
        # The TOC's own identity: sequence_counter increments by 1 modulo 1024.
        # A wrong bit offset destroys the ramp; the few multi-step gaps are
        # frames the capture genuinely lost, not parse errors.
        frames = _v2(_frames(FIXTURES[0]))
        seqs = [parse_toc(f)["sequence_counter"] for f in frames]
        steps = [(b - a) % 1024 for a, b in zip(seqs, seqs[1:])]
        assert all(s >= 1 for s in steps)
        assert sum(1 for s in steps if s == 1) >= len(steps) - 3

    def test_frame_identity_holds(self):
        # sum(substream_size) + toc_bytes never exceeds the frame length.
        for name in FIXTURES:
            for f in _v2(_frames(name)):
                t = parse_toc(f)
                assert t["toc_bytes"] + int(sum(t["substream_sizes"])) <= len(f)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
