# TODO — LDPC + demapper/pool enhancements

Saved 2026-10-05. Source context: measurement and design work on the clean
RF33 subframe-1 PLP-1 fixture (`tests/data/rf33_sf1_y.npy`, 256QAM 11/15,
64800-bit LDPC, 117 FEC blocks). See also `wiki/analyses/gpu-and-algo-speedups.md`
and `wiki/analyses/subframe1-plp1.md`.

## Baseline (measured, 2-core/4-thread i5-4278U)

| Stage | Serial (1 worker) | Per block |
|---|---|---|
| Demap (`_demap.c`) | 6.2 s | 53 ms |
| LDPC (`_ldpc.c`) | 5.6 s | 48 ms |
| BCH (`_bch.c`) | 2.7 s | 23 ms |
| Orchestration / HTI | 3.3 s | — |
| Pool build (75x16K FFT + dense CH) | 0.9 s | — |
| `DataPlpChain` setup | 0.4 s per PLP per frame | — |
| **Total (decode, 1 worker)** | **~15.1 s** | |
| Total at 2 workers | ~7.3 s | |
| FEC scaling 1 -> 4 workers | 15.6 -> 7.5 s (sublinear, bandwidth-bound) | |

All 117 blocks converge on the first alpha pass; the `ALPHA_LADDER` never
re-runs on a clean signal.

## Track A — LDPC fast path (marginal links; output-identical)

Target: `DataPlpChain.decode_cells` (`payload.py:567-598`). ~0% on a clean
signal; large win per failed block on marginal links (up to 3x100 iterations
re-run per alpha otherwise).

- A1. Convergence-first cap before the ladder: try `alpha=1.0` with a small
  cap (`FAST_ITERATIONS`, candidate 16); return on convergence; else fall
  through to the existing `ALPHA_LADDER` unchanged. Output-identical.
- A2. Skip the ladder when the block is far from any codeword, gated on
  `ATSC3LDPCExact.n_unsatisfied()` (`ldpc_exact.py:405`); threshold a named,
  graph-derived constant, never discards a recoverable block.
- A3. Confirm/keep `_fec_workers()` (`payload.py:34`) default; optional
  physical-core preference.
- A4. Tests in `tests/test_data_plp.py::TestDataPlpChain`: fast path == ladder
  output on synthetic 256QAM/64QAM 11/15; a block converging only above
  `FAST_ITERATIONS` recovers via fallback; chance-level block is skipped and
  reported non-converged.

## Track B — Demapper + pool speed-ups (clean-signal bottleneck)

- B1. Fuse `_demap.c`'s two distance passes (pass 1 currently only computes
  sigma^2, line 96-111; pass 2 recomputes distances, line 118-144): update
  per-bit minima during pass 1, compute sigma^2 once, then a cheap divide loop.
  Removes one distance pass; ~15-30% of demap depending on modulation.
  Bit-identical. Drop the unused `d2` alloc.
- B2. Batch the demap call: optional block length in `_demap.demap_llr` that
  resets the sigma^2 accumulator every N cells (default preserves current
  behaviour); thread through `demod.py` and call once per subframe, keeping
  per-8,100-cell sigma^2. Bit-identical. Backward-compatible API.
- B3. Cache per-symbol carriers in `build_data_symbol_pool` (`dense=True`):
  precompute each symbol's relative carriers once (one batched 2-D FFT over the
  subframe body) instead of re-FFTing `dy` neighbours per symbol in
  `dense_symbol_channel`. Bit-identical. Riskiest item (touches the dense path
  that made 256QAM work); fall back to per-symbol if any sample changes.
- B4. `setup.py:_kernel`: add `extra_compile_args=["-O3"]` to all four kernels.
  No `-march=native`, no `-ffast-math` (would break bit-identity).
- B5. Benchmark/measurement script reporting the per-stage split; record
  numbers in the wiki (not a timing-dependent pytest).

Estimated clean-signal effect: ~15.1 s -> ~12.5 s serial (~15-18%). Does **not**
reach real-time; a cleanup rung, not a live enabler.

## Verification

1. `cd /tmp/opencode && .venv/bin/python -m pytest [folder] -q`
   -> expect 729+ passed (cwd off repo-root avoids the pre-existing
   `ac4bindings` namespace-shadowing failures).
2. Bindings differentials unchanged: `test_demod_demap_ref`, `test_demod_demap_c`,
   `test_fec_ldpc_ref`, `test_fec_ldpc_c`.
3. `tests/test_subframe1.py::TestSf1Air::test_plp1_decodes` still 117/117.
4. `make check` for touched Python; wiki/log/meta updated with before/after.

## Constraints

- All changes bit-identical; if any differential test needs a loosened
  tolerance, stop and reconsider.
- B2 changes the C API (must stay backward-compatible); B3 is the riskiest.
- Track A does not move the clean-signal number.

## Follow-on (separate, not part of A/B): sub-second live on 8 cores / GPU

Broader throughput discussion, not yet scoped as tasks:
- **Prerequisite (mandatory, CPU side): serial-floor fixes.**  With the GPU on
  FEC the remaining per-frame CPU work is the blocker, not core count: cache
  `DataPlpChain`/tables/plans once per lock; hold the L1 lock across frames
  (acquisition re-run is ~14 s/window); pipeline capture<->decode<->network;
  batch/parallel pool build; speed up the 2.7 s BCH stage.
- True-live target is < 247 ms/frame (RF33 period); sub-second (< 1 s) is
  near-live and still drops frames.
- GPU route: raw CUDA extension (`gpubindings`) via `try: import`,
  differential-tested against the C/NumPy kernels. CPU remains the required
  fallback. GPU accelerates demap+LDPC+BCH only, not the serial floors.
- Hardware target selected (single box, **8-core CPU + modern NVIDIA GPU**,
  e.g. Ryzen 7 7700X + RTX 5070 12 GB / 672 GB/s, current Blackwell drivers):
  see `meta/HARDWARE-live-rig.md`.  The 16-core requirement was dropped — with
  GPU FEC, core count is not the live lever.
