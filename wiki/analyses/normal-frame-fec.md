# Normal-Frame FEC (Ninner = 64800)

## Date
2026-09-26

## Why
Every decodable real-air PLP so far is a short-frame (16200) code.  RF33's
PLP-1 lives in subframe 1 and signals `L1D_plp_fec_type = 1`, i.e. the 64800-bit
normal frame, as do most stations.  The short-frame chain cannot touch it, so
the FEC and bit-interleaver tables for normal frames are the prerequisite.

## What was needed
A/322 has two frame lengths sharing Section 6.1.3 (LDPC) and 6.2 (bit
interleaver) but with disjoint tables:

- **Annex A.1** parity-check address matrices (Tables A.1.1-A.1.12)
- **Annex B.1** group-wise interleaving permutations (Tables B.1.1-B.1.6)
- Table 6.5 Type A `M1/M2/Q1/Q2` (rate 7/15 is Type A here but Type B at 16200)
- Tables 6.8/6.9 block-interleaver type, 6.10/6.11 block parameters

None were in the repository.  Both Annex pages are **two-column**; naive
pdftotext/pdfplumber reads interleave the columns and corrupt every row
boundary, so the extractors use PyMuPDF line boxes and read the tables
column-major.

## Extraction discipline
The tables are machine-extracted, never typed, and gated:

- **A.1 gates**: row count equals `K/360` (Type B) or `K/360 + Q1` (Type A,
  because 6.1.3.1 steps (vii)-(viii) feed the M1 parity back through extra
  rows); every address is inside `[0, Ninner-Kinner)`; every row is strictly
  increasing; the 360-degree expansion touches every parity accumulator.
- **B.1 gate**: each table prints an identity header row `0..179`, which is a
  free per-table checksum of the column ordering; every extracted sequence must
  be a bijection of `0..Ngroup-1`.
- **Printed example**: the 256QAM block-interleaver example in 6.2.3.1
  (`q0.. = v0,v7920,...`; Part 2 begins `q63360 = v63360,v63540,...`) is an
  explicit test.
- **C1 control**: G1-G4 cannot see row *order*, so the control uses the layout
  fact that printed (column-major) two-column rows run in non-increasing
  weight; a row-interleaved read breaks that order.  All six two-column pages
  fail C1.
- The oracle's `spec_ldpc64k`/`spec_bitint` were used as referee only: the
  banked tables and the block-interleaver type matrix match element-for-element.

## Independent source cross-check
Rather than trusting the PDF extraction alone, the banked tables are also
compared against an independent public transcription: `drmpeg/gr-atsc3`,
a GNU Radio ATSC 3.0 **transmitter** pinned to commit `000b86a3`.  Its C arrays
reproduce all 12 Annex A.1 tables and all 72 Annex B.1 permutations
**element-for-element**.  `tools/crosscheck_web_tables.py` fetches that source
at run time (never committed), asserts equality, and `tests/test_web_tables.py`
gates it (skipping when offline).  The A/322 PDF remains ground truth; the web
source is a second, independent witness.

## Sources and caching
`tools/spec_sources.py` is the single registry of inputs: the official A/322
and A/330 PDF URLs, the pinned reference-witness repo/commit, and a download
cache (`/tmp/atsc3lib-spec`, override via the `pdf` argument).  The extractors
take an *optional* PDF argument and, when run with no arguments, fetch the
official A/322 PDF and rewrite the banked table in place.  Nothing
downloadable is committed except the extracted numeric tables.

## Implementation
- `tools/pdf_layout.py` holds the shared PyMuPDF layout record/thresholds;
  `tools/extract_ldpc64k.py` and `tools/extract_bicm.py` use named constants
  (`RATE_DENOM`, `GROUP_SIZE`, `FOOTER_Y`, `COLUMN_GAP`, `CAPTION_GAP`) and
  dataclasses (`CodeParams`, `TableRead`, `AnnexBlock`) with no inline
  spec or layout literals.
- `tools/crosscheck_web_tables.py` re-derives the tables from the pinned
  upstream source and compares.
- `ldpc_exact.py` gained `NINNER_NORMAL`, `TypeAParams` dataclasses for both
  lengths, and `TYPE_B_QLDPC_64800` (rate 6/15 = 108 ... 13/15 = 24).
- `group_interleaver.py` gained Table 6.8/6.9 block-type sets and the Type A/B
  block parameter tables; note Type A FEC rates are not always Type A block
  (e.g. 16QAM 5/15, 64QAM 7/15).
- `payload.DataPlpChain` selects the frame length from `L1D_plp_fec_type`
  (`PLP_NINNER`) and derives `Mouter` from the BCH codec (192 vs 168).
- All modulation strings now come from the shared `nuc` constants
  (`QPSK`, `QAM16`, ...) rather than repeated literals.

## Gate
`tests/test_normal_fec.py` (63 tests): every rate's table geometry, every rate
encodes to a zero-syndrome codeword, and interleaved error-injection decode is
exact for QPSK/16QAM/64QAM/256QAM across Type A and Type B FEC and Type A/B
block interleavers.  `tests/test_web_tables.py` adds the independent-source
cross-check.  The 16200 path is unchanged (same tests still pass).

## Remaining
- Live normal-frame PLP: PLP-1 needs subframe-1 (16K FFT) demodulation and the
  A/322 7.3 frequency-deinterleaver reset rule - both now implemented; the
  subframe-1 cell pool closes at exactly 956179 cells on real air.  The PLP is
  still link-limited on every capture held (~16.4 dB MER vs ~22 dB for 256QAM
  11/15); see [[subframe1-plp1]].
- ROHC decompression (A/330 §6) and ROUTE/MMTP media remain.

## See Also
- [[data-plp-payload]]
- [[subframe1-plp1]]
- [[multi-symbol-preamble]]
- [[consolidation]]
