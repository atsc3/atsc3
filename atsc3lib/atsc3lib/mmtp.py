"""MPEG Media Transport Protocol (MMTP) packet parsing.

MMTP (ISO/IEC 23008-1) is the second application-layer transport ATSC 3.0 uses
for media and Service Layer Signaling (A/331 7.2).  An MMTP packet is carried
directly in a UDP datagram; its header identifies the sub-flow by ``packet_id``
and the content by ``payload_type``::

    MMTP packet header | payload (MPU / signaling message / ...)

The header layout follows ISO/IEC 23008-1 (MMTP version 1, which A/331 8.1.2.1
requires): a 12-byte base, an optional 4-byte packet counter, a 2-byte version-1
``ver_ext`` field, an optional header extension, and QoS/flow fields depending
on the flags.  A/331 7.2.3.1 carries ATSC Service Layer Signaling in
``payload_type`` 0x02 packets as ``mmt_atsc3_message()``; media rides in
``payload_type`` 0x00 MPUs (A/331 8.1.2.2) reassembled by :class:`MmtpFlow`.

Gated on RF33 (587 MHz) off-air captures: the WJLA service's MMTP media
packets (``tests/data/mmtp_media_*.bin``), the ``mmtp_media_flow_8071``
datagram dump, and the signaling fixtures (``tests/data/mmtp_sls_*.bin``,
``mmtp_mpt.bin``).

Reference: ISO/IEC 23008-1, ATSC A/331:2021 7.2.3, 7.2.3.1, 8.1.2.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .gz import GZIP_MAGIC, decompress_bounded

#: Upper bound for one decompressed ``mmt_atsc3_message`` SLS payload.
MAX_SLS_CONTENT_BYTES = 8 << 20

#: MMTP version A/331 8.1.2.1 requires (the MMTP packet header "version" field).
MMTP_VERSION = 1

#: Base header fields (ISO/IEC 23008-1): version/flags(1) +
#: flags/payload_type(1) + packet_id(2) + timestamp(4) +
#: packet_sequence_number(4).
MMTP_BASE_HEADER_BYTES = 12
#: MMTP version 1 inserts a 2-byte field between the 12-byte base header and
#: the header extension / payload, called ``ver_ext`` by the reference
#: receiver's object layer.  Its authoritative spec name was not resolved here;
#: on every RF33 datagram the two bytes are the constant 0x1800.  The size is
#: gated empirically rather than assumed: the MPU payload's own
#: ``payload_length`` equals the bytes remaining for 943/943 packets with this
#: field and 0/943 without it (or with 4 bytes).  Kept as a named constant so
#: the choice is visible and testable instead of an inline literal.
MMTP_V1_VER_EXT_BYTES = 2
#: Full version-1 MMTP header with no packet_counter/extension.
MMTP_V1_HEADER_BYTES = MMTP_BASE_HEADER_BYTES + MMTP_V1_VER_EXT_BYTES
#: Optional packet_counter is 4 bytes (ISO/IEC 23008-1).
MMTP_PACKET_COUNTER_BYTES = 4
#: Header extension type (2) + length (2) precede the extension body.
MMTP_EXT_TYPE_BYTES = 2
MMTP_EXT_LEN_BYTES = 2

#: Payload types (ISO/IEC 23008-1; A/331 7.2.3 assigns signaling to 0x02).
PAYLOAD_TYPE_MPU = 0x00
PAYLOAD_TYPE_SIGNALING = 0x02

#: Message identifiers (ISO/IEC 23008-1, A/331 7.2.3.1).
MESSAGE_ID_MMT_ATSC3 = 0x8100
#: MPT message id range and the PA/MPI message id (ISO/IEC 23008-1 10.3.4).
MESSAGE_ID_MPT_START = 0x0011
MESSAGE_ID_MPT_END = 0x0020
MESSAGE_ID_PA = 0x0000

#: Signaling-message payload header in a ``payload_type`` 0x02 packet
#: (ISO/IEC 23008-1 9.3.4 Signaling Message Mode): ``fragmentation_indicator``
#: (2) + ``reserved`` (4) + ``additional_length_header`` (1) +
#: ``aggregation_flag`` (1) + ``fragmentation_counter`` (8) = 2 bytes, after
#: which the message (``message_id`` 2 + ``version`` 1 + ``length``) begins.
#: Gated on the RF33 MMTP signaling fixtures.  (This was previously read as 4
#: bytes because the version-1 ``ver_ext`` field was not yet parsed; with
#: ``ver_ext`` handled in the header the true 2-byte header is exposed and the
#: message still lands at the same offset.)
MMTP_SIGNALING_HEADER_BYTES = 2

#: MPT ``identifier_mapping`` type for an asset id (ISO/IEC 23008-1 10.3.4).
IDENTIFIER_TYPE_ASSET_ID = 0x00
#: MP table id values that carry the package id + asset rows (ISO/IEC 23008-1).
MP_TABLE_ID_FULL = 0x20
MP_TABLE_ID_SUBSET = 0x11

#: ``atsc3_message_content_type`` code points (A/331 Table 7.10).
ATSC3_CONTENT_USBD = 0x0001
ATSC3_CONTENT_MPD = 0x0002
ATSC3_CONTENT_HELD = 0x0003
ATSC3_CONTENT_AEI = 0x0004
ATSC3_CONTENT_VIDEO_STREAM_PROPERTIES = 0x0005
ATSC3_CONTENT_STAGGERCAST_DESCRIPTOR = 0x0006
ATSC3_CONTENT_INBAND_EVENT_DESCRIPTOR = 0x0007
ATSC3_CONTENT_CAPTION_ASSET_DESCRIPTOR = 0x0008
ATSC3_CONTENT_AUDIO_STREAM_PROPERTIES = 0x0009
ATSC3_CONTENT_DWD = 0x000A
ATSC3_CONTENT_RSAT = 0x000B
ATSC3_CONTENT_SECURITY_PROPERTIES = 0x000C
ATSC3_CONTENT_NAME = {
    ATSC3_CONTENT_USBD: "UserServiceDescription",
    ATSC3_CONTENT_MPD: "MPD",
    ATSC3_CONTENT_HELD: "HELD",
    ATSC3_CONTENT_AEI: "Application Event Information",
    ATSC3_CONTENT_VIDEO_STREAM_PROPERTIES: "Video Stream Properties Descriptor",
    ATSC3_CONTENT_STAGGERCAST_DESCRIPTOR: "ATSC Staggercast Descriptor",
    ATSC3_CONTENT_INBAND_EVENT_DESCRIPTOR: "Inband Event Descriptor",
    ATSC3_CONTENT_CAPTION_ASSET_DESCRIPTOR: "Caption Asset Descriptor",
    ATSC3_CONTENT_AUDIO_STREAM_PROPERTIES: "Audio Stream Properties Descriptor",
    ATSC3_CONTENT_DWD: "DWD",
    ATSC3_CONTENT_RSAT: "RSAT",
    ATSC3_CONTENT_SECURITY_PROPERTIES: "Security Properties Descriptor",
}

#: ``atsc3_message_content_compression`` code points (A/331 Table 7.11).
ATSC3_COMPRESSION_GZIP = 0x02


class MmtpError(ValueError):
    """Malformed MMTP packet or message."""


@dataclass(frozen=True)
class MmtpHeader:
    """MMTP packet header fields (ISO/IEC 23008-1)."""
    version: int
    packet_counter_flag: int
    fec_type: int
    extension_flag: int
    rap_flag: int
    qos_flag: int
    flow_identifier_flag: int
    flow_extension_flag: int
    header_compression: int
    indicator_ref_header_flag: int
    payload_type: int
    packet_id: int
    timestamp: int
    packet_sequence_number: int
    ver_ext: bytes
    packet_counter: Optional[int]
    extension_type: Optional[int]
    extension_body: bytes
    header_len: int


@dataclass(frozen=True)
class MmtpPacket:
    """One MMTP packet: header plus payload (ISO/IEC 23008-1)."""
    header: MmtpHeader
    payload: bytes

    @property
    def is_mpu(self) -> bool:
        return self.header.payload_type == PAYLOAD_TYPE_MPU

    @property
    def is_signaling(self) -> bool:
        return self.header.payload_type == PAYLOAD_TYPE_SIGNALING


def parse_mmtp_header(data: bytes) -> MmtpHeader:
    """Parse an MMTP packet header (ISO/IEC 23008-1)."""
    if len(data) < MMTP_BASE_HEADER_BYTES:
        raise MmtpError("truncated MMTP header")
    b0, b1 = data[0], data[1]
    version = b0 >> 6
    packet_counter_flag = (b0 >> 5) & 1
    fec_type = (b0 >> 3) & 0b11
    extension_flag = (b0 >> 2) & 1
    rap_flag = (b0 >> 1) & 1
    qos_flag = b0 & 1
    flow_identifier_flag = (b1 >> 7) & 1
    flow_extension_flag = (b1 >> 6) & 1
    header_compression = (b1 >> 5) & 1
    indicator_ref_header_flag = (b1 >> 4) & 1
    payload_type = b1 & 0x0F
    packet_id = (data[2] << 8) | data[3]
    timestamp = int.from_bytes(data[4:8], "big")
    packet_sequence_number = int.from_bytes(data[8:12], "big")

    offset = MMTP_BASE_HEADER_BYTES
    packet_counter = None
    if packet_counter_flag:
        if len(data) < offset + MMTP_PACKET_COUNTER_BYTES:
            raise MmtpError("truncated MMTP packet_counter")
        packet_counter = int.from_bytes(
            data[offset:offset + MMTP_PACKET_COUNTER_BYTES], "big")
        offset += MMTP_PACKET_COUNTER_BYTES

    ver_ext = b""
    if version == MMTP_VERSION:
        if len(data) < offset + MMTP_V1_VER_EXT_BYTES:
            raise MmtpError("truncated MMTP version-1 header")
        ver_ext = bytes(data[offset:offset + MMTP_V1_VER_EXT_BYTES])
        offset += MMTP_V1_VER_EXT_BYTES

    extension_type = None
    extension_body = b""
    if extension_flag:
        need = offset + MMTP_EXT_TYPE_BYTES + MMTP_EXT_LEN_BYTES
        if len(data) < need:
            raise MmtpError("truncated MMTP header extension")
        extension_type = (data[offset] << 8) | data[offset + 1]
        ext_len = (data[offset + 2] << 8) | data[offset + 3]
        offset += MMTP_EXT_TYPE_BYTES + MMTP_EXT_LEN_BYTES
        if len(data) < offset + ext_len:
            raise MmtpError("truncated MMTP header extension body")
        extension_body = data[offset:offset + ext_len]
        offset += ext_len

    return MmtpHeader(
        version=version, packet_counter_flag=packet_counter_flag,
        fec_type=fec_type, extension_flag=extension_flag, rap_flag=rap_flag,
        qos_flag=qos_flag, flow_identifier_flag=flow_identifier_flag,
        flow_extension_flag=flow_extension_flag,
        header_compression=header_compression,
        indicator_ref_header_flag=indicator_ref_header_flag,
        payload_type=payload_type, packet_id=packet_id, timestamp=timestamp,
        packet_sequence_number=packet_sequence_number, ver_ext=ver_ext,
        packet_counter=packet_counter, extension_type=extension_type,
        extension_body=bytes(extension_body), header_len=offset)


def parse_mmtp_packet(data: bytes) -> MmtpPacket:
    """Parse an MMTP packet (header + payload) from a UDP datagram."""
    header = parse_mmtp_header(data)
    return MmtpPacket(header=header, payload=data[header.header_len:])


@dataclass(frozen=True)
class MmtAtsc3Message:
    """An ``mmt_atsc3_message()`` signaling message (A/331 7.2.3.1)."""
    message_id: int
    version: int
    length: int
    service_id: int
    content_type: int
    content_version: int
    content_compression: int
    uri: str
    content: bytes

    @property
    def content_type_name(self) -> str:
        return ATSC3_CONTENT_NAME.get(self.content_type,
                                      f"reserved(0x{self.content_type:04x})")

    def decompressed(self) -> bytes:
        """Message content with ``atsc3_message_content_compression`` applied.

        Inflation is bounded (A/331 7.2.3.1 SLS fragments are a few kB).
        """
        return decompress_bounded(
            self.content, max_bytes=MAX_SLS_CONTENT_BYTES,
            is_gzip=self.content_compression == ATSC3_COMPRESSION_GZIP)


def parse_mmt_atsc3_message(payload: bytes) -> MmtAtsc3Message:
    """Parse an ``mmt_atsc3_message()`` from a signaling packet's payload."""
    if len(payload) < 8:
        raise MmtpError("truncated signaling message")
    message_id = (payload[0] << 8) | payload[1]
    if message_id != MESSAGE_ID_MMT_ATSC3:
        raise MmtpError(f"not an mmt_atsc3_message: id 0x{message_id:04x}")
    version = payload[2]
    length = int.from_bytes(payload[3:7], "big")
    body = payload[7:]
    if len(body) < 11:
        raise MmtpError("truncated mmt_atsc3_message payload")
    service_id = (body[0] << 8) | body[1]
    content_type = (body[2] << 8) | body[3]
    content_version = body[4]
    content_compression = body[5]
    uri_length = body[6]
    uri = body[7:7 + uri_length].decode("utf-8", "replace")
    p = 7 + uri_length
    content_length = int.from_bytes(body[p:p + 4], "big")
    p += 4
    content = bytes(body[p:p + content_length])
    return MmtAtsc3Message(
        message_id=message_id, version=version, length=length,
        service_id=service_id, content_type=content_type,
        content_version=content_version,
        content_compression=content_compression, uri=uri, content=content)


