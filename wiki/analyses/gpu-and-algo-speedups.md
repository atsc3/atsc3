---
created: 2026-10-03
updated: 2026-10-03
sources: [local measurements, AGENTS.md, README.md]
tags: [performance, live, ldpc, demap, gpu, hardware]
---

# GPU and Algorithmic Speed-Ups for Live Processing

## Question

Would a GPU get the receiver to live processing, as opposed to a many-thread
CPU?  **Yes for the GPU, but it is optional — and it is the last rung, not the
first.**  The measured hot path says the cheapest wins are algorithmic (the
LDPC alpha ladder, batching the per-symbol FFT), then CPU threading, and only
then a GPU for the marginal link.  VRAM is not the constraint; bandwidth is.

## The frame budget

One RF33 frame is **247.1 ms**: 1,708,032 main-rate samples at 6.144 MHz,
bootstrap to bootstrap (measured on air; see [[subframe1-plp1]]).  Live
processing means decoding a frame in less than that.

## Measured baseline (this host: 2-core/4-thread i5-4278U)

Taken on the RF33 subframe-1 PLP-1 fixture (`tests/data/rf33_sf1_y.npy`,
256QAM-NUC 11/15, 64800-bit LDPC, 117 FEC blocks):

| Stage | Measured | Per 117-block frame |
|---|---|---|
| Max-log demap, `demodbindings` (C) | ~170 ms/block (256QAM, 8100 cells) | ~20 s |
| LDPC min-sum, converged | 1.1 ms/block | ~0.13 s |
| LDPC min-sum, 100 iterations, not converged | 621 ms/block | ~73 s / alpha |
| Pool build (75 x 16K FFT + dense channel) | ~1 s/subframe | ~1 s |
| Whole frame (current code, 4-thread FEC pool) | — | ~9 s |

The alpha ladder in `DataPlpChain.decode_cells` (`payload.py:582`) re-runs the
whole decode for each of `ALPHA_LADDER = (1.0, 0.85, 0.75)` until one
converges, so a non-converged block can cost up to **3 x 621 ms**.

## Where the time actually goes

- **Demap is already wired to C** (`nuc.py:124` calls `demodbindings` on the
  receive path; the NumPy `_demap_llr_numpy` is only the differential
  reference, not the runtime).  It is ~170 ms/block — a few times over budget,
  but not the main wall.
- **LDPC non-convergence dominates.**  A *converged* block is 1.1 ms; a
  non-converged block is 621 ms per alpha try.  On a clean link most blocks
  converge early, so the honest converged frame cost is closer to
  `117 x 1.1 ms + demap` (~300 ms), not the 9 s figure.  Most of the observed
  9 s is the alpha ladder plus Python orchestration.
- **The pool-build loop is sequential** (a Python `for` over 75 symbols, each
  a 16K FFT).  Batched as one 2-D FFT it is 74 ms on a single core.

This matters: no GPU decision should be made before measuring the all-117
converged path and the single-alpha path, because the current 9 s is not the
LDPC kernel's floor.

## Why a many-thread CPU alone may be enough

- The C kernels already release the GIL, and `_parallel_fec_blocks` threads
  independent FEC blocks across cores (`payload.py:49`).  Scaling is available
  today.
- For a clean lighthouse link (74/74 PLP-0, 117/117 PLP-1 per AGENTS.md), a
  16-core host should bring the converged frame under 52.7-247 ms on CPU alone.
- The earlier ~1000x gap is a *marginal-link* number (many-iteration LDPC),
  not the clean-link number.  CPU is the right first answer.

## Why a GPU helps when the link is marginal

Batched normalized-min-sum LDPC plus a batched max-log demap is the canonical
GPU workload (srsRAN / aerial do 5G LDPC this way); 117 independent codewords
map cleanly onto SMs.

**VRAM is small.**  From the actual Tanner graphs:

| PLP | codeword | graph+workspace per codeword | 117-block frame |
|---|---|---|---|
| PLP-1 256QAM 11/15, 64K | n=64800, 284759 edges | 4.55 MB | ~0.53 GB |
| PLP-0 64QAM 11/15, 64K | n=64800, 278999 edges | 4.60 MB | ~0.54 GB |
| PLP-16 QPSK 2/15, 16K | n=16200, 45359 edges | 0.90 MB | — |

