"""Tests for muxing decoded AC-4 audio with the video track (``atsc3lib.mux``).

The broadcast ``soun`` track holds raw ``ac-4`` frames no player decodes, so
the combined file is what an operator actually opens.  The gate is on the RF33
off-air ROUTE fixture: open the built video track and the decoded AC-4 track,
mux them, and reopen the result to find both a HEVC video stream and an AAC
audio stream with real (non-silent, finite) samples.
"""

import gzip
from pathlib import Path

import numpy as np
import pytest

from atsc3lib import audio, mp4, mux
from atsc3lib.media import read_datagram_dump, reassemble

DATA = Path(__file__).parent / "data"
ROUTE_DG = DATA / "route_media_flow_8321.dg.gz"


def _route():
    return reassemble(read_datagram_dump(gzip.decompress(ROUTE_DG.read_bytes())))


def _video():
    return mp4.select_track(mp4.build_tracks(_route()), b"vide")


class TestSelectAudio:
    def test_longest_by_default(self):
        dg = _route()
        decoded = audio.decode_media(dg)
        chosen = mux.select_audio(decoded)
        assert chosen is not None
        assert chosen.frame_count == max(d.frame_count for d in decoded)

    def test_explicit_track(self):
        decoded = audio.decode_media(_route())
        chosen = mux.select_audio(decoded, track_id=30)
        assert chosen is not None and chosen.track_id == 30

    def test_unknown_track_is_none(self):
        assert mux.select_audio(audio.decode_media(_route()), track_id=999) is None

    def test_empty_is_none(self):
        assert mux.select_audio([]) is None


@pytest.mark.skipif(not ROUTE_DG.exists(), reason="ROUTE fixture not present")
class TestMuxAv:
    def test_writes_playable_av(self, tmp_path):
        av = pytest.importorskip("av")
        video = _video()
        decoded = audio.decode_media(_route())
        out = str(tmp_path / "av.mp4")
        assert mux.mux_av(video, decoded, out) == out

        container = av.open(out)
        streams = {s.type: s for s in container.streams}
        assert "video" in streams and "audio" in streams
        assert streams["video"].codec_context.name == "hevc"
        assert streams["audio"].codec_context.name == "aac"
        assert (streams["video"].width, streams["video"].height) == (1920, 1080)

        video_packets = 0
        for packet in container.demux(video=0):
            if packet.size:
                video_packets += 1
        assert video_packets == video.samples

    def test_output_is_fragmented_for_streaming(self, tmp_path):
        # The combined file must be a fragmented MP4 (empty moov up front, then
        # moof/mdat) so a player can open it while it is still being written and
        # a truncated live file remains valid past its last fragment.
        pytest.importorskip("av")
        video = _video()
        decoded = audio.decode_media(_route())
        out = str(tmp_path / "av.mp4")
        mux.mux_av(video, decoded, out)
        data = Path(out).read_bytes()
        assert data[4:8] == b"ftyp"
        moov = data.find(b"moov")
        moof = data.find(b"moof")
        assert moov != -1 and moof != -1
        assert moov < moof

    def test_audio_is_non_silent(self, tmp_path):
        av = pytest.importorskip("av")
        video = _video()
        decoded = audio.decode_media(_route())
        out = str(tmp_path / "av.mp4")
        mux.mux_av(video, decoded, out)

        container = av.open(out)
        astream = container.streams.audio[0]
        ctx = av.CodecContext.create(astream.codec_context.name, "r")
        ctx.extradata = astream.codec_context.extradata
        peak = 0.0
        clipped = 0
        for packet in container.demux(audio=0):
            if packet.size == 0:
                continue
            for frame in ctx.decode(packet):
                arr = np.abs(frame.to_ndarray().astype(np.float64))
                peak = max(peak, float(arr.max()))
                clipped += int((arr >= 0.999).sum())
        assert 0.0 < peak <= 1.0
        # The decoder emits integer-scale samples; feeding them raw clips the
        # encoder (audible scratching).  The mux must normalise.
        assert clipped == 0

    def test_no_audio_returns_none(self, tmp_path):
        video = _video()
        out = str(tmp_path / "av.mp4")
        assert mux.mux_av(video, [], out) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
