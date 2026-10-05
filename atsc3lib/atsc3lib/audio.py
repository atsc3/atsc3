"""AC-4 audio: reassembled media track -> PCM -> WAV.

The AC-4 decoder lives in the compiled ``ac4bindings`` package (ETSI
TS 103 190); this module is the bridge from ``atsc3lib``'s reassembled media
tracks to decoded audio.  A track's samples are already one ``raw_ac4_frame``
each (see :mod:`atsc3lib.media`), so decoding is a straight pass to
``ac4bindings.decode``.

Only AC-4 tracks are handled; other codecs return ``None``.  Frames the
decoder cannot carry (non-ASF codec modes, the SSF frontend) are counted in the
returned statistics, never faked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: WAV channel order for a 5.1 AC-4 render (A/342 loudspeaker layout).
SURROUND_ORDER = ("L", "R", "C", "lfe", "Ls", "Rs")


def is_ac4(track) -> bool:
    """True when ``track``'s init segment declares the AC-4 sample entry."""
    return b"ac-4" in track.init


@dataclass
class DecodedAudio:
    """Decoded audio for one media track.

    ``dst_ip``/``dst_port``/``transport`` identify the flow, so a WAV written
    beside the MP4s can be named the same way and two flows that both carry a
    track 20 do not collide.
    """
    track_id: int
    sample_rate: int
    pcm: dict
    stats: object
    dst_ip: str = ""
    dst_port: int = 0
    transport: str = ""

    @property
    def frame_count(self) -> int:
        return len(next(iter(self.pcm.values()))) if self.pcm else 0

    @property
    def channels(self):
        return tuple(name for name in SURROUND_ORDER if name in self.pcm)


def decode_track(track, order=SURROUND_ORDER):
    """Decode an AC-4 ``MediaTrack`` to per-channel PCM.

    Returns a :class:`DecodedAudio`, or ``None`` when the track is not AC-4 or
    carries no decodable frames.  This is the MMTP shape, where one track
    carries both the init and its samples; use :func:`decode_media` for the
    ROUTE shape, where init and samples arrive as separate objects.
    """
    if not is_ac4(track):
        return None
    return _decode(track, track.samples, order)


def _decode(track, samples, order):
    from ac4bindings import decode as _decode
    result = _decode.decode_frames(samples)
    if not result.pcm:
        return None
    pcm = {name: result.pcm[name] for name in order if name in result.pcm}
    return DecodedAudio(track_id=track.track_id, sample_rate=result.sample_rate,
                        pcm=pcm, stats=result.stats,
                        dst_ip=track.dst_ip, dst_port=track.dst_port,
                        transport=track.transport)


def decode_media(media, order=SURROUND_ORDER):
    """Decode every AC-4 track in a reassembled capture.

    MMTP tracks are self-contained (init and samples together).  ROUTE delivers
    a track's ``ac-4`` init (TOI ``0xFFFFFFFF``) and its samples as separate
    objects, so they are paired by ``track_id`` (the TSI).  A ROUTE sample
    track carries no init of its own (:mod:`atsc3lib.media`), so the AC-4
    decision is made from the paired init, not the sample track.  Returns
    ``[DecodedAudio, ...]``.
    """
    from .media import extract_tracks
    inits = {}
    samples = {}
    self_contained = []
    for track in extract_tracks(media):
        key = (track.dst_ip, track.dst_port, track.track_id)
        if track.init and track.samples:
            self_contained.append(track)
        elif track.samples:
            samples.setdefault(key, []).extend(track.samples)
        elif track.init:
            inits.setdefault(key, []).append(track)
    out = []
    for track in self_contained:
        if not is_ac4(track):
            continue
        decoded = _decode(track, track.samples, order)
        if decoded is not None:
            out.append(decoded)
    for key, init_tracks in inits.items():
        if key not in samples or not any(is_ac4(t) for t in init_tracks):
            continue
        decoded = _decode(init_tracks[0], samples[key], order)
        if decoded is not None:
            out.append(decoded)
    return out


#: PCM normalisation target: the peak is scaled to this before it is quantised
#: to 16-bit PCM or encoded, so the output cannot clip.  The AC-4 decoder emits
#: integer-scale samples (a full-scale sine peaks near 2**18), not the [-1, 1]
#: a codec expects, so both consumers must share one gain.
WAV_PEAK = 0.9


def normalized_pcm(audio: DecodedAudio):
    """The decoded channels as ``(array[channels, samples], gain)``.

    Returns float samples scaled so the shared peak is :data:`WAV_PEAK` (a
    silent track is left unchanged with gain 1), plus the gain applied.  This
    is the single normalisation :func:`write_wav` and :mod:`atsc3lib.mux` both
    use; feeding the raw integer-scale decoder output to a codec clips it.
    """
    import numpy as np
    names = audio.channels
    if not names:
        raise ValueError("no audio channels to write")
    arrays = [np.asarray(audio.pcm[n], dtype=np.float64) for n in names]
    n = min(len(a) for a in arrays)
    peak = max(float(np.abs(a[:n]).max()) for a in arrays)
    gain = WAV_PEAK / peak if peak > 0 else 1.0
    planar = np.stack([a[:n] * gain for a in arrays])
    return planar, gain


def write_wav(path: str, audio: DecodedAudio, interleaved=True) -> str:
    """Write a :class:`DecodedAudio` as a 16-bit PCM WAV.

    Channels are packed in ``SURROUND_ORDER``; the peak is normalised to
    :data:`WAV_PEAK` unless every channel is silent.
    """
    import wave
    import numpy as np
    planar, _gain = normalized_pcm(audio)
    pcm = np.empty((planar.shape[1], planar.shape[0]), dtype="<i2")
    for i in range(planar.shape[0]):
        pcm[:, i] = np.clip(planar[i], -1.0, 1.0) * 32767
    with wave.open(path, "wb") as w:
        w.setnchannels(planar.shape[0])
        w.setsampwidth(2)
        w.setframerate(audio.sample_rate)
        w.writeframes(pcm.tobytes())
    return path
