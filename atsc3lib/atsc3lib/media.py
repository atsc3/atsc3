"""A/331/A/344 media reassembly: IP datagrams -> MPU/ROUTE objects -> samples.

ATSC 3.0 delivers media over one of two transports, both carried in plain
IPv4/UDP datagrams:

* MMTP (ISO/IEC 23008-1, A/331 8.1.2): an ``mpuf``-branded ISO-BMFF MPU is
  fragmented by MPU Fragment Type -- FT=0 (``ftyp``/``mmpu``/``moov`` and the
  whole-MPU boxes), FT=1 (``moof`` plus the ``mdat`` box header), FT=2 (media
  samples).  :mod:`atsc3lib.mmtp` reassembles these.
* ROUTE/ALC (A/331 Annex A.3): objects are keyed by (TSI, TOI) and rebuilt
  from their 32-bit ``start_offset``.  :mod:`atsc3lib.route` reassembles these.

This module classifies each UDP flow as MMTP or ROUTE without a hard-coded
address or port, feeds the right reassembler, and turns a reassembled
fragmented segment into coded media samples so a codec can consume them.

The classification gate is the transmission's own arithmetic, not a guess: an
MMTP flow's MPU payload ``payload_length`` equals the bytes remaining in the
datagram (A/331 8.1.2.2); a ROUTE flow parses as LCT.  A flow that agrees with
neither is reported as unknown rather than forced.

Reference: ATSC A/331:2021 Annex A.3, 8.1.2; ISO/IEC 23008-1 9.3.2.2.
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from . import mmtp
from . import route

#: On-disk datagram-dump record: src/dst IPv4 (4 bytes each), source and
#: destination port (16 bits each), payload length (32 bits), then payload.
DATAGRAM_DUMP_RECORD = struct.Struct("<4s4sHHI")

#: Payload types at the start of an MPU fMP4 (ISO-BMFF brands).
MPU_BRAND = b"mpuf"


@dataclass(frozen=True)
class Datagram:
    """One reassembled UDP datagram (RFC 768) with its IPv4 addresses."""
    src_ip: bytes
    dst_ip: bytes
    src_port: int
    dst_port: int
    payload: bytes

    @property
    def dst_key(self) -> Tuple[str, int]:
        return (".".join(map(str, self.dst_ip)), self.dst_port)


def read_datagram_dump(data: bytes) -> List[Datagram]:
    """Parse a datagram dump written by :func:`write_datagram_dump`."""
    out = []
    offset = 0
    while offset + DATAGRAM_DUMP_RECORD.size <= len(data):
        src, dst, sport, dport, length = DATAGRAM_DUMP_RECORD.unpack_from(
            data, offset)
        offset += DATAGRAM_DUMP_RECORD.size
        payload = data[offset:offset + length]
        offset += length
        out.append(Datagram(src_ip=src, dst_ip=dst, src_port=sport,
                            dst_port=dport, payload=payload))
    return out


def write_datagram_dump(datagrams: Iterable[Datagram]) -> bytes:
    """Serialise datagrams to the on-disk dump format (round-trips)."""
    out = bytearray()
    for d in datagrams:
        out += DATAGRAM_DUMP_RECORD.pack(
            d.src_ip, d.dst_ip, d.src_port, d.dst_port, len(d.payload))
        out += d.payload
    return bytes(out)


def classify_flow(payloads: List[bytes]) -> str:
    """Return ``"mmtp"``, ``"route"`` or ``"unknown"`` for one UDP flow.

    The MMTP gate is :func:`mmtp.parse_mpu_payload`'s own length arithmetic on
    ``payload_type`` 0 datagrams; the ROUTE gate is a valid LCT header.  A flow
    needs a majority to be claimed, so one malformed packet does not flip it.
    """
    n = min(len(payloads), 400)
    sample = payloads[:n]
    mmtp_ok = 0
    route_ok = 0
    for p in sample:
        try:
            packet = mmtp.parse_mmtp_packet(p)
            if packet.is_mpu:
                header_len = packet.header.header_len
                if len(p) >= header_len + 2:
                    declared = int.from_bytes(
                        p[header_len:header_len + 2], "big")
                    if declared == len(p) - header_len - 2:
                        mmtp_ok += 1
        except mmtp.MmtpError:
            pass
        try:
            route.parse_route_packet(p)
            route_ok += 1
        except route.RouteError:
            pass
    if mmtp_ok and mmtp_ok >= 0.9 * n and mmtp_ok > route_ok:
        return "mmtp"
    if route_ok and route_ok >= 0.9 * n:
        return "route"
    return "unknown"


@dataclass
class Flow:
    """One reassembled UDP flow: its transport and its objects."""
    dst_ip: str
    dst_port: int
    transport: str
    mmtp_flow: Optional[mmtp.MmtpFlow] = None
    route_assembler: Optional[route.RouteAssembler] = None

    @property
    def dst_key(self) -> Tuple[str, int]:
        return (self.dst_ip, self.dst_port)

    def mpu_objects(self) -> List[mmtp.MpuObject]:
        return self.mmtp_flow.objects() if self.mmtp_flow else []

    def route_objects(self) -> List[Tuple[route.RouteObject, bytes]]:
        if not self.route_assembler:
            return []
        out = []
        for (tsi, toi), obj in sorted(self.route_assembler.objects.items()):
            out.append((obj, obj.reassemble()))
        return out


@dataclass
class ReassembledMedia:
    """Every flow recovered from a set of datagrams."""
    flows: List[Flow] = field(default_factory=list)

    def mpus(self) -> List[mmtp.MpuObject]:
        out = []
        for f in self.flows:
            out.extend(f.mpu_objects())
        return out

    def route_objects(self) -> List[Tuple[Flow, route.RouteObject, bytes]]:
        out = []
        for f in self.flows:
            for obj, data in f.route_objects():
                out.append((f, obj, data))
        return out


def reassemble(datagrams: Iterable[Datagram]) -> ReassembledMedia:
    """Classify each UDP flow and reassemble its MMTP MPUs / ROUTE objects."""
    by_flow: Dict[Tuple[str, int], List[bytes]] = {}
    for d in datagrams:
        by_flow.setdefault(d.dst_key, []).append(d.payload)

    media = ReassembledMedia()
    for (dst_ip, dst_port), payloads in sorted(by_flow.items()):
        transport = classify_flow(payloads)
        flow = Flow(dst_ip=dst_ip, dst_port=dst_port, transport=transport)
        if transport == "mmtp":
            flow.mmtp_flow = mmtp.MmtpFlow()
            for p in payloads:
                flow.mmtp_flow.feed(p)
        elif transport == "route":
            flow.route_assembler = route.RouteAssembler()
            for p in payloads:
                try:
                    flow.route_assembler.feed(p)
                except route.RouteError:
                    pass
        media.flows.append(flow)
    return media


def iter_samples(segment: bytes) -> Tuple[bytes, ...]:
    """Split a reassembled fragmented segment into its coded media samples.

    ``segment`` is a reassembled MPU body or ROUTE media object: a ``moof``
    whose first ``traf`` ``trun`` carries the media track's per-sample sizes,
    followed by the ``mdat`` box.  The media samples are laid out in ``mdat``
    order, so the size table slices them directly from just after the 8-byte
    ``mdat`` box header.  Returns the sample byte strings; the caller hands
    each to a codec (HEVC) as one access unit.
    """
    moof = mmtp.parse_mpu_moof(segment)
    sizes = moof.sample_sizes
    data_start = None
    for offset, _size, box_type in mmtp.iter_boxes(segment):
        if box_type == b"mdat":
            data_start = offset + 8
            break
    if data_start is None:
        return ()
    samples = []
    offset = data_start
    for size in sizes:
        end = offset + size
        if end > len(segment):
            break
        samples.append(segment[offset:end])
        offset = end
    return tuple(samples)


def route_segment_boxes(data: bytes) -> Tuple[Tuple[int, int, bytes], ...]:
    """ISO-BMFF boxes of a reassembled ROUTE object (thin re-export)."""
    return mmtp.iter_boxes(data)


@dataclass
class MediaTrack:
    """One media track recovered from a flow: init segment + access units.

    ``init`` is the ``ftyp``/``moov`` (or MPU FT=0) initialisation segment the
    codec needs for its parameter sets; ``samples`` are the coded access units
    in decode order; ``init_boxes``/``sample_count`` describe the structure.
    """
    dst_ip: str
    dst_port: int
    transport: str
    track_id: int
    init: bytes
    samples: Tuple[bytes, ...]
    mdat_declared: Optional[int]
    media_want: int

    @property
    def media_got(self) -> int:
        return sum(len(s) for s in self.samples)

    @property
    def complete(self) -> bool:
        return (self.mdat_declared is not None
                and self.media_got == self.media_want)


def _track_from_mpu(flow: Flow, mpu: mmtp.MpuObject) -> MediaTrack:
    return MediaTrack(
        dst_ip=flow.dst_ip, dst_port=flow.dst_port, transport="mmtp",
        track_id=mpu.packet_id, init=mpu.meta, samples=iter_samples(mpu.body),
        mdat_declared=mpu.mdat_declared, media_want=mpu.media_want)


def _track_from_route(flow: Flow, obj: route.RouteObject,
                      data: bytes) -> MediaTrack:
    # A ROUTE media track is delivered as an init object (TOI 0xFFFFFFFF) and
    # a sequence of media-segment objects; the init object carries no samples.
    if obj.toi == 0xFFFFFFFF:
        return MediaTrack(
            dst_ip=flow.dst_ip, dst_port=flow.dst_port, transport="route",
            track_id=obj.tsi, init=data, samples=(),
            mdat_declared=None, media_want=0)
    moof = mmtp.parse_mpu_moof(data)
    return MediaTrack(
        dst_ip=flow.dst_ip, dst_port=flow.dst_port, transport="route",
        track_id=obj.tsi, init=b"", samples=iter_samples(data),
        mdat_declared=moof.mdat_declared,
        media_want=sum(moof.sample_sizes))


def extract_tracks(media: ReassembledMedia) -> List[MediaTrack]:
    """Every media track in a reassembled capture, init and samples separate.

    MMTP MPUs yield one track per ``packet_id`` (the FT=0 bytes are its init).
    ROUTE objects yield an init track (TOI 0xFFFFFFFF) plus per-TSI media
    tracks; the caller pairs an init with its TSI's samples.
    """
    tracks = []
    for flow in media.flows:
        if flow.transport == "mmtp":
            for mpu in flow.mpu_objects():
                tracks.append(_track_from_mpu(flow, mpu))
        elif flow.transport == "route":
            for obj, data in flow.route_objects():
                tracks.append(_track_from_route(flow, obj, data))
    return tracks
