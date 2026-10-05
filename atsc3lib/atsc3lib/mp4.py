"""Build a playable fragmented ISO-BMFF file from reassembled media tracks.

A broadcast media track is already a sequence of fragmented-MP4 segments: an
initialisation segment (``ftyp``/``moov`` or MMTP MPU FT=0) followed by media
segments (``styp``/``moof``/``mdat`` or MMTP MPU FT=1+FT=2).  Concatenating
them *unchanged* does not play: every MPU is an INDEPENDENT ISOBMFF file whose
``mfhd`` sequence number is 1 and whose ``tfdt`` baseMediaDecodeTime is 0, so
a player shows the first segment and stops.  :func:`retime` rewrites both onto
one continuous timeline.

Two honesty rules, taken from the reference receiver's player and enforced
here rather than hoped for:

* A segment is **complete** only when the media bytes present equal the length
  the ``mdat`` box header itself declares -- that header arrives from the
  transmitter (MMTP FT=1, A/331 8.1.2.2), so it is an external referee, not a
  self-check.  A short segment is either dropped or trimmed to whole samples
  and labelled; it is never silently patched.
* The timeline advances by the **media** ``traf``'s duration, not the hint
  ``traf``'s: the two express a 60000/1001 frame differently (media alternates
  1502/1501 and sums right; the hint rounds to a constant 1502), and picking
  the larger drifts A/V by 2.3 s per two hours.

Reference: ISO/IEC 14496-12 (ISO-BMFF), ISO/IEC 23008-1 (MMT), ATSC A/331.
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import mmtp
from . import route


def boxes(data: bytes, start: int = 0,
          end: Optional[int] = None) -> List[Tuple[int, int, bytes]]:
    """Top-level ISO-BMFF box walk: ``(offset, size, type)`` (ISO/IEC 14496-12).

    ``size == 1`` selects the 64-bit largesize after the type; ``size == 0``
    means the box runs to the end.  A box whose declared size runs past the
    available bytes is yielded once (its header was read) and the walk stops.
    """
    end = len(data) if end is None else min(end, len(data))
    out = []
    offset = start
    while offset + 8 <= end:
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = bytes(data[offset + 4:offset + 8])
        if size == 1:
            if offset + 16 > end:
                break
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
        elif size == 0:
            size = end - offset
        if size < 8:
            break
        out.append((offset, size, box_type))
        if offset + size > end:
            break
        offset += size
    return out


def find_box(data: bytes, path: Tuple[bytes, ...], start: int = 0,
             end: Optional[int] = None) -> Optional[Tuple[int, int]]:
    """Locate the first box at the container ``path``; ``(offset, size)``."""
    if not path:
        return None
    end = len(data) if end is None else min(end, len(data))
    want = path[0]
    for offset, size, box_type in boxes(data, start, end):
        if box_type != want:
            continue
        if len(path) == 1:
            return offset, size
        found = find_box(data, path[1:], offset + 8, offset + size)
        if found is not None:
            return found
    return None


@dataclass(frozen=True)
class TrackInfo:
    """The init segment's media track identity (ISO/IEC 14496-12)."""
    handler: bytes
    timescale: int


def track_info(init: bytes, handler: Optional[bytes] = None) -> Optional[TrackInfo]:
    """Read a ``moov``'s track ``hdlr`` and ``mdhd`` timescale.

    ``handler`` (``b"vide"``/``b"soun"``) selects the ``trak`` when an init
    carries more than one (MMT MPUs carry a second ``hint`` track).  Without
    it, the first non-hint track wins.
    """
    moov = find_box(init, (b"moov",))
    if moov is None:
        return None
    fallback = None
    for o, sz, t in boxes(init, moov[0] + 8, moov[0] + moov[1]):
        if t != b"trak":
            continue
        mdia = find_box(init, (b"mdia",), o + 8, o + sz)
        if mdia is None:
            continue
        hd = None
        ts = None
        for o2, sz2, t2 in boxes(init, mdia[0] + 8, mdia[0] + mdia[1]):
            if t2 == b"hdlr" and o2 + 20 <= len(init):
                hd = bytes(init[o2 + 16:o2 + 20])
            elif t2 == b"mdhd" and o2 + 20 <= len(init):
                version = init[o2 + 8]
                p = o2 + 12 + (16 if version else 8)
                if p + 4 <= len(init):
                    ts = int.from_bytes(init[p:p + 4], "big")
        if ts is None:
            continue
        if handler is not None and hd == handler:
            return TrackInfo(handler=hd, timescale=ts)
        if handler is None and hd != b"hint" and fallback is None:
            fallback = TrackInfo(handler=hd, timescale=ts)
    return fallback


