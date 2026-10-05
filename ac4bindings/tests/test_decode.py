"""Whole-track decode tests (``ac4bindings.decode``).

The gate is the on-air RF33 AC-4 asset: all six channels render to equal-length
PCM, the A-SPX frames are counted, and non-v2 frames are skipped rather than
mis-decoded.  The high-band behaviour itself is gated by ``test_aspx``; here we
check the packaging the callers rely on.
"""

from pathlib import Path

import numpy as np
import pytest

from ac4bindings import decode

DATA = Path(__file__).parent / "data"
FIXTURE = "ac4_frames_mmtp_pid13.bin"


@pytest.mark.skipif(not (DATA / FIXTURE).exists(),
                    reason="on-air AC-4 fixture not present")
class TestDecode:
    def test_track(self):
        track = decode.decode_track((DATA / FIXTURE).read_bytes())
        assert track.stats.decoded > 0
        assert track.stats.aspx_frames == track.stats.decoded
        for name in ("L", "R", "C", "lfe", "Ls", "Rs"):
            assert name in track.pcm, name
            assert np.isfinite(track.pcm[name]).all(), name
        lengths = {len(v) for v in track.pcm.values()}
        assert len(lengths) == 1, lengths

    def test_skips_non_v2(self):
        # A frame whose version is not 2 must be counted, not decoded.
        frame = b"\x00" * 8
        track = decode.Decoder().decode([frame])
        assert track.stats.total == 1
        assert track.stats.skipped_error == 1
        assert track.stats.decoded == 0


#: The RF33 stereo lanes: the MMTP Spanish simulcast and the two ROUTE audio
#: objects.  All are ``channel_pair_element`` (TOC channel_mode 1), which is a
#: different element from pid13's 5.X.
STEREO_FIXTURES = [
    "ac4_frames_mmtp_pid14.bin",
    "ac4_frames_route_tsi20.bin",
    "ac4_frames_route_tsi30.bin",
]


@pytest.mark.parametrize("fixture", STEREO_FIXTURES)
class TestStereoPairDecode:
    def test_track(self, fixture):
        if not (DATA / fixture).exists():
            pytest.skip(f"{fixture} not present")
        track = decode.decode_track((DATA / fixture).read_bytes())
        assert track.stats.decoded > 0
        assert set(track.pcm) == {"L", "R"}
        lengths = {len(v) for v in track.pcm.values()}
        assert len(lengths) == 1, lengths
        assert np.isfinite(track.pcm["L"]).all()
        assert np.isfinite(track.pcm["R"]).all()
