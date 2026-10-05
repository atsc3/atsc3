# atsc3lib

**ATSC 3.0 physical-layer receiver library.**

Hardware-agnostic: works with raw IQ from any SDR wide enough for the 6 MHz
channel (SDRplay, USRP).

Implements, from the A/322 specification:

- Bootstrap detection and signalling decode
- Preamble OFDM parameter detection, channel estimation and equalization
- Frequency de-interleaving
- L1-Basic and L1-Detail FEC (BCH + LDPC) and field parsing
- **Per-PLP configuration** decoded from L1-Detail
- **Data-PLP payload chain**: NUC/QAM demap, bit de-interleave, HTI twisted
  block de-interleave and cell interleaver, LDPC/BCH, descramble to Baseband
  Packets — for both short-frame (Ninner=16200) and normal-frame
  (Ninner=64800) codes, all 12 rates and all four NUC modes
- **Link/network layer**: A/322 5.2.2 Baseband Packet headers, A/330 ALP
  de-encapsulation (single / segmentation / concatenation / signalling),
  IPv4 fragment reassembly and UDP, A/331 Low-Level Signaling
- **Service discovery / delivery**: A/331 SLT parsing (gzip + XML) with the
  ROUTE/MMTP SLS bootstrap, ROUTE/ALC (RFC 5651 LCT) packet parsing with
  delivery-object reassembly and Extended FDT parsing, and MMTP
  (ISO/IEC 23008-1) packet parsing with `mmt_atsc3_message()` SLS
  (USBD/HELD) and MP Table extraction

Validated against real off-air captures and an independent receiver.

## Installation and build

atsc3lib builds its own ATSC kernels as C extensions under
`atsc3lib._bindings` (normalized-min-sum LDPC and BCH, max-log demapper,
frequency-interleaver address generator). Two further compiled extensions are
separate (required) dependencies:

- `sdrbindings` — SDRplay capture over SoapySDR (`../sdrbindings`).
- `ac4bindings` — the AC-4 (ETSI TS 103 190) decoder kernels (`../ac4bindings`).

Installing atsc3lib compiles its own kernels:

```bash
cd atsc3lib
make install      # pip install -e . (builds atsc3lib._bindings._ldpc etc.)
```

`make` targets:

```bash
make              # install atsc3lib (editable, compiling its kernels)
make test         # run the atsc3lib test suite (includes the C kernels)
make clean        # remove build artefacts
```

## Capture IQ Samples

```bash
atsc3-capture -f 587 -o out/capture.iq              # SDRplay RSP1B
atsc3-capture -f 587 -g 45 --rf-gain 3              # tuned on RF33
```

## Decode Signalling and PLP Configuration

```bash
atsc3-decode out/capture.iq --rate 10e6 --fmt cs8
```

Example output:

```
Capture: out/capture.iq @ 10.000 MHz
  L1-Basic: version 0, CRC OK
    subframes           : 2
    L1-Detail fec type  : 2 (mode 3)
    L1-Detail total cells: 880
  L1-Detail: version 1, CRC OK
  Per-PLP configuration:
    subframe 0  PLP 0   layer=0 start=0 size=199800 fec=0 mod=2 cod=9 TI=2
    subframe 0  PLP 16  layer=0 start=199800 size=8100 fec=0 mod=0 cod=0 TI=2
    subframe 1  PLP 1   layer=0 start=0 size=947700 fec=1 mod=3 cod=9 TI=2
```

## Bounded live receive loop

`atsc3-live` (and `atsc3lib.live.LiveReceiver`) runs the same receive chain on
finite windows from a live SDRplay or a saved capture. It never scans
unbounded: each `run()` tries a bounded number of acquisition windows and a
miss advances to the next window (A/322 7.2.2.2), and L1 config is held across
frames once locked.

```bash
atsc3-live --file out/capture.iq --plp 16 --units 12 --max-frames 1
atsc3-live --freq 587e6 --plp 0                 # live SDRplay
```