def _traf_duration(seg: bytes, traf_off: int, traf_size: int) -> int:
    """Ticks in one ``traf``'s ``trun`` (media track), or 0 (ISO/IEC 14496-12)."""
    default_duration = 0
    for o, _sz, t in boxes(seg, traf_off, traf_off + traf_size):
        if t == b"tfhd":
            flags = int.from_bytes(seg[o + 9:o + 12], "big")
            q = o + 16
            if flags & 0x01:
                q += 8
            if flags & 0x02:
                q += 4
            if flags & 0x08 and q + 4 <= len(seg):
                default_duration = int.from_bytes(seg[q:q + 4], "big")
        elif t == b"trun":
            if o + 16 > len(seg):
                return 0
            flags = int.from_bytes(seg[o + 9:o + 12], "big")
            count = int.from_bytes(seg[o + 12:o + 16], "big")
            q = o + 16
            if flags & 0x01:
                q += 4
            if flags & 0x04:
                q += 4
            per = sum(4 for bit in (0x100, 0x200, 0x400, 0x800) if flags & bit)
            if flags & 0x100 and per and q + count * per <= len(seg):
                return sum(int.from_bytes(seg[q + i * per:q + i * per + 4], "big")
                           for i in range(count))
            if default_duration:
                return count * default_duration
    return 0


def segment_duration(seg: bytes) -> int:
    """The media track's tick duration in one ``moof`` segment."""
    moof = find_box(seg, (b"moof",))
    if moof is None:
        return 0
    for o, sz, t in boxes(seg, moof[0] + 8, moof[0] + moof[1]):
        if t == b"traf":
            return _traf_duration(seg, o + 8, sz)
    return 0


def retime(seg: bytes, fragment_number: int, base_time: int) -> bytes:
    """Place a segment on a continuous timeline: rewrite ``mfhd`` + ``tfdt``.

    Every MMTP MPU restarts its own fragment sequence at 1 and its decode time
    at 0; concatenating them unchanged makes a player stop after the first one.
    ``base_time`` is an integer tick count by construction.
    """
    d = bytearray(seg)
    mfhd = find_box(d, (b"moof", b"mfhd"))
    if mfhd is not None and mfhd[0] + 16 <= len(d):
        struct.pack_into(">I", d, mfhd[0] + 12, fragment_number)
    moof = find_box(d, (b"moof",))
    if moof is None:
        return bytes(d)
    for o, sz, t in boxes(d, moof[0] + 8, moof[0] + moof[1]):
        if t != b"traf":
            continue
        tfdt = find_box(d, (b"tfdt",), o + 8, o + sz)
        if tfdt is None:
            continue
        if d[tfdt[0] + 8] == 1:
            if tfdt[0] + 20 <= len(d):
                struct.pack_into(">Q", d, tfdt[0] + 12, base_time)
        elif tfdt[0] + 16 <= len(d):
            struct.pack_into(">I", d, tfdt[0] + 12, base_time & 0xFFFFFFFF)
    return bytes(d)


@dataclass(frozen=True)
class _Trun:
    """One ``trun``'s sample table, with absolute offsets into the segment."""
    trun: int
    count: int
    sample_sizes: Optional[Tuple[int, ...]]
    data_offset: Optional[int]
    default_size: int


