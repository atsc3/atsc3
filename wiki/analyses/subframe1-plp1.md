# Subframe 1 (16K FFT) and the PLP-1 link limit

## Date
2026-09-26

## Why
Subframe 0 was the only subframe ever demodulated.  RF33's subframe 1 carries
PLP-1 (256QAM-NUC 11/15, 64800-bit LDPC, 3 TI blocks x 39 columns) and is four
times the size of subframe 0.  It is the first live target for the normal-frame
FEC added earlier.

## What was needed
1. **16K pilot/data-cell model.**  A/322 Tables 7.3-7.6 and Annex F, per FFT
   size and scattered-pilot pattern, plus Table D.1.4/D.1.5 additional
   continual pilots.  Extracting these from the PDF is error-prone: the
   `SPx_4` groups print three rows and a naive read silently loses the first
   additional CP.
2. **A/322 7.3 frequency-interleaver reset rule.**  Rule 2: on the first symbol
   of every subframe after the first, the symbol-offset and interleaving
   sequence generators reset.  So the counter origin is 1 for subframe 0 (the
   Preamble is frame symbol 0) and **0 for subframe 1**.
3. **Subframe geometry + sample alignment.**  Subframe 0 draws geometry from
   L1-Basic; later subframes carry their own L1-Detail fields.  Subframe 1's
   first sample is after the Preamble and all 35 subframe-0 symbols.

## How the tables are sourced
Extracting from the PDF is the nightmare it looked like, so the per-FFT
pilot/data-cell tables are **fetched from the pinned independent transcription**
`drmpeg/gr-atsc3@000b86a3` (`lib/params.h`, `lib/pilotgenerator_cc_impl.cc`)
by `tools/fetch_pilot_tables.py`, written to `atsc3lib/data/pilot_tables.json`.

The fetch is gated, not trusted: `verify()` rebuilds the pilot grid from first
principles (scattered pilots for every lattice phase, edge pilots, the common
CP set, the fetched additional CPs) and requires the **constant-data-carrier
identity** of A/322 8.1.4.1 - the data count must be the same for every phase.
That identity independently confirms Table 8.3's allowed patterns: a pattern is
allowed for an FFT size exactly when its count is phase-invariant.  This is the
gate that catches the `SPx_4` one-row-short mis-read.

## Result
- `build_data_symbol_pool` generalises the cell pool: any FFT/GI/pilot/cred, an
  explicit FI symbol-counter origin, no Preamble cells.  `build_cell_pool` is
  now the subframe-0 special case (`fi_offset=1`).
- `receiver.subframe_geometry` / `decode_plp_payload(subframe=...)` route to
  the right geometry; `decode_subframe_plp` runs a no-Preamble subframe.
- **The 16K/SP4_4 pool closes at exactly 956179 cells on real air**, matching
  the reference receiver, and each symbol's pilot count matches Tables 7.4/7.5.
- A latent L1 parse bug was fixed: `L1D_plp_HTI_cell_interleaver` was read but
  discarded, so `hti_cell_interleaver` was always None.
- The `atsc3-decode` CLI gained `--subframe`.

## PLP-1 does not close — a LINK margin result, not a chain defect

**Corrected 2026-09-27.**  The earlier claim that PLP-0/PLP-1 are "1.4-2.8 dB
short" was measured with a broken demapper and is **retracted**.  PLP-0 (64QAM
11/15) now decodes 53-60/74 off air and produces the SLT (see
[[rf33-lighthouse-slt]]); the blocker was a demapper **scale** bug, not the
link.

An intermediate claim — that subframe-1 PLP-1 (256QAM-NUC 11/15) is a **chain
defect** because it sat at "21.2 dB MER, above the chain's synthetic 20 dB
threshold" — is also **retracted**, as a **units error**.  Nearest-point MER is
**not** Es/N0: for 256QAM 11/15 it overstates true SNR by ~2.5-4 dB.  A
synthetic point at true 18 dB reads 22.3 dB nearest-point MER and fails 0/4;
true 19.5 dB is the ~50% cliff.  So the "21.2 dB" was never above the threshold
in true-SNR terms.

Every table is now exonerated.  Our pool closes at 956179 exactly like the
reference's *corrected* model (D.1.4 = `{3460, 5768, 11452}`, SBS ACTIVE vs
TOTAL, null-offset — all implemented); 16K FI, the HTI read order, the
group/block interleaver tables and the pilot sets match the oracle; and this
chain recovers oracle-encoded 256QAM 11/15 codewords at the same SNR.  The
decisive test: the **oracle's own `m13_sf1` builder + decoder also returns 0/6,
median ~7919 unsatisfied (chance) on our fixture**, with the pool 956179 and
coherence 0.985 — two independent decoders failing identically is a capture-SNR
verdict.  CPE does not help (0/117).

