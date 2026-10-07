"""A/322 5.2.2 Baseband Packet headers.

A Baseband Packet (BBP) is a fixed-length ``Kpayload`` block whose first bytes
are a header and whose remainder is a payload of ALP packets.  Because ALP
packets may be split across Baseband Packets, the header carries a *pointer*
to the first ALP packet that begins inside this one.

Header structure (A/322 5.2.2, Figures 5.4/5.5)::

    Base Field:   MODE(1) + Pointer_LSB(7)                  [1 byte]
                  Pointer_MSB(6) + OFI(2)                    [MODE=1 only]
    Optional:     OFI=01  EXT_TYPE(3) + EXT_LEN_LSB(5)
                  OFI=10  EXT_TYPE(3) + EXT_LEN_LSB(5) + EXT_LEN_MSB(8)
                  OFI=11  NUM_EXT(3)  + EXT_LEN_LSB(5) + EXT_LEN_MSB(8)
    Extension:    OFI=01  0..31 bytes
                  OFI=10  EXT_LEN bytes (0..end of packet)
                  OFI=11  NUM_EXT sub-extensions + padding

``pointer == POINTER_NONE`` (all ones) signals that no ALP packet starts in the
packet (only padding): the real RF33 PLP-16 Baseband Packet is exactly this
case (MODE=1, OFI=10, EXT_TYPE=0b111 all-padding, EXT_LEN=245 -> 249 bytes).

Reference: ATSC A/322:2024-04, Section 5.2.2.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import bisect


#: Base Field bit layout.
MODE_BIT = 7
POINTER_LSB_BITS = 7
POINTER_MSB_BITS = 6
OFI_BITS = 2

#: All-ones pointer: no ALP packet starts in this Baseband Packet.
POINTER_NONE = (1 << (POINTER_LSB_BITS + POINTER_MSB_BITS)) - 1

#: OFI values (A/322 Table 5.1).
OFI_NONE = 0b00
OFI_SHORT = 0b01
OFI_LONG = 0b10
OFI_MIXED = 0b11

#: Extension lengths.
EXT_LEN_SHORT_BITS = 5

#: EXT_TYPE = 0b111: the whole Extension Field is padding (A/322 Table 5.2).
EXT_TYPE_ALL_PADDING = 0b111


@dataclass(frozen=True)
class BasebandPacket:
    """One Baseband Packet header plus its payload.

    Fields cite A/322 5.2.2:
        mode: MODE, 0 = one-byte Base Field, 1 = two-byte.
        pointer: offset from payload start to the first ALP packet that begins
            here, or ``POINTER_NONE``.
        ofi: Optional Field Indicator (Table 5.1).
        ext_type: EXT_TYPE of the first extension (Table 5.2), or None.
        ext_len: length in bytes of the Extension Field.
        num_ext: NUM_EXT (mixed mode only), or None.
        header_len: total header length in bytes.
        payload: the bytes after the header.
    """
    mode: int
    pointer: int
    ofi: int
    ext_type: Optional[int]
    ext_len: int
    num_ext: Optional[int]
    header_len: int
    payload: bytes

    @property
    def starts_alp(self) -> bool:
        """True when an ALP packet begins inside this packet's payload."""
        return self.pointer != POINTER_NONE


def split_baseband_packet(data: bytes) -> BasebandPacket:
    """Parse one Baseband Packet (A/322 5.2.2) from its first bytes."""
    data = bytes(data)
    if len(data) < 1:
        raise ValueError("empty Baseband Packet")
    mode = data[0] >> MODE_BIT
    pointer = data[0] & ((1 << POINTER_LSB_BITS) - 1)
    ofi = OFI_NONE
    off = 1
    if mode:
        if len(data) < 2:
            raise ValueError("truncated 2-byte Base Field")
        pointer |= (data[1] >> OFI_BITS) << POINTER_LSB_BITS
        ofi = data[1] & ((1 << OFI_BITS) - 1)
        off = 2

    ext_type: Optional[int] = None
    num_ext: Optional[int] = None
    ext_len = 0
    if ofi == OFI_SHORT:
        if len(data) < off + 1:
            raise ValueError("truncated short Optional Field")
        ext_type = data[off] >> EXT_LEN_SHORT_BITS
        ext_len = data[off] & ((1 << EXT_LEN_SHORT_BITS) - 1)
        off += 1
    elif ofi in (OFI_LONG, OFI_MIXED):
        if len(data) < off + 2:
            raise ValueError("truncated long/mixed Optional Field")
        if ofi == OFI_MIXED:
            num_ext = data[off] >> EXT_LEN_SHORT_BITS
        else:
            ext_type = data[off] >> EXT_LEN_SHORT_BITS
        ext_len = (((data[off + 1] << EXT_LEN_SHORT_BITS)
                    | (data[off] & ((1 << EXT_LEN_SHORT_BITS) - 1))))
        off += 2

    header_len = off + ext_len
    return BasebandPacket(mode=mode, pointer=pointer, ofi=ofi,
                          ext_type=ext_type, ext_len=ext_len,
                          num_ext=num_ext, header_len=header_len,
                          payload=data[header_len:])