```python
from atsc3lib.live import FileIqSource, LiveConfig, LiveReceiver

src = FileIqSource('out/capture.iq', 10e6, 'cs16')
rx = LiveReceiver(src, LiveConfig(plp_id=16, max_frames=1),
                  on_streams=lambda s: print(len(s.datagrams), 'datagrams'))
rx.run(max_windows=12)
```

Note: the LDPC (`atsc3lib._bindings.fec`, ~5x), max-log demapper
(`atsc3lib._bindings.demod`, ~3-6x), BCH (C) and frequency interleaver
(`atsc3lib._bindings.ofdm`, ~100x) run in C, the
FEC blocks are threaded across cores, and bootstrap acquisition is 4x faster
(overlap-save correlation); a PLP-1 (256QAM) subframe frame dropped from ~190 s
to ~9 s. The pipeline still does not keep up with the 52.7 ms air rate (this is
an old dual-core host; see AGENTS.md). The loop is correct and bounded
regardless.

## Python API

```python
from atsc3lib import decode_capture, decode_plp_streams

result = decode_capture('out/capture.iq', fs_main=10e6, fmt='cs8')
if result.l1_detail_ok:
    for subframe, plp in result.plps:
        print(subframe, plp.plp_id, plp.modulation, plp.code_rate)

# Decode a subframe-0 PLP through to ALP/UDP/LLS
iq = ...  # the same capture IQ
result, streams = decode_plp_streams(iq, fs_main=10e6, plp_id=16, result=result)
for table in streams.lls:
    print(table.name, len(table.data))
for datagram in streams.datagrams:
    ...
```

Raw Baseband Packet bytes remain available from `decode_plp_payload` ->
`PlpPayload.baseband_packets`; `payload.decode_streams` runs the link layer on
them alone.

## Supported Hardware

- **SDRplay** (RSP1B, via the `sdrbindings` SoapySDR extension)
- **Any SoapySDR device** (via the same extension, `--driver` passthrough)

The library is hardware-agnostic: it consumes raw IQ samples from any source
wide enough for the 6 MHz ATSC 3.0 channel.

### SDRplay driver

The capture path uses `sdrbindings`, a separate CPython extension around the
SoapySDR C API in `../sdrbindings`.  It streams the RSP1B's native CS16 and
writes interleaved int16 IQ by default (`--cs8` down-converts to the int8
layout).  `atsc3-decode` auto-detects the sample format.  Install it once:

```bash
make -C ../sdrbindings install
```

The RSP1B's two gain elements are exposed as `--gain` (IFGR) and `--rf-gain`
(RFGR); on RF33 the lighthouse decodes with `--gain 45 --rf-gain 3`.

### HackRF Pro is not supported

The **HackRF Pro** cannot meet the sample-rate floor this library requires.
ATSC 3.0 needs at least 6.144 Msps of raw IQ.  The Pro's radio path is only
8-bit, and its improved "extended-precision" (16-bit sample, ~9-11 ENOB) mode
earns that dynamic range by oversampling with a minimum decimation factor of
16x.  With the 40 MHz ADC clock, 40 MHz / 16 = **2.5 Msps maximum**, which is
below the 6.144 Msps floor.  The Pro therefore cannot capture a full ATSC 3.0
channel in a usable mode, regardless of firmware.  Use an SDRplay (e.g. RSP1A)
or another device that delivers 6.144+ Msps natively.

## Signing security (pluggable provider)

Signed signaling (A/360) is verified through a **pluggable security provider**,
so the receiver is not tied to one certificate authority or DRM vendor.  The
standards fix the formats and validation rules, not the operator; the default
provider is our own greenfield CA (`openatsc3_pki`), and an A3SA/Widevine-backed
provider is a drop-in replacement.

```python
from atsc3lib import security

security.available()                 # ['openatsc3']
roots = security.load_trust_roots(["my-root.pem"])
report = security.verify_streams(streams, roots)   # SecurityReport
print(report.ok, report.provider, security.describe(report))
```

