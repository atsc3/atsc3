# Preamble Demodulation Toolchain (in progress)

## Date
2026-09-25

## Goal
Extract and decode L1-Basic/L1-Detail from the real RF 33 (587 MHz) capture.

## Added this stage (all exact, cross-checked against A/322 + drmpeg/gr-atsc3)

### `frequency_interleaver.py` (A/322 Section 7.3)
- Exact FI address generator `H_l(p)` using the two LFSRs (interleaving
  sequence R' with wire permutation, symbol offset G).
- Wire permutations and LFSR taps per FFT size from Tables 7.12-7.14 and the
  reference `freqinterleaver_cc_impl.cc`.
- 8K/16K use even/odd wire permutations; 32K uses one permutation per symbol
  pair and inverts H on odd symbols.
- `interleave`/`deinterleave` are exact inverses; addresses are permutations.
- Tests: `tests/test_frequency_interleaver.py` (28).

### `pilot_reference.py` (A/322 Section 8.1.2/8.1.6)
- Reference sequence `r_k` (seed 0x1B, taps `sr^sr>>1^sr>>3^sr>>4`). Verified:
  first 24 values are `110110000000000101000000`.
- Preamble pilot amplitudes (Table 8.6) and DX per preamble_structure
  (Table H.1.1). Structure 27 -> DX=4, A=1.230.
- Tests: `tests/test_pilot_reference.py`.

## Real-capture finding: needs channel equalization
On the RF 33 preamble symbol (structure 27: 8K/GI1536/DX4):
- Timing lock is solid (CP correlation 0.72 at the predicted frame offset).
- Residual CFO ~ -262 Hz.
- **The preamble pilot comb (DX=4) is not visible in the raw magnitude
  spectrum** (pilot vs non-pilot ratio ~0.94). The channel is
  frequency-selective (multipath), so raw FFT magnitudes do not reveal pilots.

This is expected and is the known next step: implement **channel estimation and
equalization** for the preamble (continual pilots CP32-derived + preamble
pilots), de-rotate, then deinterleave and QPSK-demap the L1 cells.

## Validated end-to-end synthetically

`tests/test_preamble.py` plus a scratch integration test prove the complete
receive chain on synthetic data with a random multipath channel:

```
L1-Basic bits -> QPSK map -> frequency interleave -> pilot/CP insertion
  -> multiply by channel -> AWGN-free
receiver: FFT -> pilot-based channel estimate -> equalize
  -> data-cell mask -> frequency deinterleave -> QPSK demap (re_is_b0, invert)
  -> L1BasicCodec(Mode 3) -> EXACT original 200 bits
```

Key facts pinned down:
- Receiver must **frequency-deinterleave** the data cells (`fi_de(..., 0, 8192)`).
- QPSK convention for L1 cells: `b0 = ~(Re>0)`, `b1 = ~(Im>0)` (equivalently
  `Re<0 -> 1`). This is a fixed convention, not a guess.
- Data mask excludes both preamble pilots and the 45 continual pilots -> 4851.

## Real-capture blocker (unchanged, now precisely scoped)
On RF 33 the coherent pilot lock is real (0.60, sharp peak at sample offset +1)
and the channel impulse response is short/plausible, but equalized data cells
still do not form QPSK clusters (angle kurtosis ~0.01). Because the *identical*
chain decodes perfectly in simulation, the residual is a real-signal
impairment the open-loop estimator does not remove:
- residual timing/fractional-CFO drift across the symbol, and/or
- pilot phase noise requiring per-symbol CPE tracking plus Wiener/DFT
  interpolation tuned to the actual delay spread.

This needs a proper fine-sync/tracking block (multi-symbol continual-pilot
tracking), not a one-shot channel estimate.

## Status summary
✓ Complete preamble receive chain implemented and validated in simulation
✓ Fixed QPSK convention and receiver-side FI direction (were the synthetic bugs)
✓ Live L1 decode works on real air (see [[l1-basic-real-air]],
  [[l1-detail-real-air]]); the per-symbol channel estimate proved sufficient,
  and the chain was later generalised to multi-symbol Preambles
  ([[multi-symbol-preamble]])

## See Also
- [[real-atsc3-capture-rf33]]
- [[l1-basic-fec]]
- [[multi-symbol-preamble]]