def payload_stream(packets) -> Tuple[bytes, List[int]]:
    """Concatenate Baseband Packet payloads into one ALP byte stream.

    Returns ``(stream, boundaries)`` where ``boundaries`` are byte offsets in
    ``stream`` at which A/322 5.2.2 says an ALP packet starts.  These are used
    to resynchronise the ALP walk across a corrupted packet.
    """
    parts: List[bytes] = []
    boundaries: List[int] = []
    offset = 0
    for pkt in packets:
        if pkt.starts_alp:
            boundaries.append(offset + pkt.pointer)
        parts.append(pkt.payload)
        offset += len(pkt.payload)
    return b"".join(parts), sorted(set(boundaries))


def build_baseband_packet(payload: bytes, pointer: int = 0,
                          ofi: int = OFI_NONE) -> bytes:
    """Build one 2-byte-Base-Field Baseband Packet (A/322 5.2.2, TX inverse).

    MODE = 1 with a 13-bit Pointer split across the Base Field (7 LSBs) and the
    first byte's 6 MSBs; the remaining 2 bits are the OFI.  The default
    ``pointer = 0, ofi = OFI_NONE`` marks an ALP packet starting at the first
    payload byte with no Optional Field, which is the inverse of
    :func:`split_baseband_packet` for that shape.
    """
    if not 0 <= pointer < (1 << (POINTER_LSB_BITS + POINTER_MSB_BITS)):
        raise ValueError("pointer does not fit its 13 bits")
    if ofi != OFI_NONE:
        raise NotImplementedError(
            "only OFI_NONE Baseband Packets are built so far")
    payload = bytes(payload)
    base = bytes([
        0x80 | (pointer & ((1 << POINTER_LSB_BITS) - 1)),
        ((pointer >> POINTER_LSB_BITS) << OFI_BITS) | (ofi & ((1 << OFI_BITS) - 1)),
    ])
    return base + payload


def pack_baseband_stream(stream: bytes, boundaries, kpayload: int,
                         base_field_bytes: int = 2) -> List[bytes]:
    """Split an ALP stream into fixed-length Baseband Packets (A/322 5.2.2).

    Inverse of :func:`payload_stream`: the stream is cut into ``kpayload``-byte
    packets, each prefixed by a Base Field whose 13-bit Pointer is the offset
    of the first ALP boundary at or after the packet's payload start, or
    ``POINTER_NONE`` when no ALP packet begins inside it.  This is the general
    case; one ALP packet may span several packets.

    Args:
        stream: the concatenated ALP packet bytes.
        boundaries: byte offsets in ``stream`` where an ALP packet starts.
        kpayload: Baseband Packet length in bytes (FEC payload capacity).
        base_field_bytes: 1 or 2 (MODE); 2 is the 13-bit-pointer form.
    """
    stream = bytes(stream)
    bounds = sorted(set(int(b) for b in boundaries))
    if base_field_bytes != 2:
        raise NotImplementedError("only 2-byte Base Fields are built")
    body = kpayload - base_field_bytes
    if body <= 0:
        raise ValueError("kpayload too small for the Base Field")
    if len(stream) % body:
        stream = stream + b"\x00" * (body - len(stream) % body)
    packets: List[bytes] = []
    pos = 0
    while pos < len(stream):
        chunk = stream[pos:pos + body]
        nxt = bisect.bisect_left(bounds, pos)
        pointer = bounds[nxt] - pos \
            if nxt < len(bounds) and bounds[nxt] < pos + len(chunk) \
            else POINTER_NONE
        packets.append(build_baseband_packet(chunk, pointer=pointer))
        pos += len(chunk)
    return packets
