---
created: 2026-08-30
updated: 2026-10-04
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [week-3-4, channel-estimation, equalization, pilots, physical-layer]
---

# Week 3-4: Channel Estimation & Equalization

## Goal

Recover the transmitted constellation from the frequency-selective channel:
extract scattered pilots (A/322), estimate the channel response, equalize the
data subcarriers, and track phase across symbols.

**Test:** compare constellation SNR before/after equalization.

## Original implementation (2026-09-23)

- `pilots.py` — scattered-pilot extraction.
- `equalizer.py` — frequency-domain channel estimation + subcarrier
  equalization (zero-forcing with linear interpolation).
- CLI `atsc3-equalize`.
- Result on WIAV-CD: equalization tightened the constellation clusters ✓.

## Superseded by consolidation (2026-09-25)

The Gen-1 `pilots.py` / `equalizer.py` were removed in
[[consolidation]] — they were early scaffolding that was never on the
validated air path. The live chain re-implemented channel estimation correctly:

- Preamble pilots + CP8 continual pilots, data mask and QPSK demap moved into
  `preamble.py` ([[preamble-demod-toolchain]]).
- Data-PLP channel estimation / equalization lives in `payload.py`
  ([[data-plp-payload]]); the multi-symbol dense estimator
  (`dense_symbol_channel`) is what closed subframe-1 256QAM
  ([[subframe1-plp1]]).
- Fine per-symbol CPE tracking is covered by [[fine-timing-cpe]].

So the concept is air-proven, but the "week 3-4" scaffolding itself no longer
exists. The `atsc3-equalize` entry point was dropped.

## See Also

- [[week-1-2-ofdm-demodulation]]
- [[week-5-qam-demodulation]]
- [[data-plp-payload]]
- [[preamble-demod-toolchain]]
- [[fine-timing-cpe]]
- [[consolidation]]

## References

- ATSC A/322 Physical Layer Specification (pilot patterns, equalization)
- OPENATSC3_PROJECT_PLAN.md (Weeks 3-4 task breakdown)
