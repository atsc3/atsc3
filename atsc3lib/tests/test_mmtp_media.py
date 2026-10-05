"""Tests for A/331/A/344 media reassembly, gated on RF33 off-air captures.

The fixtures are datagram dumps (``*.dg.gz``) captured by draining whole frames
from the RF33 (587 MHz) SDRplay capture, decapsulating to IP/UDP, and writing
``src, dst, sport, dport, length, payload`` records.  Two flows are kept:

* ``mmtp_media_flow_8071`` -- the WJLA MMTP media flow (packet_id 12/13/14/15):
  FT=0 metadata, FT=1 movie fragments, FT=2 MFU samples.
* ``route_media_flow_8321`` -- a ROUTE/ALC media flow (TSI 10 video, 20/30/40
  audio) with complete fragmented-MP4 objects.

The expected object bytes are pinned to SHA-256 of the independent reference
receiver's output, so this is a differential gate, not a self-consistency
check.  The HEVC samples the reassembler extracts must decode to a picture.
"""

import gzip
import hashlib
from pathlib import Path

import numpy as np
import pytest

from atsc3lib import mmtp
from atsc3lib import route
from atsc3lib.media import (
    Datagram,
    classify_flow,
    extract_tracks,
    iter_samples,
    read_datagram_dump,
    reassemble,
    route_segment_boxes,
    write_datagram_dump,
)

DATA = Path(__file__).parent / "data"
MMTP_DG = DATA / "mmtp_media_flow_8071.dg.gz"
ROUTE_DG = DATA / "route_media_flow_8321.dg.gz"

#: SHA-256 of the reference receiver's reassembled objects (differential gate).
ORACLE_SHA256 = {
    (12, 228058002, "init"):
        "1958d64ba4a2c632e7fb3eeb3ede7d2b8e23385446bffefd484ffbe10e47dec9",
    (12, 228058002, "body"):
        "e0fff7ef1194d3589a8dc56a83a57118a2aa8ecba1ecefbd9a75ba9a3730a2cf",
    (15, 228058002, "init"):
        "a04335b18a27a1fc4043bca1b686ae4b21c3a6289c1dbb42687850ff18635930",
    (15, 228058002, "body"):
        "c5a2ecd03fe55781dcbe86a9cfd35d09abbd6b5b41c2732b24a12c6be2964c3e",
}
ROUTE_INIT_SHA256 = \
    "5fba8c99541d39dba7b1b6fce0cbdd1ddcb2d197af1fbb75a4047eb73b17aaea"
ROUTE_SEG_SHA256 = \
    "bd7206a068dc8dc2165137376b55db2481b3b461762c4a78ae63b9ea4808f476"


def _load(path):
    return read_datagram_dump(gzip.decompress(path.read_bytes()))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mmtp_objects():
    media = reassemble(_load(MMTP_DG))
    flow = next(f for f in media.flows if f.dst_key == ("239.255.7.1", 8071))
    return {(m.packet_id, m.mpu_sequence_number): m
            for m in flow.mpu_objects()}


def _route_objects():
    media = reassemble(_load(ROUTE_DG))
    flow = next(f for f in media.flows if f.dst_key == ("239.255.32.1", 8321))
    return dict(flow.route_assembler.objects)


class TestDatagramDump:
    def test_roundtrip(self):
        dgs = _load(MMTP_DG)
        assert dgs and write_datagram_dump(dgs) == gzip.decompress(
            MMTP_DG.read_bytes())

    def test_reads_flow_endpoints(self):
        dgs = _load(MMTP_DG)
        assert all(d.dst_port == 8071 for d in dgs)
        assert all(len(d.dst_ip) == 4 for d in dgs)


class TestClassification:
    def test_mmtp_flow_is_detected(self):
        dgs = _load(MMTP_DG)
        assert classify_flow([d.payload for d in dgs]) == "mmtp"

    def test_route_flow_is_detected(self):
        dgs = _load(ROUTE_DG)
        assert classify_flow([d.payload for d in dgs]) == "route"

    def test_noise_is_unknown(self):
        rng = np.random.default_rng(0)
        payloads = [bytes(rng.integers(0, 256, 64, dtype=np.uint8))
                    for _ in range(20)]
        assert classify_flow(payloads) == "unknown"


