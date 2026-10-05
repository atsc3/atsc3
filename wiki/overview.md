# Wiki Overview

This wiki is maintained using the [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) by Andrej Karpathy.

## Purpose

A persistent, compounding knowledge base that grows richer with every source added and question asked.

## Ground rule: every rung is validated on air

A feature is only implemented when a receivable stream carries it, and its gate
is a real-air decode.  A structural or synthetic round-trip gate does **not**
qualify a rung: a feature whose only evidence would be synthetic can never be
proven to work over the link, so it is not written.  When a feature is blocked
by the link (no LOS, insufficient margin, no capture), record the blocker and
move on rather than building an unprovable stage.

## Do-not-revisit list

Closed, and recorded so they are not attacked again.  See the README for the
numbers.

- LDM enhanced-layer cancellation — only in-scope layer (RF30 PLP-1) is behind
  removed link-limited captures; the reference's canceller stops ungated.
- RF30 / RF25 core-layer payloads — link-limited.
- RF6 (85 MHz) — not receivable (super-low power).

## What is proven

The full physical + link chain decodes **RF33 PLP-0 (64QAM-NUC 11/15)** end to
end: 74/74 FEC blocks, yielding the A/331 **SLT** (`bsid="540"`, majors
WHUT/WJLA/WTTG/WRC/WUSA) and SystemTime.  It also decodes RF33 PLP-16 (QPSK
2/15) byte-identical to the independent reference, and RF33 PLP-1
(256QAM-NUC 11/15) 117/117.  On top of that the media stack is air-proven:
ROUTE/MMTP reassembly, fragmented-MP4 build with 1920x1080 HEVC, AC-4 audio
decode, and a combined A/V MP4 (see [[capture-to-mp4]]).  See
[[rf33-lighthouse-slt]].

This retracts the earlier verdict that RF33 PLP-0 was "2.8 dB short,
unachievable": the blocker was a **demapper scale bug** (unit-power NUC
alphabets vs non-unit equaliser output), now fixed at the source.  RF33 is the
DC lighthouse, and it decodes.

**Subframe 1's PLP-1 (256QAM-NUC 11/15) now decodes 117/117 off air
(2026-10-01).**  Two changes closed it: the SDRplay RSP1B front end (replacing
the HackRF) raised the subframe-1 cells to 25.8 dB nearest-point MER, and the
dense multi-symbol channel estimator (`payload.py::dense_symbol_channel`)
resolved the frequency-selective (multipath) residual the single-symbol
interpolator left behind.  The earlier "0/117 at ~20.6 dB, a link-margin
verdict" is superseded.  The Mux/ROUTE media, AC-4 audio and the combined A/V
MP4 build are now air-proven too ([[capture-to-mp4]]).  See [[subframe1-plp1]].

**The one remaining receiver gap is live real-time throughput** — the correct
bounded loop still runs ~9 s/frame vs the 247 ms budget on CPU.  See
[[gpu-and-algo-speedups]].

## Capture target

Off-air capture target is RF33 (587 MHz): a clean low-gain take already yields
the SLT.  RF30 core (QPSK 6/15) remains a secondary target (coherence > 0.95).
See [[project-plan-status]].

## How It Works

1. **You provide sources** - Articles, papers, notes, books
2. **LLM maintains the wiki** - Summarizes, cross-references, files, updates
3. **Knowledge compounds** - Connections accumulate, contradictions flagged

## Browse

- [[index|Index]] - Catalog of all pages
- [[log|Log]] - Operation history

## Stats

- Sources: 1
- Entities: 2
- Concepts: 4
- Analyses: 32
