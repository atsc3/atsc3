"""Tests for MMTP packet parsing, gated on RF33 off-air media packets."""

import gzip
from pathlib import Path

import pytest

from atsc3lib.mmtp import (
    ATSC3_COMPRESSION_GZIP,
    ATSC3_CONTENT_HELD,
    ATSC3_CONTENT_MPD,
    ATSC3_CONTENT_USBD,
    MESSAGE_ID_MMT_ATSC3,
    MMTP_SIGNALING_HEADER_BYTES,
    MMTP_V1_HEADER_BYTES,
    MMTP_VERSION,
    MmtpError,
    PAYLOAD_TYPE_MPU,
    PAYLOAD_TYPE_SIGNALING,
    parse_mmt_atsc3_message,
    parse_mpt_message,
    parse_mmtp_packet,
    parse_signaling_datagram,
    parse_signaling_payload_header,
)

DATA = Path(__file__).parent / "data"
PID14 = DATA / "mmtp_media_pid14.bin"
PID12 = DATA / "mmtp_media_pid12.bin"
SLS_USBD = DATA / "mmtp_sls_usbd.bin"
SLS_HELD = DATA / "mmtp_sls_held.bin"
MPT = DATA / "mmtp_mpt.bin"


class TestMmtpHeader:
    def test_pid14_media_packet(self):
        pkt = parse_mmtp_packet(PID14.read_bytes())
        h = pkt.header
        assert h.version == MMTP_VERSION
        assert h.payload_type == PAYLOAD_TYPE_MPU
        assert h.packet_id == 14
        assert not h.packet_counter_flag
        assert not h.extension_flag
        assert h.packet_counter is None
        assert h.ver_ext == b"\x18\x00"
        assert h.header_len == MMTP_V1_HEADER_BYTES
        assert pkt.is_mpu and not pkt.is_signaling

    def test_pid12_media_packet(self):
        pkt = parse_mmtp_packet(PID12.read_bytes())
        assert pkt.header.version == MMTP_VERSION
        assert pkt.header.packet_id == 12
        assert pkt.header.payload_type == PAYLOAD_TYPE_MPU
        assert len(pkt.payload) == len(PID12.read_bytes()) - MMTP_V1_HEADER_BYTES

    def test_sequence_numbers_increment(self):
        first = parse_mmtp_packet(PID12.read_bytes())
        second = parse_mmtp_packet(
            (DATA / "mmtp_media_pid12_next.bin").read_bytes())
        assert first.header.packet_id == second.header.packet_id == 12
        assert (second.header.packet_sequence_number
                == first.header.packet_sequence_number + 1)

    def test_truncated_header(self):
        with pytest.raises(MmtpError):
            parse_mmtp_packet(b"\x40\x00\x00")


class TestMmtAtsc3Message:
    def _message(self, content_type=ATSC3_CONTENT_USBD, body=b"<xml/>",
                 compression=0x01, uri="urn:x"):
        uri_b = uri.encode()
        payload = (b"".join([
            (MESSAGE_ID_MMT_ATSC3).to_bytes(2, "big"),
            bytes([1]),                       # version
            b"\x00\x00\x00\x00",              # length (ignored by parser)
            (2).to_bytes(2, "big"),           # service_id
            content_type.to_bytes(2, "big"),
            bytes([0]),                       # content_version
            bytes([compression]),
            bytes([len(uri_b)]),
            uri_b,
            len(body).to_bytes(4, "big"),
            body,
        ]))
        return parse_mmt_atsc3_message(payload)

    def test_parses_usbd(self):
        m = self._message()
        assert m.message_id == MESSAGE_ID_MMT_ATSC3
        assert m.service_id == 2
        assert m.content_type == ATSC3_CONTENT_USBD
        assert m.content_type_name == "UserServiceDescription"
        assert m.uri == "urn:x"
        assert m.content == b"<xml/>"

    def test_mpd_content_type_name(self):
        m = self._message(content_type=ATSC3_CONTENT_MPD)
        assert m.content_type_name == "MPD"

    def test_gzip_content_decompresses(self):
        m = self._message(body=gzip.compress(b"<MPD/>"),
                          compression=ATSC3_COMPRESSION_GZIP)
        assert m.decompressed() == b"<MPD/>"

    def test_plain_content_passthrough(self):
        m = self._message(body=b"<HELD/>", compression=0x01)
        assert m.decompressed() == b"<HELD/>"

    def test_rejects_other_message_id(self):
        with pytest.raises(MmtpError):
            parse_mmt_atsc3_message(b"\x80\x01" + bytes(20))


class TestMmtpSignaling:
    """Off-air MMT signaling for the WJLA MMTP service (RF33)."""

    def test_usbd_packet_header(self):
        pkt = parse_mmtp_packet(SLS_USBD.read_bytes())
        assert pkt.is_signaling
        assert pkt.header.packet_id == 0
        assert pkt.header.payload_type == PAYLOAD_TYPE_SIGNALING

    def test_signaling_payload_header(self):
        pkt = parse_mmtp_packet(SLS_USBD.read_bytes())
        h = parse_signaling_payload_header(pkt.payload)
        assert h.fragmentation_indicator == 0
        assert not h.aggregation_flag
        assert h.header_len == MMTP_SIGNALING_HEADER_BYTES == 2

    def test_usbd_message_is_wjla(self):
        msg = parse_signaling_datagram(SLS_USBD.read_bytes())
        assert msg.message_id == MESSAGE_ID_MMT_ATSC3
        usbd = parse_mmt_atsc3_message(msg.raw)
        assert usbd.service_id == 2
        assert usbd.content_type == ATSC3_CONTENT_USBD
        assert usbd.content_compression == ATSC3_COMPRESSION_GZIP
        xml = usbd.decompressed().decode("utf-8")
        assert "BundleDescriptionMMT" in xml
        assert "WJLA" in xml
        assert 'mmtPackageId="TRI-PID-2"' in xml

    def test_held_message(self):
        msg = parse_signaling_datagram(SLS_HELD.read_bytes())
        held = parse_mmt_atsc3_message(msg.raw)
        assert held.content_type == ATSC3_CONTENT_HELD
        assert "HELD" in held.decompressed().decode("utf-8")

    def test_mpt_package_and_asset_count(self):
        msg = parse_signaling_datagram(MPT.read_bytes())
        assert 0x11 <= msg.message_id <= 0x20
        mpt = parse_mpt_message(msg)
        assert mpt.mmt_package_id == "TRI-PID-2"
        assert mpt.number_of_assets == 4
        assert mpt.table_id == 0x20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