class TestMpuReassembly:
    """MMTP MPU reassembly must match the reference receiver byte for byte."""

    def test_mpu_object_counts(self):
        objs = _mmtp_objects()
        # packet_id 12 video (120 samples) and 15 signaling-ish asset (1 sample)
        assert objs[(12, 228058002)].n_samples == 120
        assert objs[(15, 228058002)].n_samples == 1
        assert objs[(15, 228058002)].complete

    def test_oracle_byte_identity(self):
        objs = _mmtp_objects()
        for (pid, seq, kind), digest in ORACLE_SHA256.items():
            m = objs[(pid, seq)]
            data = m.meta if kind == "init" else m.body
            assert _sha(data) == digest, (pid, seq, kind)

    def test_partial_mpu_reports_shortfall(self):
        # The 3 s capture does not contain every packet of the 2 s video MPU.
        m = _mmtp_objects()[(12, 228058002)]
        assert not m.complete
        assert m.n_short > 0
        assert m.media_got < m.media_want

    def test_ft0_carries_mpuf_brand(self):
        meta = _mmtp_objects()[(12, 228058002)].meta
        boxes = {t for _o, _s, t in mmtp.iter_boxes(meta)}
        assert b"ftyp" in boxes and b"moov" in boxes
        assert b"mpuf" in meta


class TestMfuHeader:
    def test_fragmentation_info_and_sequence(self):
        dgs = _load(MMTP_DG)
        pkt = next(d for d in dgs
                   if mmtp.parse_mmtp_packet(d.payload).header.packet_id == 12
                   and mmtp.parse_mmtp_packet(d.payload).is_mpu)
        mpu = mmtp.parse_mpu_payload(
            mmtp.parse_mmtp_packet(pkt.payload).payload)
        assert mpu.mpu_sequence_number == 228058001
        assert mpu.fragment_type in (mmtp.FT_MPU_METADATA,
                                     mmtp.FT_MOVIE_FRAGMENT, mmtp.FT_MFU)
        assert mpu.fragmentation_indicator in (0, 1, 2, 3)

    def test_truncated_payload_rejected(self):
        with pytest.raises(mmtp.MmtpError):
            mmtp.parse_mpu_payload(b"\x18\x00\x28")

    def test_aggregation_expands_data_units(self):
        # A=1: payload_length(2) flags(1) counter(1) seq(4) + [len(2)+unit]...
        body = (b"\x00\x08" + bytes([0b00000001]) + b"\x00"
                + (7).to_bytes(4, "big")
                + (3).to_bytes(2, "big") + b"abc"
                + (3).to_bytes(2, "big") + b"def")
        body = (len(body) - 2).to_bytes(2, "big") + body[2:]
        mpu = mmtp.parse_mpu_payload(body)
        assert mpu.aggregation_flag
        assert mpu.data_units == (b"abc", b"def")


class TestMfuDataUnit:
    def test_du_header_split(self):
        du = (b"\x00\x00\x00\x01" + (42).to_bytes(4, "big")
              + (100).to_bytes(4, "big") + b"\x00\x00" + b"MEDIA")
        sample, offset, payload = mmtp.parse_mfu_data_unit(du)
        assert sample == 42 and offset == 100
        assert payload == b"MEDIA"

    def test_short_du_rejected(self):
        with pytest.raises(mmtp.MmtpError):
            mmtp.parse_mfu_data_unit(b"\x00" * 13)


class TestBoxWalk:
    def test_truncated_mdat_header_is_yielded(self):
        # FT=1 carries moof + only the mdat header: its declared size exceeds
        # the bytes present, but the size must still be observable.
        moof = (12).to_bytes(4, "big") + b"moof" + b"\x00" * 4
        mdat_hdr = (633846).to_bytes(4, "big") + b"mdat"
        boxes = mmtp.iter_boxes(moof + mdat_hdr)
        assert [t for _o, _s, t in boxes] == [b"moof", b"mdat"]
        assert boxes[-1][1] == 633846

    def test_zero_size_runs_to_end(self):
        data = (0).to_bytes(4, "big") + b"mdat" + b"\x01\x02\x03\x04"
        boxes = mmtp.iter_boxes(data)
        assert boxes == ((0, len(data), b"mdat"),)


class TestRouteReassembly:
    """ROUTE reassembly must match the reference receiver byte for byte."""

    def test_video_init_and_segment_hashes(self):
        objs = _route_objects()
        assert _sha(objs[(10, 4294967295)].reassemble()) == ROUTE_INIT_SHA256
        assert _sha(objs[(10, 228067348)].reassemble()) == ROUTE_SEG_SHA256

    def test_segment_boxes(self):
        boxes = route_segment_boxes(_route_objects()[(10, 228067348)].reassemble())
        assert [t for _o, _s, t in boxes] == [b"styp", b"moof", b"mdat"]


