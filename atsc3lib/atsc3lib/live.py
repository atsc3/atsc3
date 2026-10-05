"""Bounded live receive loop (A/322 acquisition -> A/330/A/331 streams).

The wire receiver is a *window* loop, not a "read until it works" loop: it
takes a finite span of IQ, tries to acquire a bootstrap within it (A/322
7.2.2.2: a bootstrap recurs at least every minimum frame), and on a miss
advances to the next window — it never scans unbounded.  Once locked, the L1
configuration is stable for the multiplex, so each subsequent frame's PLP
payload is decoded from the same lock.

This module is deliberately transport-agnostic: an :class:`IqSource` yields
finite windows, so the identical loop runs live (``sdrbindings``) and offline
(a saved capture), and is testable without hardware.

Honest limitation: the pure-Python decoders are far slower than the air rate,
so a live run advances in slow motion and drops windows between decodes.  The
loop is correct and bounded; keeping up needs accelerated hot paths, which is
the next rung.  Bounds are all explicit and spec-derived; nothing here scans or
buffers without a limit.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from . import spec
from .receiver import (
    _bootstrap_to_preamble,
    decode_plp_frame,
    decode_signaling,
    select_plp,
)
from .payload import decode_streams


#: Maximum datagrams held by a :class:`LiveMediaSink` before it stops
#: accumulating.  Media reassembly is already bounded per object by its
#: signalled length; this caps the number of objects a live run can collect so
#: a long run cannot grow without limit.
DEFAULT_MAX_DATAGRAMS = 8192


#: IQ samples per acquisition window at the bootstrap rate: two minimum frame
#: periods (A/322 7.2.2.2).  The wire default; a caller may widen it but it is
#: always finite.
ACQUIRE_WINDOW_S = 2 * spec.MIN_FRAME_LENGTH_S

#: Default cap on acquisition windows per :meth:`LiveReceiver.run` call.  A
#: live source never ends, so ``run`` is bounded by default; :meth:`run_once`
#: advances exactly one window for a caller that wants its own loop.
DEFAULT_MAX_WINDOWS = 8

#: Default cap on frames decoded after one lock (bounded work per call).
DEFAULT_MAX_FRAMES = 16


class IqSource:
    """A source of finite IQ windows (live or file)."""

    sample_rate: float
    sample_format: str

    def read(self, n_samples: int) -> Optional[np.ndarray]:
        """Return up to ``n_samples`` complex samples, or None at end."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class ArrayIqSource(IqSource):
    """A finite in-memory IQ array, read in windows (for tests/replay)."""

    def __init__(self, iq: np.ndarray, sample_rate: float,
                 sample_format: str = "cf32"):
        self._iq = np.asarray(iq, dtype=np.complex64)
        self._pos = 0
        self.sample_rate = sample_rate
        self.sample_format = sample_format

    def read(self, n_samples: int) -> Optional[np.ndarray]:
        if self._pos >= self._iq.size:
            return None
        chunk = self._iq[self._pos:self._pos + n_samples]
        self._pos += chunk.size
        return chunk


class FileIqSource(IqSource):
    """A saved interleaved IQ file read in windows (streams, never loads all).

    The file format matches :func:`atsc3lib.receiver._read_iq`: cs8/int8,
    cs16/int16 or cf32/float32.  Reads are bounded by the requested window, so
    a long capture is not held in memory.
    """

    def __init__(self, path: str, sample_rate: float, fmt: str = "cs16",
                 start_sample: int = 0):
        self.path = path
        self.sample_rate = sample_rate
        self.sample_format = fmt
        self._dtype = {"cs8": np.int8, "int8": np.int8, "cs16": np.int16,
                       "int16": np.int16, "cf32": np.float32,
                       "float32": np.float32}.get(fmt)
        if self._dtype is None:
            raise ValueError(f"unknown sample format {fmt!r}")
        self._items_per_sample = 2
        self._byte_offset = start_sample * self._items_per_sample * \
            np.dtype(self._dtype).itemsize
        self._exhausted = False

    def read(self, n_samples: int) -> Optional[np.ndarray]:
        if self._exhausted:
            return None
        raw = np.fromfile(self.path, dtype=self._dtype,
                          count=n_samples * self._items_per_sample,
                          offset=self._byte_offset)
        self._byte_offset += raw.nbytes
        if raw.size == 0:
            self._exhausted = True
            return None
        if raw.size < n_samples * self._items_per_sample:
            self._exhausted = True
        if self._dtype is np.int16:
            return ((raw[::2] + 1j * raw[1::2]) / 32768.0).astype(np.complex64)
        if self._dtype is np.int8:
            return ((raw[::2].astype(np.float32)
                     + 1j * raw[1::2].astype(np.float32)) / 128.0)
        return (raw[::2] + 1j * raw[1::2]).astype(np.complex64)