Even with the subframe FFT/channel (75 x 16384 ~ 20 MB) and double-buffering,
the working set is well under 2 GB.  The GPU is **bandwidth- and SM-bound, not
capacity-bound**:

```
117 codewords x 100 iterations ~ 66.6 GB of traffic per frame
  RTX 4060  (288 GB/s) -> ~231 ms/frame
  RTX 4070  (504 GB/s) -> ~132 ms/frame
  RTX 4080  (717 GB/s) ->  ~93 ms/frame
  RTX 4090  (~1 TB/s)  ->  ~66 ms/frame
  RTX 5090  (~2 TB/s)  ->  ~33 ms/frame
```

Frame budget 247.1 ms, worst case.  **16 GB VRAM and 500+ GB/s is the sweet
spot;** anything above is wasted on this workload.

## Optionality: GPU as a dispatch, never a dependency

`fecbindings` and `demodbindings` are declared, required dependencies; the
NumPy implementations are the differential reference, not a runtime fallback.
A GPU rung should follow the same convention:

```
fecbindings.decode_ldpc(...)            # required C path (today)
  -> try: import gpubindings; gpubindings.decode_ldpc(...)   # optional
```

A `try: import` dispatch keeps the receiver byte-identical without a GPU, and
the GPU kernel is differential-tested against `fecbindings` / `nuc` exactly as
the C kernels are tested against NumPy.  The GPU is an accelerator, never a
required dependency.

## Build tiers (for reference; Oct-2026 prices)

**Tier A — GPU-assisted (CPU-fallback capable):** Ryzen 9 9950X (16C/32T),
X870E, 64 GB DDR5-6000, RTX 4070 Ti Super 16 GB (504 GB/s) or 4080 Super,
2 TB NVMe, 1000 W Gold, ATX + 360 AIO.  **~$2,300-3,100.**

**Tier B — CPU-only (portable, cheaper):** Ryzen 9 9950X or Threadripper
7960X, 64-128 GB DDR5, no dGPU.  **~$1,500-3,200.**

A "large CPU" is **not** needed: once FEC and demap are off the CPU, it only
runs acquisition, L1 and orchestration — all trivial.  Spend on GPU bandwidth
and fast RAM, not core count.  Note the Oct-2026 GPU price spike (RTX 5090
well above MSRP); a ~$0.55/GPU-hr rental is the cheap way to validate the GPU
rung before buying, given a few minutes of GPU use per hour of air time.

## Architecture

The RSP1B is on the capture host (`lsusb`, this machine).  Two clean options:

- **Single box** — move the SDRplay (USB) to the new host; one machine does
  capture + FEC.  Simplest.
- **Split** — capture here, ship IQ or equalised cell pools to the GPU host
  over LAN; the GPU host is a pure FEC/demap service.  Keeps `sdrbindings`
  where the radio is and the GPU dependency out of the capture path.

## Recommendations, in order

1. **Measure honestly first.**  Run all 117 blocks through the current
   converged path and the single-alpha path on `rf33_sf1_y.npy`; establish the
   real per-frame wall time and its split (demap / LDPC-converged /
   LDPC-failed / alpha retries / orchestration).
2. **Algorithmic LDPC fixes** (no new dependency): single-alpha fast path with
   a ladder fallback only when near convergence; early exit.  Verify
   convergence is unchanged on the air fixtures.
3. **Batch the 75-symbol FFT** into one block FFT; keep `demodbindings` as the
   only demap path.
4. **Thread / pipeline**: saturate FEC-block parallelism, pipeline frame N+1
   capture against frame N decode.
5. **Only if 1-4 miss the budget for the target link**, prototype
   `gpubindings` on a rented GPU and differential-test it before buying.
6. **Then buy**, or stay CPU-only if step 2 already won.

## Related

- [[ldpc-vectorized]] — the NumPy packed-edge decoder that the C kernel
  replaced.
- [[live-ldpc-test-results]] — earlier live LDPC timing.
- [[optimization-symbol-detection]] — the earlier (CP-correlation) speed-up
  plan; this page supersedes its GPU-CuPy framing for the FEC-bound chain.
- [[subframe1-plp1]] — the 117-block 256QAM frame used for these measurements.
- [[project-plan-status]], [[overview]].
