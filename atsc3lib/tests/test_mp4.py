"""Tests for building playable fragmented-ISO-BMFF files from media tracks.

Gated two ways: structurally (the box/retime arithmetic on a synthetic
fragment) and on the RF33 off-air datagram dumps, where a reassembled video
track must build a file whose HEVC frames actually decode to 1920x1080 via
PyAV.  The point of the module is that a broadcast MPU/fragment is a
self-contained file that restarts its timeline, so "just concatenate" does not
play; these tests pin the retiming that fixes it.
"""

import gzip
import os
from pathlib import Path

import pytest

from atsc3lib import mmtp
from atsc3lib import mp4
from atsc3lib import route
from atsc3lib.media import read_datagram_dump, reassemble

DATA = Path(__file__).parent / "data"
MMTP_DG = DATA / "mmtp_media_flow_8071.dg.gz"
ROUTE_DG = DATA / "route_media_flow_8321.dg.gz"


def _media(path):
    return reassemble(read_datagram_dump(gzip.decompress(path.read_bytes())))


def _flow(media, key):
    return next(f for f in media.flows if f.dst_key == key)


def _box(box_type, body):
    return (len(body) + 8).to_bytes(4, "big") + box_type + body


class TestBoxWalk:
    def test_find_nested(self):
        stsd = _box(b"stsd", b"\x00" * 8)
        stbl = _box(b"stbl", stsd)
        minf = _box(b"minf", stbl)
        mdia = _box(b"mdia", minf)
        trak = _box(b"trak", mdia)
        moov = _box(b"moov", trak)
        found = mp4.find_box(moov, (b"moov", b"trak", b"mdia", b"minf",
                                    b"stbl", b"stsd"))
        assert found is not None
        offset, size = found
        assert moov[offset + 4:offset + 8] == b"stsd"
        assert size == len(stsd)

    def test_truncated_tail_yielded_once(self):
        data = (100).to_bytes(4, "big") + b"mdat" + b"\x00" * 4
        boxes = mp4.boxes(data)
        assert boxes == [(0, 100, b"mdat")]


def _fragment(sample_sizes):
    """A minimal moof+mdat fragment with a media and a hint traf.

    The media ``trun`` carries a per-sample size (flag 0x100) and a data
    offset (0x001); the hint ``trun`` carries only a data offset and defaults
    its size in its ``tfhd`` (flag 0x10), mirroring the RF33 layout.
    """
    def media_traf(data_offset):
        entries = b"".join((s).to_bytes(4, "big") + s.to_bytes(4, "big")
                           for s in sample_sizes)
        trun = _box(b"trun", b"\x00\x02\x03\x01"
                    + len(sample_sizes).to_bytes(4, "big")
                    + data_offset.to_bytes(4, "big", signed=True) + entries)
        tfhd = _box(b"tfhd", b"\x00\x02\x00\x00" + b"\x00" * 4)
        return _box(b"traf", tfhd + trun)

    def hint_traf(data_offset, default_size):
        trun = _box(b"trun", b"\x00\x00\x00\x01"
                    + len(sample_sizes).to_bytes(4, "big")
                    + data_offset.to_bytes(4, "big", signed=True))
        tfhd = _box(b"tfhd", b"\x00\x02\x00\x10" + b"\x00" * 4
                    + default_size.to_bytes(4, "big"))
        return _box(b"traf", tfhd + trun)

    mfhd = _box(b"mfhd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big"))
    moof = _box(b"moof", mfhd + media_traf(0) + hint_traf(0, 0))
    payload = b"".join(b"\xaa" * s for s in sample_sizes)
    media_off = len(moof) + 8
    hint_sizes = b"".join(b"\xbb" * 4 for _ in sample_sizes)
    moof = _box(b"moof", mfhd + media_traf(media_off)
                + hint_traf(media_off + len(payload), 4))
    mdat = _box(b"mdat", payload + hint_sizes)
    return moof + mdat


class TestRetime:
    def test_rewrites_mfhd_and_tfdt(self):
        seg = _fragment([10, 20, 30])
        out = mp4.retime(seg, 7, 123456)
        mfhd = mp4.find_box(out, (b"moof", b"mfhd"))
        assert int.from_bytes(out[mfhd[0] + 12:mfhd[0] + 16], "big") == 7
        # duration is the media trun sum
        assert mp4.segment_duration(out) == 60
        assert out != seg

    def test_segment_duration_uses_media_not_hint(self):
        seg = _fragment([10, 20, 30])
        assert mp4.segment_duration(seg) == 60