On the CLI:

```bash
atsc3-decode capture.iq --rate 10e6 --trust-root my-root.pem
atsc3-decode capture.iq --rate 10e6 --trust-root my-root.pem --security-provider openatsc3 --require-signature
```

The seam is `atsc3lib/security/`:

* `base.py` -- `SecurityProvider` protocol, `SecurityReport` / `TableVerdict`
  result types, a name registry, and `ATSC3LIB_SECURITY_PROVIDER` selection.
* `openatsc3.py` -- the built-in provider wrapping `openatsc3_pki` (optional
  extra, `pip install -e .[pki]`; without it verification reports "unavailable"
  and the receiver still runs).

A provider implements three methods (`load_trust_anchors`,
`verify_certification_data`, `verify_signed_table`); it receives generic
`SignableTable` rows, not receiver I/O types.  `atsc3-decode` attaches the
verdict to `DecodedStreams.verification`.  The seam is gated by
`tests/test_security_provider.py` (register/select, environment selection, and
a stub provider), and the A/331-layer end-to-end path by
`tests/test_security.py`.

## Link-layer limitations

The A/330 layer implements Base Headers, single/segmentation/concatenation
payloads, the type-specific and Extension Headers, IPv4 fragment reassembly
and UDP, and LLS table extraction.

ROHC header decompression (A/330 §6, compressed-IP `packet_type = 010`) is
**deferred, not a design exclusion**: the only receivable stream we have, RF33
(BSID 540), carries no compressed-IP.  A `packet_type` census over its decoded
PLPs (PLP-0 64QAM: 78 ALP packets; PLP-1 256QAM: 399; PLP-16: 0) found **all
477 packets `000` IPv4** — MMTP media/SLS and ROUTE alike ride as plain
IPv4/UDP.  Per the on-air ground rule there is nothing to decompress, so ROHC
is not built.  It returns to scope the moment a receivable mux signals `010`
(compressed streams are otherwise surfaced as raw ALP packets).

## Media (MMTP/ROUTE) to a picture, off disk

**A saved RF33 capture decodes to a 1920x1080 HEVC picture** — WETA's
*Amanpour & Co.* on WHUT — from `out/rf33_sdrplay_if45_cs16.iq` (3 s).  The
path is: drain every **whole frame** (the live loop's frame period must span
all subframes — see below), accumulate IP datagrams, and reassemble both
transports in `atsc3lib` itself:

* `mmtp.py` reassembles MMTP MPUs (ISO/IEC 23008-1 9.3.2.2; A/331 8.1.2.2):
  FT=0 (`ftyp`/`mmpu`/`moov`), FT=1 (`moof` + `mdat` header), FT=2 (MFU
  samples) into `MpuObject.meta` + `MpuObject.body`.
* `route.py` reassembles ROUTE/LCT objects by their 32-bit `start_offset`.
* `media.py` classifies each UDP flow as MMTP or ROUTE by the transmission's
  own arithmetic (no hard-coded address/port), reassembles it, and extracts
  HEVC access units (`iter_samples`, `extract_tracks`).
* `mp4.py` turns an initialisation segment plus its media segments into a
  **playable fragmented MP4** and writes one file per track.  `atsc3-media
  <capture> --rate 10e6` runs the whole path (it drains **every layer-0 PLP**
  for a few frames by default — `--frames 4`, the shortest that yields a
  playable segment; `--frames 0` consumes the whole capture; `--plp N` pins
  one).  `--all` writes audio/subtitles too.
* `audio.py` decodes an AC-4 track to PCM and writes a WAV.  The AC-4 decoder
  itself is the compiled `ac4bindings` package (ETSI TS 103 190); a track's
  samples are already one `raw_ac4_frame` each, so decoding is a straight pass
  to `ac4bindings.decode`.  `atsc3-media` writes a `*_soun.wav` beside every
  AC-4 track's MP4 (RF33 track 13: six channels, L/R/C/LFE/Ls/Rs).  Gated by
  `tests/test_audio.py` on the MMTP media flow.