class SdrplayIqSource(IqSource):
    """A live SDRplay IQ source, streamed through ``sdrbindings``."""

    def __init__(self, freq_hz: float, sample_rate: float = 10e6,
                 bandwidth_hz: float = 8e6, ifgr: float = 40,
                 rfgr: float = 4, driver: str = "sdrplay", index: int = 0):
        import sdrbindings
        self._bindings = sdrbindings
        self.sample_rate = sample_rate
        self.sample_format = "cs16"
        self._dev = sdrbindings.Device(driver, index=index)
        self._dev.set_sample_rate(sample_rate)
        self._dev.set_frequency(freq_hz)
        self._dev.set_bandwidth(bandwidth_hz)
        self._dev.set_antenna(sdrbindings.RX_ANTENNA)
        if self._dev.has_gain_mode():
            self._dev.set_gain_mode(False)
        self._dev.set_gain_element(sdrbindings.IFGR, float(ifgr))
        self._dev.set_gain_element(sdrbindings.RFGR, float(rfgr))
        self._dev.setup_stream(sdrbindings.CS16)
        self._dev.activate_stream()

    def read(self, n_samples: int) -> Optional[np.ndarray]:
        buf = self._dev.read(num_elems=n_samples)
        if buf is None:
            return None
        a = np.frombuffer(buf, dtype=np.int16)
        return ((a[::2] + 1j * a[1::2]) / 32768.0).astype(np.complex64)

    def close(self) -> None:
        self._dev.close()


@dataclass(frozen=True)
class LiveConfig:
    """Bounds for one live receive run (all finite; A/322 7.2.2.2)."""
    plp_id: Optional[int] = None
    subframe: int = 0
    max_windows: int = DEFAULT_MAX_WINDOWS
    max_frames: int = DEFAULT_MAX_FRAMES
    acquire_window_s: float = ACQUIRE_WINDOW_S
    max_iterations: int = 100

    def __post_init__(self):
        # Bounds must be finite; a non-positive value would mean "unbounded".
        if self.max_windows < 1 or self.max_frames < 1:
            raise ValueError("live bounds must be >= 1")
        if not (spec.MIN_FRAME_LENGTH_S <= self.acquire_window_s
                <= spec.MAX_FRAME_LENGTH_S * DEFAULT_MAX_WINDOWS):
            raise ValueError("acquire_window_s out of the spec frame range")


@dataclass
class LiveStats:
    """Counting so a live run can be judged, not guessed."""
    windows: int = 0
    acquisitions: int = 0
    lock_failures: int = 0
    frames_decoded: int = 0
    payloads: int = 0
    streams: int = 0


class LiveReceiver:
    """Bounded live receive loop over an :class:`IqSource`.

    ``on_streams`` (optional) is called with each decoded
    :class:`~atsc3lib.payload.DecodedStreams`; without it, decoded streams are
    appended to :attr:`streams` (which a caller should drain).
    """

    def __init__(self, source: IqSource, config: LiveConfig = None,
                 on_streams: Callable[[object], None] = None):
        self.source = source
        self.config = config or LiveConfig()
        self.on_streams = on_streams
        self.streams: List[object] = []
        self.stats = LiveStats()
        self.result = None
        self._structure = None
        self._exhausted = False

    def _acquire(self, iq_window: np.ndarray) -> bool:
        """Lock on a bootstrap in ``iq_window``; True when locked.

        One :func:`_bootstrap_to_preamble` call detects the bootstrap *and*
        returns the CFO-corrected main stream, and :func:`decode_signaling`
        reuses that same detection, so acquisition happens once.
        """
        try:
            _symbol, main, structure, _start = _bootstrap_to_preamble(
                iq_window, self.source.sample_rate)
        except ValueError:
            return False
        result = decode_signaling(iq_window, self.source.sample_rate,
                                  max_iterations=self.config.max_iterations)
        if not result.l1_detail_ok:
            return False
        self.result = result
        self._structure = structure
        self._main = main
        return True

    def _drain_frames(self):
        # Decode at most max_frames frames from the acquired main stream,
        # stepping by the frame period.
        from .receiver import frame_samples, subframe_end_samples
        period = frame_samples(self.result)
        need = subframe_end_samples(self.result, self.config.subframe)
        main = self._main
        decoded = 0
        offset = 0
        while decoded < self.config.max_frames:
            frame = main[offset:offset + period]
            if frame.size < need:
                break
            target = select_plp(self.result, self.config.plp_id,
                                self.config.subframe)
            if target is None:
                break
            payload = decode_plp_frame(
                self.result, self._structure, frame, target,
                subframe=self.config.subframe,
                max_iterations=self.config.max_iterations)
            self.stats.frames_decoded += 1
            if payload is not None:
                self.stats.payloads += 1
                streams = decode_streams(payload)
                self.stats.streams += 1
                if self.on_streams is not None:
                    self.on_streams(streams)
                else:
                    self.streams.append(streams)
            offset += period
            decoded += 1

    def run_once(self) -> bool:
        """Advance exactly one acquisition window; True when locked.

        On a miss the loop does not scan further — the caller (or
        :meth:`run`) supplies the next window.  That is the wire behaviour:
        miss means "advance and retry the next window".
        """
        window_samples = int(self.config.acquire_window_s
                             * self.source.sample_rate)
        window = self.source.read(window_samples)
        if window is None:
            self._exhausted = True
            return False
        self.stats.windows += 1
        if not self._acquire(window):
            self.stats.lock_failures += 1
            return False
        self.stats.acquisitions += 1
        self._drain_frames()
        return True

    def run(self, max_windows: int = None) -> LiveStats:
        """Try up to ``max_windows`` acquisition windows (bounded).

        A live source never ends, so this is always finite: it returns after
        ``max_windows`` misses, the first lock, or the source ending.  Call
        :meth:`run_once` in the caller's own loop for continuous reception.
        """
        if max_windows is None:
            max_windows = self.config.max_windows
        self._exhausted = False
        for _ in range(max_windows):
            if self._exhausted:
                break
            if self.run_once():
                break
        return self.stats