@dataclass(frozen=True)
class SignalingPayloadHeader:
    """Payload header of an MMTP ``payload_type`` 0x02 packet (ISO/IEC 23008-1).

    ``fragmentation_indicator``: 0 = one or more complete signaling messages,
    1 = first fragment, 2 = middle fragment, 3 = last fragment.
    """
    fragmentation_indicator: int
    additional_length_header: int
    aggregation_flag: int
    fragmentation_counter: int
    header_len: int


@dataclass(frozen=True)
class SignalingMessage:
    """A generic MMT signaling message header (ISO/IEC 23008-1 10.3).

    ``length`` is 4 bytes for PA/MPI and ``mmt_atsc3_message``, 2 bytes for
    every other message (A/331 / ISO/IEC 23008-1).  ``payload`` starts after
    the ``length`` field; ``raw`` is the whole message beginning at
    ``message_id`` (what :func:`parse_mmt_atsc3_message` consumes).
    """
    message_id: int
    version: int
    length: int
    header_len: int
    payload: bytes
    raw: bytes


def parse_signaling_payload_header(data: bytes) -> SignalingPayloadHeader:
    """Parse the 2-byte signaling payload header (ISO/IEC 23008-1 9.3.4)."""
    if len(data) < MMTP_SIGNALING_HEADER_BYTES:
        raise MmtpError("truncated signaling payload header")
    b0, b1 = data[0], data[1]
    return SignalingPayloadHeader(
        fragmentation_indicator=(b0 >> 6) & 0b11,
        additional_length_header=(b0 >> 1) & 1,
        aggregation_flag=b0 & 1,
        fragmentation_counter=b1,
        header_len=MMTP_SIGNALING_HEADER_BYTES)