* `mux.py` combines the longest video track and the decoded AC-4 PCM into one
  **playable A/V MP4** (PyAV): the HEVC video is stream-copied (no re-encode)
  and the AC-4 PCM is encoded to AAC-LC and muxed onto one timeline, written
  as a *fragmented* MP4 (`empty_moov` + `moof`/`mdat`) so a player opens it
  and a truncated file stays valid.  `atsc3-media` writes it by default
  (`--no-mux` skips it; `--audio-track` picks the AC-4 track).  The live loop
  writes the same artifacts via `live.LiveMediaSink` when `atsc3-live
  --outdir` is given.  Gated by `tests/test_mux.py`.

A broadcast fragment is a *self-contained* ISOBMFF file: every MPU restarts
its `mfhd` sequence number at 1 and its `tfdt` decode time at 0, so simply
concatenating them makes a player show the first segment and stop.  `mp4.py`
rewrites both onto one continuous timeline, advancing by the **media** `traf`'s
duration and not the hint `traf`'s (the two express a 60000/1001 frame
differently; the hint rounds, and picking it drifts A/V by 2.3 s per two
hours).  A segment is appended only when the media bytes present equal the
length its own `mdat` header declares — a transmitter statement, an external
referee — or is trimmed to its whole leading samples and labelled; it is never
silently patched.

The on-air details are the reference receiver's, gated byte-for-byte here: the
MMTP version-1 header is **14 bytes** (12-byte base + a 2-byte `ver_ext` field,
constant `0x1800` on RF33) — established by the MPU payload `payload_length`
matching the bytes remaining for 943/943 packets; the MPU payload header is
`payload_length`(16)/`fragmentation_info`(8)/`fragment_counter`(8)/
`mpu_sequence_number`(32); and each FT=2 data unit is
`[14-byte DU header][MMT hint sample][media sample]`.

The reassembly output is byte-identical to the independent reference
receiver's (`lab/m7_objects.py`, used as referee only).  Decoding does not keep
up live on this machine, but offline it is bounded by file size, not wall
clock.  Gated by `tests/test_mmtp_media.py` and `tests/test_mp4.py`, the latter
including an end-to-end picture gate: the built ROUTE video file decodes to
1920x1080 HEVC frames via PyAV (when present).  A plain `atsc3-media
out/rf33_sdrplay_if45_cs16.iq --rate 10e6 --fmt cs16` run finishes in ~35 s and
writes a 16-frame 1920x1080 MP4.  Draining all layer-0 PLPs over the whole 3 s
capture (`--frames 0 --all`) writes `239_255_32_1_8321_route_10+20_av.mp4`,
which decodes to 117 HEVC frames with non-silent stereo AAC.

**Bug fixed: `frame_samples` dropped subframe 1.**  It summed only the
Preamble + subframe 0 (364,032 samples = the *start* of subframe 1) rather
than the whole frame.  RF33's real bootstrap-to-bootstrap period is
**1,708,032 samples = 247.1 ms**, measured on air (5 bootstrap detections at a
constant 1,518,251-sample spacing at 6.144 MHz).  Any multi-frame drain was
therefore misaligned by subframe 1's 1,344,000 samples.  `frame_samples` now
sums every subframe; `subframe_end_samples(result, sf)` gives the span through
a named subframe so a one-subframe capture still decodes.  Gated by
`tests/test_air_payload.py::TestFramePeriod` (asserts 1,708,032).

## Payload limitations

**Ground rule: every rung must be validated on air.**  A feature is only
implemented when a receivable stream carries it; a structural or synthetic
round-trip gate does not qualify a rung.  If the link cannot deliver the
feature, record the blocker and move on rather than building an unprovable
stage.