def _scan_trafs(seg: bytes) -> Optional[Tuple[_Trun, Optional[_Trun]]]:
    """The (media, hint) truns of a fragment, or None.

    The media ``traf`` is the one whose ``trun`` carries a per-sample size; the
    hint ``traf`` carries a ``data_offset`` and a ``tfhd`` default size.
    """
    moof = find_box(seg, (b"moof",))
    if moof is None:
        return None
    media = hint = None
    for o, sz, t in boxes(seg, moof[0] + 8, moof[0] + moof[1]):
        if t != b"traf":
            continue
        trun = None
        default_size = 0
        for co, _csz, ct in boxes(seg, o + 8, o + sz):
            if ct == b"trun":
                trun = co
            elif ct == b"tfhd":
                flags = int.from_bytes(seg[co + 9:co + 12], "big")
                q = co + 16
                if flags & 0x01:
                    q += 8
                if flags & 0x02:
                    q += 4
                if flags & 0x08:
                    q += 4
                if flags & 0x10 and q + 4 <= len(seg):
                    default_size = int.from_bytes(seg[q:q + 4], "big")
        if trun is None:
            continue
        flags = int.from_bytes(seg[trun + 9:trun + 12], "big")
        count = int.from_bytes(seg[trun + 12:trun + 16], "big")
        p = trun + 16
        data_offset = None
        if flags & 0x001 and p + 4 <= len(seg):
            data_offset = int.from_bytes(seg[p:p + 4], "big", signed=True)
            p += 4
        if flags & 0x004:
            p += 4
        per = sum(4 for bit in (0x100, 0x200, 0x400, 0x800) if flags & bit)
        size_at = 4 if flags & 0x100 else 0
        if flags & 0x200 and per and p + count * per <= len(seg):
            sizes = tuple(int.from_bytes(
                seg[p + i * per + size_at:p + i * per + size_at + 4], "big")
                for i in range(count))
            media = _Trun(trun, count, sizes, data_offset, default_size)
        else:
            hint = _Trun(trun, count, None, data_offset, default_size)
    if media is None or media.data_offset is None:
        return None
    return media, hint


def whole_samples_in(seg: bytes, payload_present: int) -> Optional[int]:
    """Number of leading media samples wholly contained in ``payload_present``."""
    scan = _scan_trafs(seg)
    if scan is None or scan[0].sample_sizes is None:
        return None
    total = 0
    keep = 0
    for size in scan[0].sample_sizes:
        if total + size > payload_present:
            break
        total += size
        keep += 1
    return keep


def trim_to_samples(seg: bytes, keep: int) -> Optional[bytes]:
    """Rebuild a fragment holding only its first ``keep`` media samples.

    Both trafs are fixed, not just the media one: the hint track's
    ``data_offset`` follows the media block, so trimming media without moving
    the hint leaves the hint samples pointing into the video.  The ``mdat`` is
    rebuilt as ``media[:sum(sizes[:keep])] + hint[:keep]`` and its declared
    size rewritten.
    """
    scan = _scan_trafs(seg)
    if scan is None:
        return None
    media, hint = scan
    if media.sample_sizes is None:
        return None
    moof = find_box(seg, (b"moof",))
    mdat = find_box(seg, (b"mdat",))
    if moof is None or mdat is None:
        return None
    n = media.count
    keep = max(0, min(int(keep), n))
    if keep == 0:
        return None
    total = sum(media.sample_sizes[:keep])
    start = moof[0] + media.data_offset
    if start + total > len(seg):
        return None
    body = bytes(seg[start:start + total])
    new = bytearray(seg[:mdat[0]])
    struct.pack_into(">I", new, media.trun + 12, keep)
    if hint is not None and hint.data_offset is not None and hint.default_size:
        h_start = moof[0] + hint.data_offset
        h_keep = min(keep, hint.count)
        h_len = h_keep * hint.default_size
        if h_start + h_len > len(seg):
            return None
        struct.pack_into(">I", new, hint.trun + 12, h_keep)
        struct.pack_into(">I", new, hint.trun + 16,
                         media.data_offset + total)
        body += bytes(seg[h_start:h_start + h_len])
    return bytes(new) + (8 + len(body)).to_bytes(4, "big") + b"mdat" + body


