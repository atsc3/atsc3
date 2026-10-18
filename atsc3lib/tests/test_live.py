"""Tests for the bounded live receive loop.

The decode path is exercised on the real RF33 air slice via
:class:`ArrayIqSource`; the source abstractions are tested directly.  These
tests assert the loop is *bounded* (finite windows/frames per call) and that a
miss advances rather than scanning.
"""

import gzip
import os

import numpy as np
import pytest

from atsc3lib.live import (
    ACQUIRE_WINDOW_S,
    DEFAULT_MAX_DATAGRAMS,
    DEFAULT_MAX_WINDOWS,
    ArrayIqSource,
    FileIqSource,
    IqSource,
    LiveConfig,
    LiveMediaSink,
    LiveReceiver,
    SdrplayIqSource,
)

_DATA = os.path.join(os.path.dirname(__file__), "data")
_SLICE = os.path.join(_DATA, "rf33_acquire_slice.npy")
_LOCK = os.path.join(_DATA, "rf33_live_lock.cs8")
_ROUTE_DG = os.path.join(_DATA, "route_media_flow_8321.dg.gz")


class TestIqSource:
    def test_array_source_windows(self):
        iq = np.arange(10, dtype=np.complex64)
        src = ArrayIqSource(iq, 10e6)
        assert src.read(4).size == 4
        assert src.read(4).size == 4
        assert src.read(4).size == 2
        assert src.read(4) is None

    def test_file_source_roundtrip(self, tmp_path):
        samples = (np.arange(2000, dtype=np.int16) - 1024).astype(np.int16)
        inter = np.empty(samples.size * 2, dtype=np.int16)
        inter[0::2] = samples
        inter[1::2] = samples
        path = tmp_path / "iq.cs16"
        path.write_bytes(inter.tobytes())
        src = FileIqSource(str(path), 10e6, "cs16")
        first = src.read(1000)
        second = src.read(1000)
        assert first.size == 1000 and second.size == 1000
        assert src.read(1000) is None

    def test_file_source_rejects_unknown_format(self, tmp_path):
        path = tmp_path / "x"
        path.write_bytes(b"\x00" * 16)
        with pytest.raises(ValueError):
            FileIqSource(str(path), 10e6, "nope")

    def test_sdrplay_source_is_iqsource(self):
        assert issubclass(SdrplayIqSource, IqSource)


class TestLiveConfig:
    def test_defaults_are_finite(self):
        c = LiveConfig()
        assert c.max_windows > 0
        assert c.max_frames > 0
        assert c.acquire_window_s == ACQUIRE_WINDOW_S
        assert DEFAULT_MAX_WINDOWS > 0


@pytest.mark.skipif(not os.path.exists(_SLICE),
                    reason="real-air capture slice not present")
class TestLiveLoopOnAir:
    """The loop locks and decodes the real RF33 slice (PLP-16)."""

    def test_locks_and_decodes(self):
        iq = np.load(_SLICE)
        config = LiveConfig(plp_id=16, max_frames=1)
        receiver = LiveReceiver(ArrayIqSource(iq, 10e6), config)
        receiver.run(max_windows=1)
        assert receiver.stats.acquisitions >= 1
        assert receiver.stats.frames_decoded >= 1
        assert receiver.stats.payloads >= 1
        assert len(receiver.streams) >= 1

    def test_on_streams_callback_receives_datagrams(self):
        iq = np.load(_SLICE)
        seen = []
        config = LiveConfig(plp_id=16, max_frames=1)
        receiver = LiveReceiver(ArrayIqSource(iq, 10e6), config,
                                on_streams=seen.append)
        receiver.run(max_windows=1)
        assert seen and receiver.streams == []

    def test_run_is_bounded_on_endless_noise(self):
        # A source that never ends but is not a signal: run() must still
        # return after a finite number of windows.  The source reports the
        # bootstrap rate and under-delivers short reads, so each miss is a
        # cheap ValueError rather than a full correlation.
        class EndlessNoise(IqSource):
            sample_rate = 6_144_000
            sample_format = "cf32"

            def __init__(self):
                self.reads = 0

            def read(self, n_samples):
                self.reads += 1
                rng = np.random.default_rng(self.reads)
                return (rng.normal(0, 0.05, 4000)
                        + 1j * rng.normal(0, 0.05, 4000)).astype(
                            np.complex64)

        src = EndlessNoise()
        receiver = LiveReceiver(src, LiveConfig(max_windows=1))
        receiver.run(max_windows=3)
        assert receiver.stats.windows == 3
        assert receiver.stats.acquisitions == 0
        assert receiver.stats.lock_failures == 3
        assert src.reads == 3

    def test_run_stops_at_source_end(self):
        src = ArrayIqSource(np.zeros(10, dtype=np.complex64), 6_144_000)
        receiver = LiveReceiver(src, LiveConfig(max_windows=5))
        receiver.run()
        # One (short) window is read and fails acquisition; the source then
        # ends, so the loop stops rather than spinning.
        assert receiver.stats.windows == 1
        assert receiver.stats.acquisitions == 0
        assert receiver.run().windows == 1

    def test_config_rejects_nonpositive_bounds(self):
        with pytest.raises(ValueError):
            LiveConfig(max_windows=0)
        with pytest.raises(ValueError):
            LiveConfig(max_frames=0)