The payload chain supports both Ninner = 16200 (short frames) and
Ninner = 64800 (normal frames), and the tabulated QPSK/16QAM/64QAM/256QAM
MODCODs.  TI mode 2 supports the A/322 7.1.5.4 twisted block interleaver and
the optional A/322 7.1.5.2 **cell** interleaver
(`L1D_plp_HTI_cell_interleaver`); TI modes 0/1 are supported.

**A lighthouse multiplex decodes off air.**  With the SDRplay RSP1B, PLP-0
(64QAM-NUC 11/15) converges **74/74** FEC blocks (was 53-60/74 on the HackRF)
and yields real LLS: the A/331 **SLT** and SystemTime.  The SLT lists the major
services carried by the multiplex.  This is the first off-air service list, and
it replaces the earlier "padding-only, link-limited" conclusion.

The scale bug that hid this: the A/322 Annex C NUC alphabets have unit average
power, but an equalised cell block does not, and the max-log metric is not
invariant to that scale.  Every data FEC block is therefore normalised to unit
mean power before demapping (`DataPlpChain.decode_cells`); without it PLP-0
decodes 0/74, with it 74/74 on the SDRplay (53-60/74 on the HackRF).
The decision-directed CPE happened to normalise internally, which masked the
defect whenever CPE was on.  The property is gated by
`tests/test_data_plp.py::test_decode_cells_scale_invariant`.

**Subframe-1 PLP-1 (256QAM-NUC 11/15) now decodes 117/117 off air**, yielding
358 UDP datagrams.  The two changes that closed it: the SDRplay RSP1B front end
reads subframe 1 at 25.8 dB nearest-point MER (the HackRF fixture was 20.6 dB),
and the channel estimator now merges the ``DY`` scattered-pilot subsets of
``DY`` consecutive symbols into a ``DX``-spaced grid (`dense_symbol_channel`),
resolving the frequency-selective (multipath) residual the single-symbol linear
interpolator left behind.  The measured channel ripple was |H| std 0.19
(norm 0.85-2.64); a synthetic static channel with that ripple read 23.0 dB MER
and decoded 0/39 under the old estimator, while pure AWGN at 22.9 dB decoded
39/39 — nearest-point MER under-reports multipath residual for 256QAM.
`decode_subframe_plp` defaults ``dense=True``.

Payload demodulation works on any subframe.  Subframe 0 carries the Preamble
spare cells; later subframes are demodulated at their own FFT/GI/pilot geometry
with the A/322 7.3 frequency-interleaver counter reset at the subframe boundary.
Pass `subframe=` to `decode_plp_payload` (and `--plp` plus `--subframe` to the
CLI).  The per-FFT pilot and data-cell tables (A/322 Tables 7.3-7.6, Annex F,
D.1.4/D.1.5) are inlined in `atsc3lib/pilot_data.py`, generated by
`tools/fetch_pilot_tables.py` from the pinned independent transcription and
gated by the constant-data-carrier identity.

The Preamble may span **multiple OFDM symbols** (A/322 7.2.5).  L1-Basic sits
at the start of the first symbol; L1-Detail fills the rest of it and the later
symbols, block-de-interleaved with Lc = NP columns (7.2.5.2).  All Preamble
symbols share the bootstrap geometry, later ones use
`L1B_preamble_reduced_carriers`, and the frequency-interleaver counter keeps
counting frame symbols (so subframe 0's first data symbol has origin NP).  A
two-symbol Preamble (NP = 2, L1-Basic Mode 1 + L1-Detail) is verified off air.

LDM and CTI multiplexes are supported: `cti.py` implements the A/322 7.1.4
convolutional time de-interleaver and its 9.3.9.1 signalled identity, `spec.py`
carries the Table 9.24 `Nrows` menu and the Table 9.22/6.15/6.16 LDM power
ratios, and the demodulator includes scattered-pilot **fine timing**
(8.1.3.1, `subframe_fine_timing`) and a decision-directed per-symbol **CPE**
(7.2.6.5 dummy tail, `cpe_correct`).  Both are on by default for the CTI path
(`decode_cti_plp_streams`; `--no-fine-timing`, `--no-cpe` to disable).