@dataclass
class BuiltTrack:
    """One playable fragmented-MP4 file assembled from a media track."""
    dst_ip: str
    dst_port: int
    transport: str
    track_id: int
    handler: bytes
    timescale: int
    init: bytes
    data: bytes
    segments: List[Tuple[int, int, bool]] = field(default_factory=list)
    samples: int = 0
    duration: int = 0

    @property
    def media_bytes(self) -> int:
        return len(self.data) - len(self.init)

    @property
    def seconds(self) -> float:
        return self.duration / self.timescale if self.timescale else 0.0


#: Bounds: a playable file is bounded by the capture it came from, but a
#: corrupt signalled size must not allocate without limit.
MAX_FILE_BYTES = 2 << 30
MAX_SEGMENTS = 4096


def _assemble(init: bytes, fragments: List[bytes], dst_ip: str, dst_port: int,
              transport: str, track_id: int, truncations: List[int]
              ) -> Optional[BuiltTrack]:
    info = track_info(init)
    if info is None:
        return None
    data = bytearray(init)
    segments: List[Tuple[int, int, bool]] = []
    samples = 0
    duration = 0
    fragment_number = 0
    for seq, frag in fragments:
        if len(data) + len(frag) > MAX_FILE_BYTES:
            break
        fragment_number += 1
        retimed = retime(frag, fragment_number, duration)
        dur = segment_duration(retimed)
        data += retimed
        samples += _sample_count(retimed)
        segments.append((seq, len(retimed), seq in truncations))
        duration += dur
    return BuiltTrack(
        dst_ip=dst_ip, dst_port=dst_port, transport=transport,
        track_id=track_id, handler=info.handler, timescale=info.timescale,
        init=bytes(init), data=bytes(data), segments=segments,
        samples=samples, duration=duration)


def _sample_count(seg: bytes) -> int:
    scan = _scan_trafs(seg)
    if scan is None:
        return 0
    return scan[0].count


def build_mmtp_track(flow, packet_id: int, repair: bool = True,
                     handler: Optional[bytes] = None) -> Optional[BuiltTrack]:
    """Assemble one MMTP ``packet_id``'s MPUs into a playable file.

    MPUs are ordered by ``mpu_sequence_number``; the FT=0 bytes are the init.
    A short MPU is trimmed to its leading whole samples when the reassembler
    tracked which samples arrived, and labelled; otherwise it is dropped.
    """
    init = b""
    objects = sorted((m for m in flow.mpu_objects()
                      if m.packet_id == packet_id),
                     key=lambda m: m.mpu_sequence_number)
    fragments: List[Tuple[int, bytes]] = []
    truncations: List[int] = []
    for mpu in objects:
        if not init and mpu.meta:
            init = mpu.meta
            if handler is not None and track_info(init, handler) is None:
                return None
        if not mpu.body:
            continue
        if mpu.complete:
            fragments.append((mpu.mpu_sequence_number, mpu.body))
        elif repair and mpu.leading_samples > 0:
            trimmed = trim_to_samples(mpu.body, mpu.leading_samples)
            if trimmed is not None:
                fragments.append((mpu.mpu_sequence_number, trimmed))
                truncations.append(mpu.mpu_sequence_number)
        if len(fragments) >= MAX_SEGMENTS:
            break
    if not init or not fragments:
        return None
    return _assemble(init, fragments, flow.dst_ip, flow.dst_port, "mmtp",
                     packet_id, truncations)


def build_route_track(flow, tsi: int, repair: bool = True,
                      handler: Optional[bytes] = None) -> Optional[BuiltTrack]:
    """Assemble one ROUTE ``tsi``'s objects into a playable file.

    The init object is TOI ``0xFFFFFFFF``; media objects are ordered by TOI.
    A partial object is trimmed to the bytes before its first gap.
    """
    if not flow.route_assembler:
        return None
    objects = flow.route_assembler.objects
    init_obj = objects.get((tsi, route.ROUTE_TOI_INIT))
    if init_obj is None:
        return None
    init = init_obj.reassemble()
    if handler is not None and track_info(init, handler) is None:
        return None
    fragments: List[Tuple[int, bytes]] = []
    truncations: List[int] = []
    for (t, toi), obj in sorted(objects.items()):
        if t != tsi or toi == route.ROUTE_TOI_INIT:
            continue
        data = obj.reassemble()
        declared = obj.transfer_length
        contiguous = obj.contiguous_length
        if declared is not None and contiguous >= declared:
            fragments.append((toi, data))
        elif repair:
            mdat = find_box(data, (b"mdat",))
            gap = max(0, contiguous - (mdat[0] + 8)) if mdat else 0
            keep = whole_samples_in(data, gap)
            if keep is None:
                continue
            trimmed = trim_to_samples(data, keep)
            if trimmed is not None:
                fragments.append((toi, trimmed))
                truncations.append(toi)
        if len(fragments) >= MAX_SEGMENTS:
            break
    if not fragments:
        return None
    return _assemble(init, fragments, flow.dst_ip, flow.dst_port, "route",
                     tsi, truncations)


