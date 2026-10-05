"""Differential test for the compiled 5.X channel element walk.

The gate is the independent reference receiver's ``decode_element`` over the
RF33 5.1 audio: each element's bitstream bytes, the global I-frame flag, the
per-channel quantized spectral lines and the final bit position.  A faithful
element walk must close at the same bit and reproduce every channel.

Only ``5_X_codec_mode`` 1 (ASF) with ``coding_config`` 0 appears in the RF33
audio, so that is what is gated here.  The mode-4 (ASPX_ACPL_3) core is
implemented to the same Table 25 order but is not exercised by these fixtures;
it is reported rather than claimed as validated.
"""

import base64
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from ac4bindings import element, huffman, build_huff_tree

CHANNELS = ("lfe", "L", "R", "Ls", "Rs", "C")


def _load_refs():
    path = Path(__file__).parent / "data" / "ac4_element_ref.json.gz"
    return json.loads(gzip.decompress(path.read_bytes()))


def _trees():
    spectrum = {
        cb: build_huff_tree(list(getattr(huffman, f"ASF_HCB_{cb}_LEN")),
                            list(getattr(huffman, f"ASF_HCB_{cb}_CW")))
        for cb in range(1, 12)}
    sf = build_huff_tree(list(huffman.ASF_HCB_SCALEFAC_LEN),
                         list(huffman.ASF_HCB_SCALEFAC_CW))
    snf = build_huff_tree(list(huffman.ASF_HCB_SNF_LEN),
                          list(huffman.ASF_HCB_SNF_CW))
    return spectrum, sf, snf


class TestElementDifferential:
    def test_matches_reference(self):
        refs = _load_refs()
        assert len(refs) > 40
        spectrum, sf_tree, snf_tree = _trees()
        checked = 0
        for r in refs:
            data = base64.b64decode(r["data"])
            got = element(data, spectrum, sf_tree, snf_tree,
                          b_iframe=r["b_iframe"])
            assert got["bitpos"] == r["bits"], r["bits"]
            for name, ref in r["channels"].items():
                assert name in got["channels"], name
                g = got["channels"][name]
                lines = np.asarray(g["lines"])
                want = np.asarray(ref["lines"], dtype=np.int32)
                assert len(lines) >= len(want)
                assert np.array_equal(lines[:len(want)], want), name
                assert g["ref"] == ref["ref"], name
                assert g["groups"] == ref["groups"], name
            if r["aspx"] is None:
                assert got["aspx"] is None
            else:
                for key in ("start_freq", "stop_freq", "master_freq_scale"):
                    assert got["aspx"][key] == r["aspx"][key]
            checked += 1
        assert checked == len(refs)


def _load_pair_refs():
    path = Path(__file__).parent / "data" / "ac4_pair_ref.json.gz"
    return json.loads(gzip.decompress(path.read_bytes()))


class TestPairElementDifferential:
    """The stereo ``channel_pair_element`` (TS 103 190-1 Table 22) against the
    reference receiver, on the RF33 stereo lanes (MMTP pid14 Spanish simulcast
    and the ROUTE tsi20/tsi30 objects).  Gates the different element header and
    the shared ``two_channel_data``/``aspx_config`` core, and the closing bit
    position."""

    def test_matches_reference(self):
        path = Path(__file__).parent / "data" / "ac4_pair_ref.json.gz"
        if not path.exists():
            pytest.skip("pair-element reference fixture not present")
        refs = _load_pair_refs()
        assert len(refs) > 40
        spectrum, sf_tree, snf_tree = _trees()
        checked = 0
        for r in refs:
            data = base64.b64decode(r["data"])
            got = element(data, spectrum, sf_tree, snf_tree,
                          b_iframe=r["b_iframe"], is_pair=True)
            assert got["is_pair"] == 1
            assert got["bitpos"] == r["bits"], (r["source"], r["bits"])
            for name, ref in r["channels"].items():
                assert name in got["channels"], name
                g = got["channels"][name]
                lines = np.asarray(g["lines"])
                want = np.asarray(ref["lines"], dtype=np.int32)
                assert len(lines) >= len(want)
                assert np.array_equal(lines[:len(want)], want), name
                assert g["ref"] == ref["ref"], name
                assert g["groups"] == ref["groups"], name
            if r["aspx"] is None:
                assert got["aspx"] is None
            else:
                for key in ("start_freq", "stop_freq", "master_freq_scale"):
                    assert got["aspx"][key] == r["aspx"][key]
            checked += 1
        assert checked == len(refs)

    def test_pair_is_not_five_x(self):
        # The pair element's header (2-bit stereo_codec_mode) must not decode
        # on the 5_X path (3-bit codec_mode): dispatching the wrong way has to
        # fail rather than mis-decode a stereo lane as 5.1.
        spectrum, sf_tree, snf_tree = _trees()
        frames = _v2_pair(_frames_pair("ac4_frames_route_tsi20.bin"))
        f = frames[0]
        toc = _parse_toc(f)
        o = toc["toc_bytes"] + toc.get("payload_base", 0) + \
            toc["substream_sizes"][0]
        sub = f[o:o + toc["substream_sizes"][1]]
        with pytest.raises(ValueError):
            element(sub, spectrum, sf_tree, snf_tree,
                    b_iframe=bool(toc["b_iframe_global"]), is_pair=False)


def _frames_pair(name):
    d = (Path(__file__).parent / "data" / name).read_bytes()
    out, o = [], 0
    while o + 4 <= len(d):
        import struct
        n = struct.unpack("<I", d[o:o + 4])[0]
        o += 4
        out.append(d[o:o + n])
        o += n
    return out


def _v2_pair(frames):
    return [f for f in frames if (f[0] >> 6) & 3 == 2]


def _parse_toc(frame):
    from ac4bindings import parse_toc
    return parse_toc(frame)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