def _message_length_bytes(message_id: int) -> int:
    if message_id == MESSAGE_ID_PA or message_id == 0x0001:
        return 4
    if message_id == MESSAGE_ID_MMT_ATSC3:
        return 4
    return 2


def parse_signaling_message(data: bytes,
                            header_len: int = MMTP_SIGNALING_HEADER_BYTES
                            ) -> SignalingMessage:
    """Parse one MMT signaling message header + payload (ISO/IEC 23008-1)."""
    if len(data) < header_len + 3:
        raise MmtpError("truncated signaling message")
    p = header_len
    message_id = (data[p] << 8) | data[p + 1]
    version = data[p + 2]
    p += 3
    nlen = _message_length_bytes(message_id)
    if len(data) < p + nlen:
        raise MmtpError("truncated signaling message length")
    length = int.from_bytes(data[p:p + nlen], "big")
    p += nlen
    return SignalingMessage(message_id=message_id, version=version,
                            length=length, header_len=p,
                            payload=data[p:], raw=data[header_len:])


@dataclass(frozen=True)
class MpTable:
    """The fixed part of an MP Table (ISO/IEC 23008-1 10.3.4).

    Only the fields up to ``number_of_assets`` are parsed: the per-asset
    ``identifier_mapping`` rows need ISO/IEC 23008-1 10.3.4 to decode exactly,
    and guessing their variable layout is worse than not reporting them.  The
    ATSC SLS asset list comes from the USBD ``mmtPackageId`` / ComponentInfo,
    which is parsed in :class:`MmtAtsc3Message` instead.
    """
    table_id: int
    version: int
    length: int
    table_mode: int
    mmt_package_id: str
    number_of_assets: int