class TestLiveLockPersistence:
    """The lock is held across windows: acquire once, then stream frames.

    Uses a real 0.7 s RF33 capture (int8, 10 MS/s).  A per-window re-acquire
    would show one acquisition per window; the held lock shows exactly one
    across all of them.
    """

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_lock_is_held_across_windows(self):
        receiver = LiveReceiver(
            FileIqSource(_LOCK, 10e6, "cs8"),
            LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10,
                       relock_every=10))
        for _ in range(10):
            if not receiver.run_once():
                break
        s = receiver.stats
        assert s.windows >= 5
        assert s.acquisitions == 1          # locked once, not once per window
        assert s.lock_failures == 0
        assert s.relocks == 0
        assert s.frames_decoded >= 2        # successive frames off the held lock

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_stream_buffer_is_bounded(self):
        receiver = LiveReceiver(
            FileIqSource(_LOCK, 10e6, "cs8"),
            LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10))
        for _ in range(10):
            if not receiver.run_once():
                break
            if receiver._lock is not None:
                # Frame period 1.7 MSamples + one window (0.1 s -> 0.7 MSamples)
                # plus the ~14-sample filter tail; never grows without bound.
                assert receiver._lock.main.size <= 2_600_000

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_pipelined_matches_synchronous(self):
        # The capture-overlapped runner must produce exactly the same decode as
        # the synchronous loop over the same windows.
        def cfg():
            return LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10)

        sync = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        for _ in range(10):
            if not sync.run_once():
                break
        pipe = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        pipe.run_pipelined(max_windows=10, queue_depth=2)
        assert pipe.stats == sync.stats
        assert len(pipe.streams) == len(sync.streams)

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_pipelined_fec_async_matches_synchronous(self):
        def cfg():
            return LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10)

        sync = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        for _ in range(10):
            if not sync.run_once():
                break
        pipe = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        pipe.run_pipelined(max_windows=10, queue_depth=2, fec_async=True)
        assert pipe.stats == sync.stats
        assert len(pipe.streams) == len(sync.streams)

    def test_pipelined_rejects_bad_depth(self):
        receiver = LiveReceiver(ArrayIqSource(np.zeros(10, dtype=np.complex64),
                                              10e6))
        with pytest.raises(ValueError):
            receiver.run_pipelined(max_windows=1, queue_depth=0)

    def test_config_rejects_bad_network_queue(self):
        with pytest.raises(ValueError):
            LiveConfig(network_queue=0)

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_async_network_matches_synchronous(self):
        # Running the ALP/IP/LLS stage on its own worker must give the same
        # decode as doing it inline.
        def cfg():
            return LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10)

        sync = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        for _ in range(10):
            if not sync.run_once():
                break
        pipe = LiveReceiver(FileIqSource(_LOCK, 10e6, "cs8"), cfg())
        pipe.run_pipelined(max_windows=10, queue_depth=2, network_async=True)
        assert pipe.stats == sync.stats
        assert len(pipe.streams) == len(sync.streams)

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_async_network_reports_worker_failure(self, monkeypatch):
        # A failure in the network worker must surface on close, not vanish.
        from atsc3lib import live as live_mod

        def boom(_payload):
            raise RuntimeError("network stage failed")

        monkeypatch.setattr(live_mod, "decode_streams", boom)
        receiver = LiveReceiver(
            FileIqSource(_LOCK, 10e6, "cs8"),
            LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10))
        with pytest.raises(RuntimeError, match="network stage failed"):
            receiver.run_pipelined(max_windows=10, queue_depth=2,
                                   network_async=True)

    @pytest.mark.skipif(not os.path.exists(_LOCK),
                        reason="real lock-persistence capture not present")
    def test_config_change_triggers_relock(self, monkeypatch):
        # Emulate a multiplex reconfiguration detected at the periodic L1
        # re-check: the held lock must be replaced, not silently reused.
        from atsc3lib import live as live_mod
        real = live_mod._config_fingerprint
        state = {"n": 0}

        def changed(result):
            state["n"] += 1
            if state["n"] == 2:             # the first re-check sees a change
                return ("reconfigured",)
            return real(result)

        monkeypatch.setattr(live_mod, "_config_fingerprint", changed)
        receiver = LiveReceiver(
            FileIqSource(_LOCK, 10e6, "cs8"),
            LiveConfig(plp_id=16, max_frames=1, acquire_window_s=0.10,
                       relock_every=2))
        for _ in range(6):
            if not receiver.run_once():
                break
        assert receiver.stats.relocks >= 1
        assert receiver.stats.acquisitions >= 1


class TestLiveMediaSink:
    """The sink is the live path's media output: it collects a bounded run's
    datagrams and reassembles them into per-track MP4s, WAVs and one A/V MP4.
    """

    class _Streams:
        def __init__(self, datagrams):
            self.datagrams = datagrams

    def _route_datagrams(self):
        from atsc3lib.media import read_datagram_dump
        with open(_ROUTE_DG, "rb") as fh:
            return read_datagram_dump(gzip.decompress(fh.read()))

    def test_collects_and_caps(self):
        dg = self._route_datagrams()
        sink = LiveMediaSink("/tmp/unused", max_datagrams=10)
        sink(self._Streams(dg))
        assert len(sink.datagrams) == 10
        assert sink.dropped == len(dg) - 10

    def test_build_empty_is_noop(self):
        assert LiveMediaSink("/tmp/unused").build() == []

    @pytest.mark.skipif(not os.path.exists(_ROUTE_DG),
                        reason="ROUTE media capture not present")
    def test_build_writes_av_files(self, tmp_path):
        pytest.importorskip("av")
        sink = LiveMediaSink(str(tmp_path))
        sink(self._Streams(self._route_datagrams()))
        written = sink.build()
        names = {os.path.basename(p) for p in written}
        assert any(n.endswith("_vide.mp4") for n in names)
        assert any(n.endswith("_soun.wav") for n in names)
        assert any(n.endswith("_av.mp4") for n in names)
        for path in written:
            assert os.path.getsize(path) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
