---
created: 2026-09-26
updated: 2026-10-04
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [project-plan, status, scope]
---

# Project plan status

Status of the three-phase plan in [[openatsc3-project-plan]] against the
on-air ground rule ("every rung is validated on air; if no receivable stream
carries a feature, do not implement it").

## Phase 1 — ATSC3 Python library

| Plan item | Status |
|---|---|
| OFDM demodulation (time/freq sync, CP removal, FFT) | ✓ Air-proven (RF33) |
| Channel estimation / equalization | ✓ Air-proven (RF33) |
| QAM demodulation + soft-decision LLRs | ✓ Air-proven 64QAM 11/15 (RF33 PLP-0); scale fix gated |
| LDPC decoding (exact A/322) | ✓ Air-proven |
| BCH outer code, descrambler | ✓ Air-proven |
| Bootstrap + L1-Basic/L1-Detail framing | ✓ Air-proven (RF33 single-symbol; RF30 multi-symbol) |
| BBP / ALP / IPv4 / UDP / LLS | ✓ Air-proven on RF33 PLP-0 (SLT) and PLP-16 |
| NUC alphabets, HTI/cell/TI, CTI, LDM tables | Implemented; 64QAM + 256QAM air-proven, LDM/CTI not |
| AC-4 audio decoder | ✓ Air-proven (`ac4bindings`; `audio.py`) — decodes RF33 AC-4 to PCM/WAV |

**Phase 1 success criterion** ("decode real broadcast, extract signaling") is
met on the physical + link layers, and AC-4 audio now decodes off RF33.

## Phase 2 — Software receiver

| Plan item | Status |
|---|---|
| SDR integration | ✓ Present (`frontend`, `capture`); HackRF captures used throughout. RTL-SDR unsupported (2.4 MHz < 6 MHz channel) |
| Real-time pipeline | ✓ CLI decode pipeline exists; offline whole-frame drain bounded by file size |
| MMTP/ROUTE demultiplexing | ✓ Air-proven (`mmtp.py`, `route.py`, `media.py`) — RF33 MPUs and ROUTE objects reassemble byte-identically to the reference |
| Video (HEVC) | ✓ Air-proven (`mp4.py`, `atsc3-media`): reassembled ROUTE video builds to a playable fragmented MP4 and decodes to 1920x1080 ([[capture-to-mp4]]) |
| Audio (AC-4) | ✓ Air-proven: `ac4bindings` + `audio.py` decode AC-4 to PCM/WAV; `mux.py` writes one combined A/V MP4 (HEVC + AAC-LC), offline and live ([[capture-to-mp4]]) |
| Certificate validation gate | ⛔ Blocked — needs the SLS/certificate path (platform-side) |

**Phase 2 status changed**: the A/331 **SLT decodes off RF33**
([[rf33-lighthouse-slt]]) and the full media stack now runs — MMTP/ROUTE
reassembly, a fragmented-MP4 build whose video decodes to 1920x1080, AC-4 audio
decode, and a combined A/V MP4 ([[capture-to-mp4]]).  Watching TV *offline* is
done end to end.  The only remaining Phase-2 blocker is **live-rate
throughput** ([[gpu-and-algo-speedups]]); the "watch TV live" criterion is open
on speed alone, not on audio or the link.

## Phase 3 — Certificate Authority platform

| Plan item | Status |
|---|---|
| REST API, enrollment, provisioning, OCSP, dashboard | Not started |

Phase 3 depends on a Phase-2 receiver that validates a broadcaster certificate
from live media — blocked by the media stack.  It is a separate product/service
effort, not a receiver rung.

## What is next: live throughput

The signal and the whole media stack are there; the earlier "RF33 cannot
decode" and "audio is blocked" verdicts are retracted.

1. **Live real-time throughput** is the only open receiver item.  The bounded
   `LiveReceiver` is correct and now writes a VLC-openable A/V file
   (`LiveMediaSink`), but runs ~9 s/frame vs the 247 ms budget on CPU.  The
   recorded options are algorithmic/threading first, optional GPU second
   ([[gpu-and-algo-speedups]]).
2. **LDM/CTI payloads (RF30/RF25)** are implemented-and-gated but blocked by
   capture margin — parked under the on-air rule, not by difficulty.
3. **Platform (Phase 3)** — certificate validation, CA service — is separate
   and not started.

**A nearest-point MER is not Es/N0 — measure at the decoder, then decide.**
This rule held for both PLP-0 (64QAM) and PLP-1 (256QAM), which now decode
74/74 and 117/117.

## See Also

- [[openatsc3-project-plan]]
- [[rf33-lighthouse-slt]]
- [[capture-to-mp4]]
- [[gpu-and-algo-speedups]]
- [[subframe1-plp1]]
- [[fine-timing-cpe]]