def parse_mpt_message(message: SignalingMessage) -> MpTable:
    """Parse the MP Table header of an MPT message (ISO/IEC 23008-1 10.3.4)."""
    if not (MESSAGE_ID_MPT_START <= message.message_id <= MESSAGE_ID_MPT_END):
        raise MmtpError(f"not an MPT message: 0x{message.message_id:04x}")
    data = message.payload
    if len(data) < 5:
        raise MmtpError("truncated MP table")
    table_id = data[0]
    version = data[1]
    length = (data[2] << 8) | data[3]
    reserved_mode = data[4]
    p = 5
    package_id = ""
    if table_id in (MP_TABLE_ID_FULL, MP_TABLE_ID_SUBSET):
        pkg_len = data[p]
        p += 1
        package_id = data[p:p + pkg_len].decode("utf-8", "replace")
        p += pkg_len
        desc_len = (data[p] << 8) | data[p + 1]
        p += 2 + desc_len
    number_of_assets = data[p]
    return MpTable(table_id=table_id, version=version, length=length,
                   table_mode=reserved_mode & 0b11,
                   mmt_package_id=package_id,
                   number_of_assets=number_of_assets)


def parse_signaling_datagram(data: bytes) -> SignalingMessage:
    """Parse a full ``payload_type`` 0x02 MMTP datagram's signaling message."""
    packet = parse_mmtp_packet(data)
    if not packet.is_signaling:
        raise MmtpError("not a signaling packet")
    header = parse_signaling_payload_header(packet.payload)
    return parse_signaling_message(packet.payload,
                                   header_len=header.header_len)