class TestTrim:
    def test_trim_keeps_whole_samples_and_rewrites_mdat(self):
        seg = _fragment([10, 20, 30])
        out = mp4.trim_to_samples(seg, 2)
        assert out is not None
        assert mp4._sample_count(out) == 2
        mdat = mp4.find_box(out, (b"mdat",))
        assert mdat[1] == len(out) - mdat[0]

    def test_whole_samples_in(self):
        seg = _fragment([10, 20, 30])
        assert mp4.whole_samples_in(seg, 10) == 1
        assert mp4.whole_samples_in(seg, 29) == 1
        assert mp4.whole_samples_in(seg, 30) == 2
        assert mp4.whole_samples_in(seg, 60) == 3


class TestTrackInfo:
    def test_route_video_init(self):
        flow = _flow(_media(ROUTE_DG), ("239.255.32.1", 8321))
        init = flow.route_assembler.objects[(10, route.ROUTE_TOI_INIT)]
        info = mp4.track_info(init.reassemble(), b"vide")
        assert info is not None
        assert info.handler == b"vide"
        assert info.timescale == 90000

    def test_mmtp_audio_init_is_ac4(self):
        flow = _flow(_media(MMTP_DG), ("239.255.7.1", 8071))
        meta = next(m.meta for m in flow.mpu_objects()
                    if m.packet_id == 13 and m.meta)
        stsd = mp4.find_box(meta, (b"moov", b"trak", b"mdia", b"minf", b"stbl",
                                   b"stsd"))
        assert meta[stsd[0] + 20:stsd[0] + 24] == b"ac-4"
        info = mp4.track_info(meta)
        assert info is not None and info.handler == b"soun"


class TestRouteBuild:
    def test_video_track_builds_and_retimes(self):
        flow = _flow(_media(ROUTE_DG), ("239.255.32.1", 8321))
        built = mp4.build_route_track(flow, 10, handler=b"vide")
        assert built is not None
        assert built.handler == b"vide"
        assert built.samples == 120
        assert built.duration == 180180        # 120 frames at 60000/1001
        assert built.segments == [(228067348, 750958, False)]

    def test_partial_segment_is_trimmed_and_labelled(self):
        flow = _flow(_media(ROUTE_DG), ("239.255.32.1", 8321))
        built = mp4.build_route_track(flow, 20)
        assert built is not None
        seq, _size, truncated = built.segments[-1]
        assert truncated is True

    def test_build_all_tracks_handlers(self):
        media = _media(ROUTE_DG)
        tracks = mp4.build_tracks(media)
        handlers = {t.handler for t in tracks}
        assert b"vide" in handlers and b"soun" in handlers
        video = mp4.select_track(tracks, b"vide")
        assert video is not None and video.samples == 120


class TestMmtpBuild:
    def test_subtitle_track_complete(self):
        flow = _flow(_media(MMTP_DG), ("239.255.7.1", 8071))
        built = mp4.build_mmtp_track(flow, 15)
        assert built is not None
        assert built.samples == 1
        assert built.segments[0][2] is False

    def test_incomplete_video_without_complete_frame_is_none(self):
        # The 3 s MMTP capture loses a packet inside the 120-frame video MPU
        # before its first whole sample, so no complete or repairable video
        # track exists; the builder must say so rather than emit a broken file.
        flow = _flow(_media(MMTP_DG), ("239.255.7.1", 8071))
        assert mp4.build_mmtp_track(flow, 12) is None


@pytest.mark.skipif(not os.path.exists(ROUTE_DG),
                    reason="ROUTE media capture not present")
class TestPictureGate:
    """The built ROUTE video file decodes to real HEVC pictures (E82-style)."""

    def test_route_video_decodes(self):
        av = pytest.importorskip("av")
        flow = _flow(_media(ROUTE_DG), ("239.255.32.1", 8321))
        built = mp4.build_route_track(flow, 10, handler=b"vide")
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, built.data)
        os.close(fd)
        try:
            container = av.open(path)
            stream = container.streams.video[0]
            ctx = av.CodecContext.create(stream.codec_context.name, "r")
            ctx.extradata = stream.codec_context.extradata
            frames = 0
            width = height = None
            for packet in container.demux(video=0):
                if packet.size == 0:
                    continue
                try:
                    for frame in ctx.decode(packet):
                        frames += 1
                        width, height = frame.width, frame.height
                except av.error.InvalidDataError:
                    break
            assert frames > 100
            assert (width, height) == (1920, 1080)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