The current good-antenna capture (RF33 587 MHz, gain 12) measures **20.6 dB
nearest-point MER** on subframe 1 — ~17-18 dB true, at the LDPC cliff.  The
oracle needed **21.7-22.9 dB nearest-point MER** (its own capture) for 117/117,
about **2 dB more** than we can currently capture.  The next step is a stronger
record (better aim/gain/LNA), not a table change.

**The old subframe-1 fixture was 9.3 dB nearest-point MER** — unusable for
256QAM.  This is why earlier "both our pool and the oracle builder fail both
decoders" tests proved nothing.  It was replaced 2026-09-27 from the fresh
20.6 dB capture.

## What the capture search actually surfaced (2026-09-26)
The strongest stations on this antenna are **not RF33**, and chasing them
exposed two receiver gaps rather than a link result:

- **RF30 (WIAV, 569 MHz)** and **RF25 (539 MHz)** are a *different machine*:
  LDM (two layers on the same cells), **CTI** (convolutional time interleaving,
  not the HTI twisted block), **Ninner = 64800**, and a **two-symbol Preamble**.
  Neither is supported.
- Both signal **L1-Basic Mode 1**, the only mode that uses A/322 6.5.2.7 parity
  **repetition**.  `L1BasicCodec` mishandled it: `cells_to_llr` truncated to
  `Nfec/eta` cells (dropping the repeated parity), and `decode` placed the
  repeat block *after* the punctured tail instead of after the information.
  A/322 6.5.2.9's transmitted word is
  `[Nouter | Nrepeat repeated parity | Nfec - Nouter tail]`; the repetition is
  the **first** Nrepeat permuted-parity bits, so their LLRs add onto the same
  codeword positions.  A signalled/derived identity catches it: without the
  repetition term the arithmetic gives `Nfec/eta = 1984` cells against the
  `3820` cells A/322 prints for Mode 1 (Table 6.17).
- **Fixed** (L1-Basic and L1-Detail, same clause) and gated: Table 6.17's 3820
  cells for L1-Basic, and Ksig 336 bits -> exactly the 3611
  `L1B_L1_Detail_total_cells` that an independent receiver reads on RF30.
  **RF30's L1-Basic now verifies off air (BCH + CRC OK)**, and - after the
  multi-symbol-Preamble work in [[multi-symbol-preamble]] - so does its
  L1-Detail.
- **The 256QAM streams are on the LDM enhanced layers**: RF25 PLP-1 at
  256QAM-NUC 7/15 and RF30 PLP-1 at 64QAM-NUC 6/15, both behind LDM
  cancellation and CTI.  RF33's PLP-1 (256QAM 11/15) remains the only 256QAM
  reachable without those rungs, and it is signal-limited.

## Gate
- `tests/test_pilot_tables.py`: rebuilds the identity from the banked JSON.
- `tests/test_fetch_pilot_tables.py`: network-gated - the fetched tables equal
  the banked ones and the identity gate passes for all 240 rows.
- `tests/test_subframe1.py`: 16K/SP4_4 pilot counts, null split, pool
  arithmetic, and (with the real-air fixture) the exact 956179-cell pool.
- `tests/test_bootstrap.py`: the `versions` restriction.
- `tests/test_l1_basic.py` / `test_l1_detail.py`: Mode-1 repetition geometry
  (3820 cells; 3611 signalled) and round-trips.

## Remaining
- **Subframe-1 PLP-1 (256QAM-NUC 11/15)** — 0/117 at ~20.6 dB nearest-point MER
  (~17-18 dB true, at the LDPC cliff).  A **link-margin** result, not a cell
  model defect: our pool/FI/HTI/tables match the oracle and the oracle's own
  decoder fails identically on our fixture.  Next: a ~2 dB stronger record.
  **256QAM is in scope.**
- **RF33 PLP-0 (64QAM 11/15)** — done; 53-60/74, SLT + SystemTime off air.
- **Payload still a different machine** for RF25/RF30: CTI (7.1.4), LDM core
  demapping, and Ninner = 64800.  The Preamble (2-symbol) and L1-Basic/L1-Detail
  Mode-1 repetition are now done - see [[multi-symbol-preamble]] - and the two
  front-end stages the core path needs are done too - see
  [[fine-timing-cpe]].  These carry the other 256QAM (RF25) and the SLT-bearing
  core layers; the signalling is margin-independent, so RF30 64QAM-NUC 6/15 is
  the higher-value target.

## Scope: retracted

The earlier "deferred for high-order margin" scope has been **retracted**.  It
was based on a demapper scale bug; 64QAM 11/15 (RF33 PLP-0) now decodes off air
and 256QAM is back in scope.  See [[rf33-lighthouse-slt]].

## See Also
- [[normal-frame-fec]]
- [[data-plp-payload]]
- [[multi-symbol-preamble]]
- [[rf33-lighthouse-slt]]