# ---------------------------------------------------------------------------
# MPU payload (ISO/IEC 23008-1 9.3.2.2, A/331 8.1.2.2)
# ---------------------------------------------------------------------------

#: MPU payload header up to and including ``mpu_sequence_number``:
#: payload_length(16) + fragmentation_info(8) + fragment_counter(8) +
#: mpu_sequence_number(32) = 8 bytes (ISO/IEC 23008-1 9.3.2.2 Figure 12).
MPU_PAYLOAD_HEADER_BYTES = 8
#: Timed MFU Data Unit header: movie_fragment_sequence_number(32) +
#: sample_number(32) + offset(32) + priority(8) + dependency_counter(8) = 14
#: bytes (ISO/IEC 23008-1 9.3.2.2 Figure 13).  On RF33 every FT=2 fragment
#: rides this header; established from the air (`offset(k+1) - offset(k)`
#: equals `len(du_k) - 14`) and gated in `tests/test_mmtp_media.py`.
MPU_DU_HEADER_BYTES = 14

#: MPU fragment types (ISO/IEC 23008-1, `FT` field; A/331 8.1.2.2).
FT_MPU_METADATA = 0        # ftyp + mmpu + moov, and other whole-MPU boxes
FT_MOVIE_FRAGMENT = 1      # moof + the header of the following mdat box
FT_MFU = 2                 # media sample / sub-sample data units

#: Bounds.  An MPU is a self-contained media unit whose header lengths arrive
#: over MMTP with no integrity protection, so every allocation is bounded by
#: broadcast physics rather than the signalled number: 8 MB per coded sample
#: (a 720p60 HEVC frame is orders of magnitude smaller), 4096 samples per MPU
#: (real values are ~120 video / ~60 audio), and a finite number of concurrent
#: MPUs.  Values past these are a corrupted header, not a large picture.
MAX_MPU_SAMPLE_BYTES = 8 << 20
MAX_MPU_SAMPLES = 4096
MAX_SAMPLES_TABLE_BYTES = MAX_MPU_SAMPLES * 16
#: Concurrent MPUs tracked per flow; a packet for a new (packet_id, seq) past
#: the cap is dropped and counted, so buffering cannot grow without limit.
MAX_MPU_OBJECTS = 64
#: Ceiling for one MPU's reassembled fMP4 body (declared mdat + moof).
MAX_MPU_BODY_BYTES = 256 << 20


