"""Mux a decoded video track and AC-4 PCM into one playable A/V MP4.

The broadcast itself carries no playable audio track: the ``soun`` track in
:mod:`atsc3lib.mp4` holds raw AC-4 frames, for which no player has a decoder.
An operator therefore needs one file that both plays and sounds.  This module
opens the retimed fragmented MP4 in memory, **stream-copies** the HEVC video
(no re-encode), encodes the decoded AC-4 PCM to AAC-LC with the ffmpeg encoder
that PyAV ships, and muxes both onto one timeline.

Audio is the *decoded* PCM from :class:`~atsc3lib.audio.DecodedAudio`, not the
``ac-4`` sample entry: the raw frames cannot be remuxed into a standard MP4
sample entry a player understands, and decoding them here is the whole point.

Spec: the AC-4 render is 48 kHz (A/342); the AAC-LC frame is 1024 samples per
channel.  The video stream is copied verbatim, so its timing and codec
parameters come from the transmitter's own init segment.
"""

from __future__ import annotations

import io
from typing import Optional

import numpy as np

#: ffmpeg AAC-LC encoder, and the bitrate/sample rate used for the audio track.
AAC_CODEC = "aac"
AAC_BIT_RATE = 192_000
AAC_SAMPLE_RATE = 48_000

#: AAC-LC encodes in 1024-sample frames.
AAC_FRAME_SAMPLES = 1024

#: Fragmented-MP4 flags: an empty initial ``moov`` up front and one fragment
#: per keyframe.  A player (VLC) can open and seek a file that is still being
#: written, and a truncated live file is still valid past its last fragment —
#: a plain MP4 writer holds the whole file until EOF.
MOVFLAGS = "frag_keyframe+empty_moov+default_base_moof"

#: The decoded channel-name tuple -> the ffmpeg channel layout it denotes.
#: Names come from :data:`atsc3lib.audio.SURROUND_ORDER`, which is the A/342
#: loudspeaker order; an unrecognised set is downmixed to stereo.
_LAYOUTS = {
    ("L", "R"): "stereo",
    ("L", "R", "C"): "3.0",
    ("L", "R", "C", "lfe"): "4.0",
    ("L", "R", "Ls", "Rs"): "quad",
    ("L", "R", "C", "lfe", "Ls", "Rs"): "5.1",
    ("L", "R", "C", "lfe", "Ls", "Rs", "Lb", "Rb"): "7.1",
}


def _av():
    try:
        import av
    except ImportError as exc:  # pragma: no cover - exercised by the CLI guard
        raise RuntimeError(
            "PyAV ('av') is required to mux audio and video") from exc
    return av


def mux_error_types() -> tuple:
    """The exception types :func:`mux_av` raises, for callers that guard it.

    PyAV reports decode/encode/mux failures as ``av.error.FFmpegError``; that
    type is added only when ``av`` is importable so callers need no hard PyAV
    dependency.  The rest cover the missing-extra path (:class:`ImportError`,
    :class:`RuntimeError` from :func:`_av`) and the local I/O of writing the
    output file (:class:`OSError`, :class:`ValueError`).
    """
    errors = (ImportError, RuntimeError, OSError, ValueError)
    try:
        from av.error import FFmpegError
    except ImportError:
        return errors
    return errors + (FFmpegError,)


def select_audio(decoded, track_id: Optional[int] = None):
    """The audio track to mux: ``track_id`` when given, else the longest.

    ``decoded`` is a list of :class:`~atsc3lib.audio.DecodedAudio`.  Every
    track still gets its own WAV; only one rides in the combined A/V file.
    """
    if not decoded:
        return None
    if track_id is not None:
        for d in decoded:
            if d.track_id == track_id:
                return d
        return None
    return max(decoded, key=lambda d: d.frame_count)


def _planar(decoded, av):
    """The decoded PCM as ``(array[channels, samples], ffmpeg_layout)``.

    The samples are normalised with :func:`atsc3lib.audio.normalized_pcm` — the
    decoder output is integer-scale, and the encoder silently clips anything
    outside ``[-1, 1]`` (audible as scratching).
    """
    from .audio import normalized_pcm
    names = decoded.channels
    norm, _gain = normalized_pcm(decoded)
    index = {name: i for i, name in enumerate(names)}
    if names in _LAYOUTS:
        return norm.astype(np.float32), _LAYOUTS[names]
    if "L" in index and "R" in index:
        return norm[[index["L"], index["R"]]].astype(np.float32), "stereo"
    mono = norm[index[names[0]]].astype(np.float32)
    return np.stack([mono, mono]), "stereo"


def mux_av(video, decoded, path: str,
           audio_track_id: Optional[int] = None) -> Optional[str]:
    """Mux ``video`` (a :class:`~atsc3lib.mp4.BuiltTrack`) and one decoded AC-4
    track into the playable file ``path``.

    Video is stream-copied from ``video.data``; the audio track is encoded to
    AAC-LC from the selected :class:`~atsc3lib.audio.DecodedAudio`.  Returns
    ``path``, or ``None`` when there is no audio to mux.
    """
    chosen = select_audio(decoded, audio_track_id)
    if chosen is None:
        return None
    av = _av()
    pcm, layout = _planar(chosen, av)
    rate = chosen.sample_rate or AAC_SAMPLE_RATE

    src = av.open(io.BytesIO(video.data))
    vstream = src.streams.video[0]
    out = av.open(path, "w", format="mp4", options={"movflags": MOVFLAGS})
    vout = out.add_stream_from_template(vstream)
    aout = out.add_stream(AAC_CODEC, rate=rate)
    aout.layout = layout
    aout.bit_rate = AAC_BIT_RATE

    for pkt in src.demux(vstream):
        if pkt.dts is None:
            continue
        pkt.stream = vout
        out.mux(pkt)

    total = pcm.shape[1]
    for start in range(0, total, AAC_FRAME_SAMPLES):
        chunk = pcm[:, start:start + AAC_FRAME_SAMPLES]
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(chunk), format="fltp", layout=layout)
        frame.sample_rate = rate
        frame.pts = start
        for pkt in aout.encode(frame):
            out.mux(pkt)
    for pkt in aout.encode(None):
        out.mux(pkt)
    out.close()
    return path
