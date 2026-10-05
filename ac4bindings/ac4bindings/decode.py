"""Whole-track AC-4 decode: raw frames in, per-channel PCM out.

This is the entry point that turns a captured AC-4 asset -- a sequence of raw
``raw_ac4_frame`` units, as reassembled from MMTP or ROUTE -- into audio.  It
packages the frame walk (TOC -> element -> A-SPX payload -> core + HF render)
so callers (tests, ``atsc3lib``) do not each re-implement it.

The bitstream version 2 syntax of ETSI TS 103 190-2 governs ATSC 3.0; the ASF
core and A-SPX toolchain are implemented from TS 103 190-1/2 and gated against
the independent reference receiver.

Scope: the ASF core of the 5_X element (``channel_mode`` 4,
``5_X_codec_mode`` 1) and the stereo ``channel_pair_element``
(``channel_mode`` 1, ``stereo_codec_mode`` 0/1), both with their A-SPX.  The
5_X modes 2/3 (ASPX_ACPL_1/2) and 4 (ASPX_ACPL_3), the ACPL pair modes, and
the SSF speech frontend are not decoded; frames that carry them are counted
and skipped, never faked.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import aspx, aspx_render

#: Bitstream version 2 is TS 103 190-2 (ATSC 3.0).
BITSTREAM_V2 = 2
#: ``ac4_sync_word`` (2 bytes) then ``bitstream_version`` (2 bits) precede the
#: length-prefixed payload, so the version is bits 6..7 of byte 0.
_VERSION_SHIFT = 6
_VERSION_MASK = 3
#: The only 5_X codec mode this decoder carries (Table 96, ASPX_ASF).
CODEC_MODE_ASF = 1


def load_frames(data: bytes):
    """Split a length-prefixed AC-4 asset into raw frames.

    The fixture format is a 4-byte little-endian length then that many bytes,
    repeated -- the same framing ``atsc3lib`` writes for captured audio.
    """
    out, o = [], 0
    while o + 4 <= len(data):
        n = struct.unpack("<I", data[o:o + 4])[0]
        o += 4
        if o + n > len(data):
            break
        out.append(data[o:o + n])
        o += n
    return out


def _is_v2(frame: bytes) -> bool:
    return ((frame[0] >> _VERSION_SHIFT) & _VERSION_MASK) == BITSTREAM_V2


def _trees():
    from . import build_huff_tree, huffman
    spectrum = {
        cb: build_huff_tree(getattr(huffman, f"ASF_HCB_{cb}_LEN"),
                            getattr(huffman, f"ASF_HCB_{cb}_CW"))
        for cb in range(1, 12)}
    sf = build_huff_tree(huffman.ASF_HCB_SCALEFAC_LEN,
                         huffman.ASF_HCB_SCALEFAC_CW)
    snf = build_huff_tree(huffman.ASF_HCB_SNF_LEN, huffman.ASF_HCB_SNF_CW)
    return spectrum, sf, snf


@dataclass
class DecodeStats:
    """What the decoder did, so a caller can report misses rather than hide
    them."""
    total: int = 0
    decoded: int = 0
    skipped_mode: int = 0
    skipped_error: int = 0
    aspx_frames: int = 0


@dataclass
class Track:
    """The decoded track: per-channel PCM plus the decode statistics."""
    pcm: dict
    stats: DecodeStats
    sample_rate: int = 48000


class Decoder:
    """Stateful AC-4 decoder over a stream of frames.

    Holds the cross-frame ASF (MDCT overlap) and A-SPX (envelope deltas,
    chirp, filter state) state through :mod:`ac4bindings.aspx_render`, so
    frames must be fed in order.
    """

    def __init__(self):
        from . import element, parse_toc
        self._parse_toc = parse_toc
        self._element = element
        self.spectrum, self.sf_tree, self.snf_tree = _trees()
        self.aspx_trees = aspx.build_codebook_trees()
        self._cfg = None
        self._tables = None
        self._fstate = {}
        self._is_pair = False
        self._stats = DecodeStats()

    def element(self, frame: bytes):
        """Decode one frame's element; latch aspx_config on I-frames.

        Returns ``(element_dict, groups)``; ``groups`` is the A-SPX channel
        records (five for a 5.X element, two for a ``channel_pair_element``)
        or None.  The element type is the TOC's own ``channel_modes``:
        ``channel_mode`` 1 is the stereo pair, 4 the 5.X element.
        """
        toc = self._parse_toc(bytes(frame))
        modes = toc.get("channel_modes")
        if modes is not None and len(modes):
            self._is_pair = int(modes[0]) == 1
        o = (toc["toc_bytes"] + toc.get("payload_base", 0)
             + toc["substream_sizes"][0])
        sub = frame[o:o + toc["substream_sizes"][1]]
        b_iframe = bool(toc["b_iframe_global"])
        el = self._element(sub, self.spectrum, self.sf_tree, self.snf_tree,
                           b_iframe=b_iframe, is_pair=self._is_pair)
        if el.get("aspx"):
            self._cfg = aspx.AspxConfig.from_mapping(el["aspx"])
            self._tables = aspx.sbg_tables(self._cfg, 0)
        return el, self._aspx_groups(el, sub, b_iframe)

    def _aspx_groups(self, el, sub, b_iframe):
        from . import aspx as _aspx
        codec_mode = el["codec_mode"]
        if self._cfg is None or self._tables is None:
            return None
        if self._is_pair:
            if codec_mode != CODEC_MODE_ASF:
                return None
            try:
                groups, _ = _aspx.parse_payload_pair(
                    sub, el["bitpos"], self._tables, self._cfg,
                    self.aspx_trees, _aspx.num_aspx_timeslots(), b_iframe,
                    self._fstate)
                return groups
            except Exception:
                return None
        if codec_mode != CODEC_MODE_ASF:
            return None
        try:
            groups, _ = _aspx.parse_payload(
                sub, el["bitpos"], self._tables, self._cfg, self.aspx_trees,
                _aspx.num_aspx_timeslots(), b_iframe, self._fstate)
            return groups
        except Exception:
            return None

    def decode(self, frames) -> Track:
        """Decode a sequence of raw frames to per-channel PCM.

        A-SPX rendering is applied across the whole run at once, so the
        cross-interval state carries correctly.
        """
        data = []
        for frame in frames:
            self._stats.total += 1
            if not _is_v2(frame):
                self._stats.skipped_error += 1
                continue
            try:
                el, groups = self.element(frame)
            except ValueError:
                self._stats.skipped_mode += 1
                continue
            except Exception:
                self._stats.skipped_error += 1
                continue
            if groups is not None:
                self._stats.aspx_frames += 1
            self._stats.decoded += 1
            data.append((el, groups))
        if not data:
            return Track(pcm={}, stats=self._stats)
        st = aspx_render.RenderState(cfg=self._cfg, tables=self._tables)
        pcm, _, _ = aspx_render.render(data, st, use_hf=self._tables is not None)
        return Track(pcm=pcm, stats=self._stats)


def decode_frames(frames) -> Track:
    """Decode an explicit sequence of raw frames to a :class:`Track`.

    This is the form ``atsc3lib`` media reassembly yields (one media sample per
    ``raw_ac4_frame``).
    """
    return Decoder().decode(frames)


def decode_track(data: bytes) -> Track:
    """Decode a length-prefixed AC-4 asset to a :class:`Track`."""
    return Decoder().decode(load_frames(data))
