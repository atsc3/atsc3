"""IPv4 / UDP parsing and fragment reassembly.

The ALP layer (A/330) hands up network-layer packets: IPv4 datagrams,
optionally fragmented, carrying UDP.  This module turns them into complete
``(src, dst, sport, dport, payload)`` UDP datagrams, reassembling IPv4
fragments (flags/fragment-offset, RFC 791).

This is not itself an ATSC specification — it is the IP/UDP layer above
A/330 — so the fields follow RFC 791 and RFC 768 directly.

Reference: RFC 791 (IPv4), RFC 768 (UDP).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: IPv4 fixed header / UDP header lengths (RFC 791, RFC 768).
IPV4_MIN_HEADER = 20
UDP_HEADER_BYTES = 8
IPV4_VERSION = 4
PROTO_UDP = 17
FRAG_OFFSET_UNIT = 8
MORE_FRAGMENTS = 0x1

#: Upper bound for one reassembled IPv4 payload.  An IPv4 datagram's total
#: length is 16 bits (RFC 791), so no legitimate reassembly exceeds 65535
#: bytes; a fragment past this is dropped (and counted) so a malformed or
#: hostile stream cannot grow a buffer without limit.
MAX_REASSEMBLED_IPV4_BYTES = (1 << 16) - 1
#: Maximum number of concurrent in-progress fragmented datagrams tracked; a
#: fragment for a new key past this is dropped (and counted).
MAX_PENDING_FRAGMENTS = 64


@dataclass(frozen=True)
class UdpDatagram:
    """A reassembled UDP datagram (RFC 768)."""
    src_ip: bytes
    dst_ip: bytes
    src_port: int
    dst_port: int
    payload: bytes


@dataclass
class IpStats:
    """Reassembly bookkeeping."""
    datagrams: int = 0
    fragments: int = 0
    reassembled: int = 0
    not_ipv4: int = 0
    not_udp: int = 0
    dropped: int = 0


@dataclass
class IpReassembler:
    """Reassemble IPv4 fragments into UDP datagrams (RFC 791).

    Reassembly is bounded: a fragment whose end would exceed the 16-bit IPv4
    total length is dropped, and the number of concurrent in-progress datagrams
    is capped, so neither a single datagram nor the pending set can grow
    without limit.
    """
    stats: IpStats = field(default_factory=IpStats)
    _frags: Dict[Tuple, Dict] = field(default_factory=dict)
    max_pending: int = MAX_PENDING_FRAGMENTS

    def feed(self, packet: bytes) -> List[UdpDatagram]:
        """Feed one IPv4 packet; return any complete UDP datagrams."""
        if len(packet) < IPV4_MIN_HEADER or (packet[0] >> 4) != IPV4_VERSION:
            self.stats.not_ipv4 += 1
            return []
        ihl = (packet[0] & 0x0F) * 4
        total_length = (packet[2] << 8) | packet[3]
        ident = (packet[4] << 8) | packet[5]
        flags = packet[6] >> 5
        frag_offset = (((packet[6] & 0x1F) << 8) | packet[7]) * FRAG_OFFSET_UNIT
        proto = packet[9]
        src, dst = bytes(packet[12:16]), bytes(packet[16:20])
        if proto != PROTO_UDP:
            self.stats.not_udp += 1
            return []
        body = packet[ihl:total_length] if 0 < total_length <= len(packet) \
            else packet[ihl:]
        more = flags & MORE_FRAGMENTS
        if not more and frag_offset == 0:
            self.stats.datagrams += 1
            return self._udp(src, dst, body)
        if frag_offset + len(body) > MAX_REASSEMBLED_IPV4_BYTES:
            self.stats.dropped += 1
            return []
        self.stats.fragments += 1
        key = (src, dst, ident, proto)
        entry = self._frags.get(key)
        if entry is None:
            if len(self._frags) >= self.max_pending:
                self.stats.dropped += 1
                return []
            entry = self._frags[key] = {}
        entry[frag_offset] = bytes(body)
        if not more:
            entry["_end"] = frag_offset + len(body)
        end = entry.get("_end")
        if end is None:
            return []
        chunks, off = [], 0
        while off < end:
            if off not in entry:
                return []
            chunks.append(entry[off])
            off += len(entry[off])
        del self._frags[key]
        self.stats.reassembled += 1
        self.stats.datagrams += 1
        return self._udp(src, dst, b"".join(chunks))

    def _udp(self, src: bytes, dst: bytes, udp: bytes) -> List[UdpDatagram]:
        if len(udp) < UDP_HEADER_BYTES:
            return []
        ulen = (udp[4] << 8) | udp[5]
        payload = udp[UDP_HEADER_BYTES:ulen] \
            if UDP_HEADER_BYTES <= ulen <= len(udp) else udp[UDP_HEADER_BYTES:]
        return [UdpDatagram(src_ip=src, dst_ip=dst,
                            src_port=(udp[0] << 8) | udp[1],
                            dst_port=(udp[2] << 8) | udp[3],
                            payload=payload)]


#: LLS multicast / port (A/331).
LLS_IP = b"\xe0\x00\x17\x3c"        # 224.0.23.60
LLS_PORT = 4937

#: LLS_table_id for the SLT (A/331 Table 6.1).
LLS_SLT = 0x01

#: LLS table_id -> name (A/331 Table 6.1).
LLS_TABLE_NAME = {
    0x01: "SLT", 0x02: "RRT", 0x03: "SystemTime", 0x04: "AEAT",
    0x05: "OnscreenMessageNotification", 0x06: "CertificationData",
    0x07: "SignedMultiTable",
}

#: LLS_table() fixed header (A/331 Table 6.1): table_id, group_id,
#: group_count_minus1, table_version.
LLS_HEADER_BYTES = 4


def is_lls(datagram: UdpDatagram) -> bool:
    """True for a Low-Level Signaling datagram (A/331 6.1)."""
    return datagram.dst_ip == LLS_IP and datagram.dst_port == LLS_PORT


@dataclass(frozen=True)
class LlsTable:
    """A Low-Level Signaling table (A/331 6.1, Table 6.1)."""
    table_id: int
    name: str
    data: bytes
    group_id: int = 0
    group_count_minus1: int = 0
    table_version: int = 0


def parse_lls(payload: bytes) -> Optional[LlsTable]:
    """Parse an LLS_table() header (A/331 Table 6.1).

    The body ``data`` begins after the 4-byte header; for table_id 0x01 it is
    the gzip-compressed SLT XML (A/331 6.3).
    """
    if len(payload) < LLS_HEADER_BYTES:
        return None
    table_id, group_id, group_count_minus1, table_version = payload[:4]
    return LlsTable(table_id=table_id,
                    name=LLS_TABLE_NAME.get(table_id, f"0x{table_id:02x}"),
                    data=payload[LLS_HEADER_BYTES:],
                    group_id=group_id,
                    group_count_minus1=group_count_minus1,
                    table_version=table_version)


def ipv4_checksum(header: bytes) -> int:
    """One's-complement Internet checksum of a 20-byte IPv4 header (RFC 1071)."""
    header = bytes(header)
    if len(header) % 2:
        header += b"\x00"
    total = 0
    for i in range(0, len(header), 2):
        total += (header[i] << 8) | header[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_ipv4_udp(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                   payload: bytes, ttl: int = 64, ident: int = 0
                   ) -> bytes:
    """Build an unfragmented IPv4/UDP datagram (TX inverse of the reassembler).

    Produces the exact bytes :class:`IpReassembler` parses back, with the UDP
    length and the IPv4 header checksum filled in (RFC 791/768).
    """
    payload = bytes(payload)
    udp = (bytes([src_port >> 8, src_port & 0xFF,
                  dst_port >> 8, dst_port & 0xFF])
           + (UDP_HEADER_BYTES + len(payload)).to_bytes(2, 'big')
           + b"\x00\x00" + payload)
    total_length = IPV4_MIN_HEADER + len(udp)
    header = (bytes([(IPV4_VERSION << 4) | (IPV4_MIN_HEADER // 4),
                     0, total_length >> 8, total_length & 0xFF,
                     ident >> 8, ident & 0xFF, 0, 0,
                     ttl, PROTO_UDP, 0, 0])
              + bytes(src_ip) + bytes(dst_ip))
    csum = ipv4_checksum(header)
    header = header[:10] + bytes([csum >> 8, csum & 0xFF]) + header[12:]
    return header + udp


def build_lls_udp(ls_payload: bytes, src_ip: bytes = b"\xac\x12\x81\x14",
                  src_port: int = 1234) -> bytes:
    """Wrap an LLS table payload in the A/331 LLS IPv4/UDP datagram (TX side)."""
    return build_ipv4_udp(src_ip, LLS_IP, src_port, LLS_PORT, ls_payload)