@dataclass(frozen=True)
class MpuPayload:
    """Parsed MPU-mode payload header and its data units (ISO/IEC 23008-1).

    ``fragment_type`` is one of :data:`FT_MPU_METADATA`,
    :data:`FT_MOVIE_FRAGMENT`, :data:`FT_MFU`.  ``timed`` is the ``T`` flag;
    ``fragmentation_indicator`` is ``fi``; ``aggregation_flag`` is ``A``.
    ``data_units`` are the A=1-expanded sub-payloads, or the single remainder
    when A=0.
    """
    payload_length: int
    fragment_type: int
    timed: bool
    fragmentation_indicator: int
    aggregation_flag: bool
    fragment_counter: int
    mpu_sequence_number: int
    data_units: Tuple[bytes, ...]


def parse_mpu_payload(payload: bytes) -> MpuPayload:
    """Parse an MPU-mode MMTP payload (ISO/IEC 23008-1 9.3.2.2).

    ``payload_length`` bounds the declared payload; the A=1 aggregation list
    is walked with that bound so a bad sub-length cannot read past it.
    """
    if len(payload) < MPU_PAYLOAD_HEADER_BYTES:
        raise MmtpError("truncated MPU payload header")
    declared = int.from_bytes(payload[0:2], "big")
    info = payload[2]
    fragment_type = (info >> 4) & 0x0F
    timed = bool((info >> 3) & 1)
    fragmentation_indicator = (info >> 1) & 0b11
    aggregation_flag = bool(info & 1)
    fragment_counter = payload[3]
    mpu_sequence_number = int.from_bytes(payload[4:8], "big")
    end = min(len(payload), 2 + declared)
    rest = payload[MPU_PAYLOAD_HEADER_BYTES:end]

    data_units: Tuple[bytes, ...]
    if aggregation_flag:
        units = []
        pos = 0
        while pos + 2 <= len(rest):
            length = int.from_bytes(rest[pos:pos + 2], "big")
            pos += 2
            if length == 0 or pos + length > len(rest):
                break
            units.append(bytes(rest[pos:pos + length]))
            pos += length
        data_units = tuple(units)
    else:
        data_units = (bytes(rest),) if rest else ()
    return MpuPayload(
        payload_length=declared, fragment_type=fragment_type, timed=timed,
        fragmentation_indicator=fragmentation_indicator,
        aggregation_flag=aggregation_flag, fragment_counter=fragment_counter,
        mpu_sequence_number=mpu_sequence_number, data_units=data_units)


def parse_mfu_data_unit(data_unit: bytes) -> Tuple[int, int, bytes]:
    """Split an FT=2 MFU data unit into ``(sample_number, offset, payload)``.

    The 14-byte timed Data Unit header (ISO/IEC 23008-1 9.3.2.2 Figure 13)
    gives the movie-fragment sequence number, the media sample number and the
    byte ``offset`` of this fragment inside that sample.  The payload that
    follows is ``[MMT hint sample][media sample bytes]`` (A/331 8.1.2.2): the
    hint is a fixed per-sample size read from the movie fragment metadata.
    """
    if len(data_unit) < MPU_DU_HEADER_BYTES:
        raise MmtpError("truncated MFU data unit header")
    sample_number = int.from_bytes(data_unit[4:8], "big")
    offset = int.from_bytes(data_unit[8:12], "big")
    return sample_number, offset, bytes(data_unit[MPU_DU_HEADER_BYTES:])