def track_ids(flow) -> List[int]:
    """The track ids a flow carries (MMTP packet_ids or ROUTE TSIs)."""
    if flow.transport == "mmtp":
        return sorted({m.packet_id for m in flow.mpu_objects()})
    if flow.route_assembler:
        return sorted({tsi for (tsi, _toi) in flow.route_assembler.objects})
    return []


def build_tracks(media, repair: bool = True) -> List[BuiltTrack]:
    """Every playable track in a :class:`~atsc3lib.media.ReassembledMedia`.

    Each flow is walked, each of its track ids assembled; tracks with no
    complete/repairable segment are omitted rather than emitted empty.
    """
    out = []
    for flow in media.flows:
        for track_id in track_ids(flow):
            if flow.transport == "mmtp":
                built = build_mmtp_track(flow, track_id, repair=repair)
            else:
                built = build_route_track(flow, track_id, repair=repair)
            if built is not None:
                out.append(built)
    return out


def select_track(tracks: List[BuiltTrack], handler: bytes
                 ) -> Optional[BuiltTrack]:
    """The longest track whose init handler is ``handler`` (b"vide"/b"soun")."""
    candidates = [t for t in tracks if t.handler == handler]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.samples)


def _datagrams_to_media(datagrams):
    """Convert ``UdpDatagram`` objects to the media layer's ``Datagram``."""
    from .media import Datagram

    return [Datagram(src_ip=d.src_ip, dst_ip=d.dst_ip, src_port=d.src_port,
                     dst_port=d.dst_port, payload=d.payload)
            for d in datagrams]


def build_from_iq(iq, fs: float, plp_id=None, subframe: int = 0,
                  max_iterations: int = 100, full_search: bool = False,
                  max_frames=None, repair: bool = True, plp_ids=None):
    """Decode a capture's PLP payloads and reassemble every media track.

    The RF33 path decodes one PLP per ``(subframe, plp_id)``; a capture that
    carries media across several PLPs is drained by passing ``plp_ids`` as
    ``(subframe, plp_id)`` pairs.  With both ``plp_id`` and ``plp_ids`` None,
    every layer-0 PLP the L1 signalling carries is drained in one pass (the
    multiplex's media is spread across subframe-0 and subframe-1 PLPs).

    Returns ``(result, media)`` with ``media`` a
    :class:`~atsc3lib.media.ReassembledMedia`.
    """
    from .media import reassemble
    from .receiver import decode_all_plp_frames, decode_plp_frames_streams

    if plp_ids is not None or plp_id is None:
        result, payloads = decode_all_plp_frames(
            iq, fs, targets=plp_ids, max_iterations=max_iterations,
            full_search=full_search, max_frames=max_frames)
    else:
        result, _structure, payloads = decode_plp_frames_streams(
            iq, fs, plp_id=plp_id, subframe=subframe,
            max_iterations=max_iterations, full_search=full_search,
            max_frames=max_frames)
    if not result.l1_detail_ok:
        return result, None
    media = reassemble(_datagrams_to_media(_collect_datagrams(payloads)))
    return result, media


def _collect_datagrams(payloads):
    """Every distinct UDP datagram seen across a list of PLP payloads."""
    from .payload import decode_streams

    seen = set()
    out = []
    for payload in payloads:
        streams = decode_streams(payload)
        for d in streams.datagrams:
            key = (d.src_ip, d.dst_ip, d.src_port, d.dst_port, d.payload)
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
    return out
