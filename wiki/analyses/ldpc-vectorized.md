# LDPC Decoder Vectorized (numpy, Option A)

## Date
2026-09-25

## Problem
The pure-Python min-sum decoder in `ldpc_exact.py` looped over every check node
in Python (12,960 checks for L1-Basic Mode 3) and re-looped all checks for the
syndrome check after every iteration.  The real-air tests took ~75 s each.

## Fix (no new dependency)
Replaced the per-check Python loop with a **packed rectangular-edge** decoder,
still pure numpy:

- `_build_packed()` precomputes, once per code:
  - `check_idx (n_checks, dmax) int32` and `check_mask (n_checks, dmax) bool`
    (padding slots use a dummy column and are masked out),
  - `edge_var (n_edges,)`, `edge_ptr (n_checks+1,)`,
  - `var_edges (n_edges,)` grouped by variable for the scatter step.
  ATSC 3.0 Ninner=16200 codes have max check degree <= 16, so the arrays are
  small.
- `decode()` is now a handful of array ops per iteration:
  `W = total_pad[idx] - M`, `argpartition` for min1/min2 (with the degree-1
  case set to min1), extrinsic sign from the check parity, `np.add.at` for the
  variable-node sum, and a vectorized syndrome check.
- `check_syndrome_bits()` is vectorized the same way.

The exact LLR convention (LLR>0 => bit 1) and the `decode()` return signature
are unchanged, so no caller or test needed changing.

## Result
- 1% raw-error decode: ~0.3-0.5 s -> **0.02-0.08 s** (per code rate).
- Real-air L1-Basic + L1-Detail tests: ~75 s -> **<10 s**.
- Full suite: **346 passed** in ~107 s (was ~180 s+).

Correctness gated by the existing synthetic round-trips and the real-air
regression tests (which assert decoded bits against independently-confirmed
ground truth, plus BCH and CRC).

## Files
- `atsc3lib/ldpc_exact.py` — `_build_packed`, vectorized `decode` and
  `check_syndrome_bits`.

## See Also
- [[l1-detail-real-air]]
- [[l1-basic-real-air]]
