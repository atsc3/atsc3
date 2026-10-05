# Multi-Symbol Preamble (A/322 7.2.5)

## Date
2026-09-26

## Why
Every station that decoded before (RF33) transmits a **one-symbol** Preamble.
The strongest multiplexes on this installation, RF30 (WIAV-CD, 569 MHz) and
RF25 (539 MHz), signal `L1B_preamble_num_symbols = 1`, i.e. **NP = 2**.  L1-Basic
fits in the first symbol but L1-Detail does not: RF30's 3708 L1-Detail cells
overflow its 1031 free cells, so the remainder live in the second Preamble
symbol.  Before this, the receiver stopped at `multi-symbol Preamble not
implemented` and the whole per-PLP configuration of those stations was
unreachable.  This is LOS-independent, unlike the 256QAM payload on RF25 behind
it.

## What the spec says
- **7.2.5.1** The Preamble is one or more symbols; all share the FFT/GI/pilot
  pattern signalled by the bootstrap.  The first symbol uses the cred_coeff = 4
  carrier count; later symbols use `L1B_preamble_reduced_carriers`.
- **7.2.5.2** L1-Basic cells go only in the first symbol.  L1-Detail cells are
  interleaved and mapped to the remaining first-symbol cells and then to the
  later symbols.  The interleaver is a block interleaver with `Lc = NP` columns
  and `Lr = floor(total_cells / NP)` rows: the first `Lc*Lr` cells are written
  row-wise and read column-wise (`y(i*Lr + j) = M(j*Lc + i)`); any remainder is
  appended unchanged.
- **7.3** The frequency-interleaver symbol counter continues across the
  Preamble (the first Preamble symbol is frame symbol 0); the first data symbol
  of subframe 0 therefore has counter origin `NP`, not 1.
- **Table 7.2** gives the available data cells per Preamble symbol for
  cred_coeff 0..4; the data-cell mask had to be generalised off cred_coeff 4.

## Implementation
- `preamble.preamble_symbol_cells` demodulates **any** Preamble symbol at a
  given FI counter index and cred_coeff.  `preamble_l1_cells` is now the
  first-symbol special case.
- `l1_detail.preamble_block_deinterleave` inverts 7.2.5.2.
- `receiver._preamble_l1_detail_cells` gathers the first symbol's post-L1-Basic
  cells and each later symbol's cells, then `decode_signaling` block
  de-interleaves when `NP > 1`.
- `payload.build_cell_pool` now takes NP / reduced-carriers / L1-Basic cells:
  subframe-0 data symbols start after all NP Preamble symbols and the FI origin
  is NP; the spare cells come from the last Preamble symbol.
- `receiver._subframe0_geometry` reports `fi_offset = NP` and
  `n_preamble_symbols`.

## Gate
The signalled/derived identity is exact: RF30 signals
`L1B_L1_Detail_total_cells = 3708` and the L1-Detail chain at Ksig 352 bits
yields 3708 cells; the block interleaver is verified as an exact inverse for
NP = 1 and NP = 2.  The real-air gate is `tests/test_air_multi_preamble.py`:
the two Preamble symbol spans of RF30 (fixture `rf30_preamble_pair.npy`) decode
L1-Basic Mode 1 and L1-Detail with **BCH + CRC both OK**.  The CLI now prints
RF30's full configuration (BSID 9100, two LDM-layer PLPs) instead of stopping.

## Scope
This closes the **signalling** rung for multi-symbol-Preamble stations.  Their
PLP payloads are a separate, larger machine: LDM (two layers on shared cells),
CTI (convolutional time interleaving, A/322 7.1.4) and Ninner = 64800 on the
PLPs.  RF30's PLP-1 is 64QAM-NUC 6/15 and RF25's is 256QAM-NUC 7/15; the latter
is out of scope (needs LOS), the former is the next non-LOS payload target.

## See Also
- [[subframe1-plp1]]
- [[l1-detail-real-air]]
- [[normal-frame-fec]]
