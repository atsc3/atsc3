"""AC-4 audio bridge tests (``atsc3lib.audio``) on the RF33 off-air capture.

The fixture is the MMTP media flow whose track 13 is AC-4 (``stsd`` ``ac-4``).
The gate is that the track decodes to six equal-length PCM channels, the A-SPX
frames are counted, and a WAV round-trips -- the same asset the independent
reference receiver renders.
"""

import gzip
import wave
from pathlib import Path

import numpy as np
import pytest

from atsc3lib import audio
from atsc3lib.media import extract_tracks, read_datagram_dump, reassemble

DATA = Path(__file__).parent / "data"
MMTP_DG = DATA / "mmtp_media_flow_8071.dg.gz"
ROUTE_DG = DATA / "route_media_flow_8321.dg.gz"


@pytest.fixture(scope="module")
def ac4_track():
    dg = read_datagram_dump(gzip.decompress(MMTP_DG.read_bytes()))
    for track in extract_tracks(reassemble(dg)):
        if track.complete and audio.is_ac4(track):
            return track
    pytest.skip("no complete AC-4 track in the fixture")


class TestAudio:
    def test_decode(self, ac4_track):
        decoded = audio.decode_track(ac4_track)
        assert decoded is not None
        assert decoded.sample_rate == 48000
        assert decoded.channels == ("L", "R", "C", "lfe", "Ls", "Rs")
        assert decoded.frame_count > 0
        assert decoded.stats.decoded > 0
        assert decoded.stats.aspx_frames == decoded.stats.decoded
        for name in decoded.channels:
            assert np.isfinite(decoded.pcm[name]).all(), name
        lengths = {len(v) for v in decoded.pcm.values()}
        assert len(lengths) == 1, lengths

    def test_non_ac4_is_none(self, ac4_track):
        class _Other:
            init = b"\x00\x00\x00\x18stsd\x00\x00\x00\x00hvc1"
            samples = ()
        assert audio.decode_track(_Other()) is None

    def test_decode_media(self):
        dg = read_datagram_dump(gzip.decompress(MMTP_DG.read_bytes()))
        decoded = audio.decode_media(reassemble(dg))
        assert [d.track_id for d in decoded] == [13, 14]
        by_id = {d.track_id: d for d in decoded}
        assert by_id[13].channels == ("L", "R", "C", "lfe", "Ls", "Rs")
        assert by_id[14].channels == ("L", "R")

    def test_wav_round_trip(self, ac4_track, tmp_path):
        decoded = audio.decode_track(ac4_track)
        path = str(tmp_path / "ac4.wav")
        audio.write_wav(path, decoded)
        with wave.open(path) as w:
            assert w.getnchannels() == 6
            assert w.getframerate() == 48000
            assert w.getnframes() == decoded.frame_count

    def test_normalized_pcm_does_not_clip(self, ac4_track):
        # The decoder emits integer-scale samples; normalized_pcm scales the
        # shared peak to WAV_PEAK so the WAV and the mux cannot clip.
        planar, gain = audio.normalized_pcm(audio.decode_track(ac4_track))
        assert planar.shape[0] == 6
        assert np.isclose(np.abs(planar).max(), audio.WAV_PEAK)
        assert int((np.abs(planar) > 1.0).sum()) == 0

    def test_normalized_pcm_silent_gain_one(self):
        silent = audio.DecodedAudio(
            track_id=1, sample_rate=48000,
            pcm={"L": np.zeros(10), "R": np.zeros(10)}, stats=None)
        planar, gain = audio.normalized_pcm(silent)
        assert gain == 1.0
        assert planar.shape == (2, 10)


class TestRouteAudio:
    """ROUTE delivers a track's ``ac-4`` init (TOI 0xFFFFFFFF) and its samples
    as SEPARATE objects, so pairing by TSI is what makes them decodable; the
    samples-only track has no init and cannot be recognised as AC-4 on its own.
    The RF33 ROUTE lanes (TSI 20 and 30) are stereo ``channel_pair_element``."""

    def test_decode_media_pairs_route_init(self):
        dg = read_datagram_dump(gzip.decompress(ROUTE_DG.read_bytes()))
        decoded = audio.decode_media(reassemble(dg))
        assert [d.track_id for d in decoded] == [20, 30]
        for d in decoded:
            assert d.channels == ("L", "R")
            assert d.sample_rate == 48000
            assert d.frame_count > 0
            assert d.stats.decoded > 0
            for name in d.channels:
                assert np.isfinite(d.pcm[name]).all(), name
            lengths = {len(v) for v in d.pcm.values()}
            assert len(lengths) == 1, lengths

    def test_route_wav_round_trip(self, tmp_path):
        dg = read_datagram_dump(gzip.decompress(ROUTE_DG.read_bytes()))
        decoded = audio.decode_media(reassemble(dg))[0]
        path = str(tmp_path / "route_ac4.wav")
        audio.write_wav(path, decoded)
        with wave.open(path) as w:
            assert w.getnchannels() == 2
            assert w.getframerate() == 48000
            assert w.getnframes() == decoded.frame_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
