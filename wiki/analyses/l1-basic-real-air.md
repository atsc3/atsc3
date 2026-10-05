# L1-Basic Decoded From Real Air (RF33) — bugs fixed

## Date
2026-09-25

## Headline
Our receiver now decodes L1-Basic **from a real over-the-air ATSC 3.0
broadcast**, and the decoded 200 bits match an independent receiver
implementation exactly. The earlier "the capture is too weak" conclusion was
wrong — the fault was in our chain.

## Oracle (clean-room use)
`Felbs/atsc3` (Apache-2.0, a working open-source ATSC 3.0 receiver that decodes
RF33 to 720p) was used only as an **external referee**, never as a source. It
decodes our capture `out/whut_rf33_new.iq` with LDPC converged (4 iterations,
0/12960 unsatisfied) and BCH syndrome zero, proving the capture good. Its
documented lesson matched ours: *"the fault was ours, twice, and 'we need a
better capture' was a comfortable answer that delayed the real one."*

## Bugs found and fixed in our chain

1. **LDPC Type A encoding was incomplete** (`ldpc_exact.py`). The encoder and
   parity-check builder ignored A/322 6.1.3.1 steps (v)-(viii): the M1
   dual-diagonal running XOR, the `_pi` parity interleave, and the step-(vii)
   feedback through the extra Q1 rows. Implemented all of them (Type A and
   Type B now both satisfy `H . encode == 0` for all 12 rates).

2. **QPSK mapping + 6.5.2.10 block de-interleave were missing** (`l1_basic.py`).
   Annex C.1.1 maps `y1 -> sign(I)`, `y0 -> sign(Q)`; 6.5.2.10 writes the NFEC
   bits into a 2-column block interleaver (all y0 bits, then all y1). Added
   `L1BasicCodec.cells_to_llr` / `decode_cells`.

3. **Frequency-interleaver symbol-offset register width** (`frequency_interleaver.py`).
   The offset generator G is `log2(Nmax)` bits wide, not `pn_degree`. The
   address sequences now match the reference bit-for-bit for all four symbol
   indices.

4. **Common continual pilot indices were absolute, not relative**
   (`preamble.py`). CP8 is derived from CP32 but must be shifted by the carrier
   origin `(NoCmax - NoC)//2`. Our data-cell mask now matches the reference's
   4851-cell set exactly (A/322 Table 7.2).

5. **Preamble carrier origin / NoCmax** were hard-coded inconsistently; now
   derived from A/322 Table 7.1.

Result: our full chain (FFT -> pilot channel estimate -> equalize -> frequency
de-interleave -> QPSK demap -> block de-interleave -> LDPC -> BCH) recovers the
exact 200 bits, independently confirmed.

## Code-quality fix: one cited constants module
The scratch experiments were full of unexplained numbers. All spec constants
now live in **`atsc3lib/spec.py`**, each with its A/322 clause/table citation:
sample rates, bootstrap geometry/seeds, Table 7.1 carrier counts, Table H.1.1
`preamble_structure` map (including the 32K doubled GI rows), Table 8.6 pilot
amplitudes, Table 7.2 cell counts, Table D.1.1 CP32, the reference-sequence and
scrambler parameters, and the L1-Basic tables (6.17/6.20/6.21/6.23/6.24).
`preamble.py`, `pilot_reference.py`, `bootstrap.py`, `frontend.py` and
`l1_basic.py` now import from it.

## Regression test
`tests/test_air_l1_basic.py` decodes a saved 64 KB real Preamble symbol
(`tests/data/rf33_preamble_symbol.npy`) and asserts the 200 bits equal the
independently-confirmed ground truth. It exercises the whole chain in one test.

## Status
✓ L1-Basic decoded from real air, matching an independent receiver
✓ Full suite: 348 passed
✓ Constants centralised in `spec.py` with citations
⚠ Old captures (`at3_587.iq`) fail even for the oracle — capture-limited; use
  `out/whut_rf33_new.iq`
⚠ L1-Detail and the PLP payload are the next rungs

## See Also
- [[real-atsc3-capture-rf33]]
- [[l1-basic-fec]]
- [[bch-outer-code]]
- [[exact-ldpc-and-group-interleaver]]