def iter_boxes(data: bytes, start: int = 0,
               end: Optional[int] = None) -> Tuple[Tuple[int, int, bytes], ...]:
    """ISO-BMFF box walk: ``(offset, size, type)`` for each box in range.

    ``size == 1`` selects the 64-bit largesize that follows the type;
    ``size == 0`` means the box runs to the end.  A box whose declared size
    runs past the available bytes is still yielded once (its header was read),
    then the walk stops: an FT=1 movie fragment carries only the *header* of
    the following ``mdat`` box (A/331 8.1.2.2), so that truncated box's size is
    the declared media length and must be observable.  No box is yielded
    without at least a valid 8-byte header and ``size >= 8``.
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
    return tuple(out)


@dataclass(frozen=True)
class MpuMoof:
    """Sample layout read from an MPU's FT=1 movie fragment (ISO-BMFF).

    ``sample_sizes`` are the media track's per-sample sizes (from the first
    ``traf``'s ``trun``); ``hint_size``/``hint_count`` are the fixed MMT hint
    sample size and count (from the second ``traf``'s ``tfhd`` default sample
    size, used when the first track has no explicit ``trun`` table).
    ``mdat_declared`` is the ``mdat`` box's own size.  All three are the
    transmitter's own statements, used as referees, not self-checks.
    """
    moof_size: Optional[int]
    mdat_declared: Optional[int]
    sample_sizes: Tuple[int, ...]
    hint_size: int
    hint_count: int
    sizes_rejected: int


def parse_mpu_moof(mfmd: bytes) -> MpuMoof:
    """Read the sample table out of an FT=1 ``moof`` fragment (ISO-BMFF).

    Every length comes off the air, so the ``trun`` sample count is checked
    against :data:`MAX_MPU_SAMPLES` *before* the table is built, and each size
    against :data:`MAX_MPU_SAMPLE_BYTES` before it is trusted (A/331 8.1.2.2).
    """
    moof_size = None
    mdat_declared = None
    sample_sizes: Tuple[int, ...] = ()
    hint_size = 0
    hint_count = 0
    sizes_rejected = 0
    for offset, size, box_type in iter_boxes(mfmd):
        if box_type == b"moof":
            moof_size = size
            for po, psz, pt in iter_boxes(mfmd, offset + 8, offset + size):
                if pt != b"traf":
                    continue
                default_sample_size = 0
                count = 0
                sizes = []
                for co, _csz, ct in iter_boxes(mfmd, po + 8, po + psz):
                    if ct == b"tfhd":
                        flags = int.from_bytes(mfmd[co + 9:co + 12], "big")
                        q = co + 16
                        if flags & 0x01:
                            q += 8
                        if flags & 0x02:
                            q += 4
                        if flags & 0x08:
                            q += 4
                        if flags & 0x10 and q + 4 <= len(mfmd):
                            default_sample_size = int.from_bytes(
                                mfmd[q:q + 4], "big")
                    elif ct == b"trun":
                        flags = int.from_bytes(mfmd[co + 9:co + 12], "big")
                        count = int.from_bytes(mfmd[co + 12:co + 16], "big")
                        q = co + 16
                        if flags & 0x01:
                            q += 4
                        if flags & 0x04:
                            q += 4
                        per = sum(4 for bit in (0x100, 0x200, 0x400, 0x800)
                                  if flags & bit)
                        size_at = 4 if flags & 0x100 else 0
                        if flags & 0x200 and count <= MAX_MPU_SAMPLES \
                                and per and q + count * per <= len(mfmd):
                            sizes = [int.from_bytes(
                                mfmd[q + i * per + size_at:
                                     q + i * per + size_at + 4], "big")
                                for i in range(count)]
                        elif flags & 0x200:
                            sizes_rejected = count
                if sizes:
                    if (len(sizes) > MAX_MPU_SAMPLES
                            or any(s > MAX_MPU_SAMPLE_BYTES for s in sizes)):
                        sizes_rejected = len(sizes)
                    else:
                        sample_sizes = tuple(sizes)
                else:
                    hint_size, hint_count = default_sample_size, count
        elif box_type == b"mdat":
            mdat_declared = size
    return MpuMoof(moof_size=moof_size, mdat_declared=mdat_declared,
                   sample_sizes=sample_sizes, hint_size=hint_size,
                   hint_count=hint_count, sizes_rejected=sizes_rejected)


@dataclass
class MpuObject:
    """One MPU reassembled from MMTP packets (A/331 8.1.2.2).

    ``meta`` is the FT=0 bytes (``ftyp``/``mmpu``/``moov`` init segment);
    ``body`` is the concatenated FT=1 movie fragment followed by the media
    samples in sample order and then the MMT hint samples -- the ``mdat``
    layout the second ``traf``'s data offset declares, which is why the data
    units cannot simply be concatenated.  ``media_got``/``media_want`` and
    ``mdat_got``/``mdat_declared`` let a caller judge completeness rather than
    assume it.
    """
    packet_id: int
    mpu_sequence_number: int
    meta: bytes
    body: bytes
    n_meta: int
    n_moof: int
    n_mfu: int
    n_samples: int
    n_short: int
    leading_samples: int
    media_got: int
    media_want: int
    mdat_got: Optional[int]
    mdat_declared: Optional[int]

    @property
    def complete(self) -> bool:
        return (self.mdat_declared is not None and self.n_samples > 0
                and self.n_short == 0
                and self.media_got == self.media_want
                and self.mdat_got == self.mdat_declared)


@dataclass
class MmtpFlow:
    """Reassemble MMTP MPUs from a flow's UDP payloads (A/331 8.1.2.2).

    Packets are grouped by ``(packet_id, mpu_sequence_number)``; FT=0, FT=1
    and FT=2 data units are kept per fragment type and rebuilt in packet
    order.  Concurrent MPU count is capped at :data:`MAX_MPU_OBJECTS`; a
    packet for a new MPU past the cap is dropped and counted.
    """
    packets: int = 0
    bad: int = 0
    dropped: int = 0
    max_objects: int = MAX_MPU_OBJECTS
    _mpus: dict = field(default_factory=dict)

    def feed(self, datagram: bytes) -> None:
        packet = parse_mmtp_packet(datagram)
        if not packet.is_mpu:
            return
        self.packets += 1
        try:
            mpu = parse_mpu_payload(packet.payload)
        except MmtpError:
            self.bad += 1
            return
        key = (packet.header.packet_id, mpu.mpu_sequence_number)
        entry = self._mpus.get(key)
        if entry is None:
            if len(self._mpus) >= self.max_objects:
                self.dropped += 1
                return
            entry = {FT_MPU_METADATA: [], FT_MOVIE_FRAGMENT: [], FT_MFU: []}
            self._mpus[key] = entry
        if mpu.fragment_type in entry:
            for unit in mpu.data_units:
                entry[mpu.fragment_type].append(
                    (packet.header.packet_sequence_number, unit))

    def objects(self) -> List[MpuObject]:
        """Rebuild every tracked MPU into an :class:`MpuObject`."""
        return [self._build(pid, seq, entry)
                for (pid, seq), entry in sorted(self._mpus.items())]

    def _build(self, packet_id: int, seq: int, entry: dict) -> MpuObject:
        meta_parts = entry[FT_MPU_METADATA]
        mfmd_parts = entry[FT_MOVIE_FRAGMENT]
        mfu_parts = entry[FT_MFU]
        meta = b"".join(unit for _, unit in sorted(meta_parts))
        mfmd = b"".join(unit for _, unit in sorted(mfmd_parts))
        moof = parse_mpu_moof(mfmd)
        sizes = moof.sample_sizes
        hint_size = moof.hint_size

        fragments: dict = {}
        for psn, unit in mfu_parts:
            if len(unit) <= MPU_DU_HEADER_BYTES:
                continue
            sample_number, offset, payload = parse_mfu_data_unit(unit)
            fragments.setdefault(sample_number, []).append(
                (offset, psn, payload))

        media: List[bytes] = []
        hints: List[bytes] = []
        media_got = 0
        media_want = 0
        n_short = 0
        leading_samples = 0
        prefix_ok = True
        for index, want in enumerate(sizes):
            sample_number = index + 1
            media_want += want
            buf = bytearray(want)
            have = bytearray(want)
            for offset, _psn, payload in sorted(
                    fragments.get(sample_number, [])):
                if offset == 0 and hint_size:
                    hints.append(payload[:hint_size])
                    payload = payload[hint_size:]
                count = min(len(payload), want - offset)
                if count > 0:
                    buf[offset:offset + count] = payload[:count]
                    have[offset:offset + count] = b"\x01" * count
            n_have = sum(have)
            media_got += n_have
            if n_have != want:
                n_short += 1
                prefix_ok = False
            elif prefix_ok:
                leading_samples = index + 1
            media.append(bytes(buf))
        body = mfmd + b"".join(media) + b"".join(hints)
        return MpuObject(
            packet_id=packet_id, mpu_sequence_number=seq, meta=meta,
            body=body, n_meta=len(meta_parts), n_moof=len(mfmd_parts),
            n_mfu=len(mfu_parts), n_samples=len(sizes), n_short=n_short,
            leading_samples=leading_samples, media_got=media_got,
            media_want=media_want, mdat_got=len(body) - (moof.moof_size or 0),
            mdat_declared=moof.mdat_declared)
