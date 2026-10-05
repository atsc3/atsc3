---
created: 2026-10-03
updated: 2026-10-04
sources: [out/rf33_sdrplay_if45_cs16.iq, tests/data/route_media_flow_8321.dg.gz]
tags: [rf33, mmtp, route, isobmff, mp4, hevc, aac, ac4, media]
---

# Capture to playable MP4

## Summary

`atsc3lib/mp4.py` turns the reassembled MMTP/ROUTE media tracks of a capture
into playable fragmented-MP4 files, one per track.  `atsc3-media <capture>
--rate 10e6` runs the whole path and writes one file per track.  By default it
drains **every layer-0 PLP** for a short, bounded **4 frames** — the shortest
drain that yields a playable segment on the saved RF33 capture (2 frames is
empty; `--frames 0` consumes the whole capture; `--plp N` pins one).  On the
saved RF33 capture (`out/rf33_sdrplay_if45_cs16.iq`, 3 s) the built ROUTE
video files decode to real 1920x1080 HEVC frames via PyAV; a plain default run
finishes in ~35 s and writes a 16-frame MP4.

This is the "capture -> picture" rung inside `atsc3lib`; it complements
`media.py` (MMTP/ROUTE reassembly) and mirrors what the reference `lab/` tools
(`m7_play.py`, `m39_mux.py`) do, used as referee only.

## The problem a naive concatenation has

A broadcast media object is already an ISOBMFF file, so the tempting build is
"init once, append each `moof`+`mdat`".  That does **not** play: every MPU /
ROUTE media segment is *self-contained* and restarts its timeline —

* `moof/mfhd` sequence number is 1 in every fragment, and
* `traf/tfdt` baseMediaDecodeTime is 0 in every fragment.

A player therefore shows the first 2 s and stops.  `mp4.retime(seg, frag_no,
base_time)` rewrites `mfhd` and every `tfdt` onto one continuous timeline, and
returns/accumulates the fragment's own duration.

## Which traf's duration

An MPU carries two `traf`s: the media track and an MMT **hint** track.  At
60000/1001 fps a frame is 1501.5 ticks at timescale 90000; the media `trun`
alternates 1502/1501 and sums to exactly 180180 per 2.002 s MPU, while the hint
`tfhd` carries a rounded constant 1502 and claims 180240.  Advancing by
`max()` — or by the hint — drifts A/V by 60 ticks/MPU ≈ 2.3 s per two hours.
The builder advances by the **first (media)** `traf`.  (This reproduces the
reference's E93/E86 finding against the same air.)

## Completeness, not hope

* A segment is appended only when the media bytes physically present equal the
  length the `mdat` box header itself declares.  That header arrives from the
  transmitter (MMTP FT=1, A/331 8.1.2.2), so it is an external referee.
* A short MMTP MPU is trimmed to its leading whole samples
  (`MpuObject.leading_samples`, new) and labelled; a partial ROUTE object is
  trimmed at its first `start_offset` gap (`RouteObject.contiguous_length`,
  new).  Nothing is silently patched.
* The 3 s MMTP capture loses a packet inside the 120-frame video MPU before
  its first whole sample, so **no MMTP video track is emitted**.  The builder
  reports "no playable track" rather than writing a broken file.  (The ROUTE
  video, whose loss lands later, trims cleanly.)

## Result on air (saved RF33, 3 s)

Draining PLP-0 (subframe 0) and PLP-1 (subframe 1) via
`mp4.build_from_iq(iq, 10e6, plp_ids=[(0,0),(1,1)])` recovers four ROUTE video
lanes, each decoding:

| flow | track samples | HEVC frames decoded | picture |
|------|--------------:|--------------------:|---------|
| 239.255.32.1:8321 | 120 | 117 | 1920x1080 |
| 239.255.4.1:8041  |  97 |  94 | 1920x1080 |
| 239.255.5.1:8051  | 137 | 135 | 1920x1080 |
| 239.255.9.1:8091  |  60 |  59 | 1920x1080 |

The MPU/MMTP flow carries AC-4 audio (`ac-4` in `stsd`) and `stpp` subtitles;
its tracks reassemble and build, but the video MPU is too short to yield a
whole sample from this capture.

## One playable A/V file (audio baked in)

The broadcast `soun` track is raw AC-4; no player decodes it, so the MP4-only
rung is silent.  `mux.py` closes that: it opens the built video track in
memory, **stream-copies** the HEVC (`add_stream_from_template`, no re-encode),
encodes the decoded AC-4 PCM to **AAC-LC** with ffmpeg's encoder via PyAV, and
muxes both onto one timeline.  The result is written as a **fragmented** MP4
(`empty_moov` + `moof`/`mdat`) so a player opens it and a truncated file stays
valid past its last fragment.  `atsc3-media` writes it by default
(`--no-mux` opts out; `--audio-track` selects which AC-4 track).

On the saved RF33 3 s capture, `atsc3-media out/rf33_sdrplay_if45_cs16.iq
--rate 10e6 --fmt cs16 --all --frames 0` (drains every layer-0 PLP) writes
`239_255_32_1_8321_route_10+20_av.mp4`: 117 HEVC frames (1920x1080) + non-silent
stereo AAC, ~2.07 s, 800720 bytes.

The live loop reaches the same artifact: `live.LiveMediaSink` collects the
run's UDP datagrams (bounded by `DEFAULT_MAX_DATAGRAMS`; the reassembler already
bounds each object by its signalled length), then reassembles and writes the
per-track MP4s, AC-4 WAVs and the combined A/V file.  `atsc3-live --outdir`
wires it in.  A live run still lags the air rate, but the output it does
produce is a normal VLC-openable file.

**Normalisation (the scratching fix).**  The AC-4 decoder emits integer-scale
PCM (a full-scale sine peaks near 2**18), not the `[-1, 1]` a codec expects.
The first combined file fed those raw values to the AAC encoder, which clipped
every loud sample — audible scratching (the WAVs were fine, since `write_wav`
already normalised).  `audio.normalized_pcm` is now the single shared stage:
it scales the peak to `WAV_PEAK` 0.9 and is used by both `write_wav` and
`mux._planar`.  Measured: decoded peak 262291 -> muxed AAC peak 0.899, zero
samples at or above 0.999.

## Multi-PLP drain

`receiver.decode_plp_frames_streams` acquires once (L1 config + bootstrap-
aligned, CFO-corrected main stream) and steps every whole frame by
`frame_samples`; `mp4.build_from_iq` accepts `plp_ids` as `(subframe, plp_id)`
pairs so media split across PLPs is drained.  The single-PLP CLI default is
unchanged.

## Gates

* `tests/test_mp4.py` — box walk, `retime`, `trim_to_samples` on a synthetic
  fragment, `track_info` handlers, and an RF33 ROUTE picture gate (>100 HEVC
  frames, 1920x1080) when PyAV is present.
* `tests/test_mmtp_media.py` — reassembly byte-identity against the reference.
* `tests/test_mux.py` — the combined file reopens with a HEVC video and an AAC
  audio stream, video packet count preserved, audio non-silent, `moov < moof`.
* `tests/test_live.py` — `LiveMediaSink` caps/collects and writes the A/V files.
* Full atsc3lib suite: 637 tests.

## See Also

- [[rf33-lighthouse-slt]]
- [[data-plp-payload]]
- [[overview]]
- [[index]]
