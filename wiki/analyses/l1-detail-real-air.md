# L1-Detail Decoded From Real Air — PLP Configuration

## Date
2026-09-25

## Headline
The receiver now decodes **L1-Detail from a real ATSC 3.0 broadcast** and
parses the full per-PLP configuration (A/322 Table 9.8), end to end from a raw
Preamble symbol. The decoded bits match an independent receiver exactly.

## Result on RF33 (587 MHz NextGen TV multiplex)
BSID 540, 2 subframes, 3 PLPs:

| Subframe | FFT | GI | Symbols | PLP | FEC | Mod | Code rate |
|----------|-----|----|---------|-----|-----|-----|-----------|
| 0 | 8K | 1536 | 34 | 0 | 16K LDPC | 64QAM-NUC | 11/15 |
| 0 | 8K | 1536 | 34 | 16 | 16K LDPC | QPSK | 2/15 |
| 1 | 16K | 1536 | 74 | 1 | 64K LDPC | 256QAM-NUC | 11/15 |

This matches the independent receiver's parse field for field. `L1D_reserved`
is 7 bits all-ones and `L1D_crc` verifies.

## How it was built (clean-room; oracle as referee only)
Geometry came entirely from our CRC-clean L1-Basic: `fec_type=2` (Mode 3),
`size_bytes=64` (Ksig 512), `total_cells=880`, `preamble_num_symbols=0` (NP=1,
so the 7.2.5.2 interleaver is identity). No guessing. The oracle (`Felbs/atsc3`)
was run **once** to produce ground-truth bits for the regression harness; it was
not a source.

## New modules
- `crc.py` — CRC-32 (A/322 6.1.2.2), all-ones init.
- `signaling_fec.py` — shared scrambling / zero-pad / group-wise /
  parity-interleave primitives, parameterised for both L1 blocks.
- `l1_detail.py` — `L1DetailCodec`: QPSK demap + 6.5.2.10 block de-interleave,
  de-puncture, parity + group-wise de-permute, LDPC, BCH, descramble, CRC gate.
- `l1_signaling.py` — **replaced** the invented placeholder with real A/322
  Table 9.2 (L1-Basic) and Table 9.8 (L1-Detail) parsers.

## Constants
All L1-Detail parameters now live in `spec.py` as cited dataclasses:
`L1DetailMode` (Tables 6.19/6.20/6.21/6.22/6.24/6.25) and `L1DetailLengths`;
`PreambleStructure` (Table H.1.1), `L1BasicMode`/`L1BasicLengths`. Consumers
import attributes rather than indexing magic dicts.

## Referee / gates
LDPC converges (0 unsatisfied) **and** BCH syndrome zero **and** `L1D_crc == 0`,
with wrong-variant controls. On RF33 all three pass.

## Tests
- `tests/test_l1_detail.py` — synthetic round-trips for Modes 3-7, cells path,
  real-air fixture.
- `tests/test_air_l1_detail.py` — full Preamble → L1-Basic → L1-Detail → PLP
  config on the saved RF33 symbol.
- `tests/test_l1_signaling.py` — rewritten: parses the real RF33 L1-Basic and
  L1-Detail bits and spot-checks against the independent parse.
- Full suite green (validated).

## Known limitation
The pure-Python LDPC decoder makes the real-air tests slow (~1 min): a full
L1-Basic decode iterates all 12960 check equations per iteration in Python.
Correctness is unaffected; performance is a future optimisation.

## Extensions since
PLP payload extraction (BICM) is implemented - see [[data-plp-payload]]. The
NP = 1 identity interleaver used here was later generalised to the NP-symbol
Preamble (A/322 7.2.5.2) - see [[multi-symbol-preamble]].

## See Also
- [[l1-basic-real-air]]
- [[real-atsc3-capture-rf33]]
- [[l1-basic-fec]]
- [[multi-symbol-preamble]]