class LiveMediaSink:
    """Accumulate a bounded live run's UDP datagrams, then build A/V files.

    The live loop decodes one payload at a time; media, however, spans many
    frames (a video MPU is ~120 frames).  This sink collects the datagrams a
    run produced and, at the end of the bounded run, reassembles them once and
    writes the same artifacts as the offline path: per-track MP4s, AC-4 WAVs,
    and the combined A/V MP4.  It is the live-facing counterpart of
    :mod:`atsc3lib.cli`'s ``_write_audio``/``_write_av``.

    Bounds: at most ``max_datagrams`` datagrams are held (the reassembler
    already bounds each object by its signalled length); once full, further
    datagrams are counted and dropped, never buffered.
    """

    def __init__(self, outdir: str, max_datagrams: int = DEFAULT_MAX_DATAGRAMS,
                 audio_track_id: Optional[int] = None):
        self.outdir = outdir
        self.max_datagrams = max_datagrams
        self.audio_track_id = audio_track_id
        self.datagrams = []
        self.dropped = 0

    def __call__(self, streams) -> None:
        """``LiveReceiver.on_streams`` callback: collect the run's datagrams."""
        for d in streams.datagrams:
            if len(self.datagrams) >= self.max_datagrams:
                self.dropped += 1
                continue
            self.datagrams.append(d)

    def build(self) -> List[str]:
        """Reassemble the collected datagrams and write every output file.

        Returns the paths written.  A no-op when nothing was collected or no
        playable track recovered.
        """
        from . import audio, mp4, mux
        from .media import Datagram, reassemble
        if not self.datagrams:
            return []
        media = reassemble(
            Datagram(src_ip=d.src_ip, dst_ip=d.dst_ip, src_port=d.src_port,
                     dst_port=d.dst_port, payload=d.payload)
            for d in self.datagrams)
        import os
        os.makedirs(self.outdir, exist_ok=True)
        written: List[str] = []
        tracks = mp4.build_tracks(media)
        video = next((t for t in tracks if t.handler == b"vide"), None)
        for built in tracks:
            flow = built.dst_ip.replace('.', '_')
            name = (f"{flow}_{built.dst_port}_{built.transport}_"
                    f"{built.track_id}_{built.handler.decode('latin1')}.mp4")
            path = os.path.join(self.outdir, name)
            with open(path, 'wb') as fh:
                fh.write(built.data)
            written.append(path)
        decoded = audio.decode_media(media)
        for d in decoded:
            flow = d.dst_ip.replace('.', '_')
            name = (f"{flow}_{d.dst_port}_{d.transport}_{d.track_id}_soun.wav")
            path = os.path.join(self.outdir, name)
            audio.write_wav(path, d)
            written.append(path)
        if video is not None and decoded:
            chosen = mux.select_audio(decoded, self.audio_track_id)
            if chosen is not None:
                flow = video.dst_ip.replace('.', '_')
                name = (f"{flow}_{video.dst_port}_{video.transport}_"
                        f"{video.track_id}+{chosen.track_id}_av.mp4")
                path = os.path.join(self.outdir, name)
                try:
                    mux.mux_av(video, decoded, path,
                               audio_track_id=chosen.track_id)
                    written.append(path)
                except Exception:
                    pass
        return written