class TestSampleExtraction:
    def test_route_samples_sum_to_mdat(self):
        seg = _route_objects()[(10, 228067348)].reassemble()
        samples = iter_samples(seg)
        moof = mmtp.parse_mpu_moof(seg)
        assert len(samples) == len(moof.sample_sizes)
        assert sum(len(s) for s in samples) == sum(moof.sample_sizes)

    def test_mmtp_samples_sum_to_declared_media(self):
        m = _mmtp_objects()[(12, 228058002)]
        samples = iter_samples(m.body)
        moof = mmtp.parse_mpu_moof(m.body)
        assert sum(len(s) for s in samples) <= m.media_want
        assert len(samples) == len(moof.sample_sizes)

    def test_samples_are_hevc_nals(self):
        # A routed video sample is an HEVC access unit: 4-byte length-prefixed
        # NALs that must parse cleanly to the end.  The initial sample carries
        # the parameter sets (VPS 32 / SPS 33 / PPS 34).
        seg = _route_objects()[(10, 228067348)].reassemble()
        first = iter_samples(seg)[0]
        nal_types = []
        pos = 0
        while pos + 4 <= len(first):
            length = int.from_bytes(first[pos:pos + 4], "big")
            assert pos + 4 + length <= len(first)
            nal_types.append((first[pos + 4] >> 1) & 0x3F)
            pos += 4 + length
        assert pos == len(first)
        assert {32, 33, 34} <= set(nal_types)


def _mmtp_packet(pid, body):
    header = (bytes([0x40, 0x00]) + pid.to_bytes(2, "big")
              + b"\x00" * 4 + b"\x00" * 4 + b"\x18\x00")
    return header + body


def _mpu_body(seq, fragment_type=mmtp.FT_MFU):
    data_unit = b"\x00" * 4
    rest = (len(data_unit).to_bytes(2, "big") + data_unit)
    info = (fragment_type << 4) | 0b1000   # timed flag set
    payload = (bytes([info, 0]) + seq.to_bytes(4, "big") + rest)
    return (len(payload)).to_bytes(2, "big") + payload


class TestEndToEndPicture:
    """The reassembled ROUTE video decodes to a real HEVC picture.

    Uses PyAV (a declared dev/testing extra) when present; skipped otherwise.
    This is the rung's air gate: bytes on the wire become a 1920x1080 frame.
    """

    def test_route_video_decodes(self):
        av = pytest.importorskip("av")
        media = reassemble(_load(ROUTE_DG))
        tracks = extract_tracks(media)
        video_init = next(t for t in tracks
                          if t.track_id == 10 and t.init)
        video_samples = [s for t in tracks if t.track_id == 10 and t.samples
                         for s in t.samples]
        import tempfile
        import os
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, video_init.init)
        os.close(fd)
        try:
            container = av.open(path)
            extradata = container.streams.video[0].codec_context.extradata
        finally:
            os.unlink(path)
        ctx = av.CodecContext.create("hevc", "r")
        ctx.extradata = extradata
        frames = 0
        width = height = None
        for sample in video_samples:
            try:
                for frame in ctx.decode(av.Packet(sample)):
                    frames += 1
                    width, height = frame.width, frame.height
            except av.error.InvalidDataError:
                break
        assert frames > 50
        assert (width, height) == (1920, 1080)


class TestBounds:
    def test_mpu_object_cap_drops_and_counts(self):
        flow = mmtp.MmtpFlow(max_objects=1)
        flow.feed(_mmtp_packet(12, _mpu_body(1)))
        flow.feed(_mmtp_packet(12, _mpu_body(2)))
        assert flow.dropped == 1
        assert len(flow.objects()) == 1

    def test_sample_size_bound_rejects_huge_table(self):
        # A trun whose sample count exceeds MAX_MPU_SAMPLES is refused before
        # the table is built (the bound runs before the loop it bounds).
        def box(box_type, body):
            return (len(body) + 8).to_bytes(4, "big") + box_type + body
        tfhd = box(b"tfhd", b"\x00\x00\x00" + b"\x00" * 4)
        trun = box(b"trun", b"\x00\x00\x02\x00"
                   + (mmtp.MAX_MPU_SAMPLES + 1).to_bytes(4, "big"))
        traf = box(b"traf", tfhd + trun)
        mfmd = box(b"moof", box(b"mfhd", b"\x00" * 8) + traf)
        moof = mmtp.parse_mpu_moof(mfmd)
        assert moof.sample_sizes == ()
        assert moof.sizes_rejected == mmtp.MAX_MPU_SAMPLES + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
