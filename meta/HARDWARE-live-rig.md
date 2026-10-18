# Hardware target — single-box live receiver (CPU + GPU)

**Documentation:** ``docs/hardware.rst`` is the doc-facing summary of this
file (host spec, candidate table and measured prototype).  This file remains
the engineering record with the full derivation.

Decision recorded 2026-10-05 (revised same day: 8 cores + RTX 5070).  Goal:
sub-second (ideally true-live < 247 ms) decode of the RF33 256QAM PLP on one
machine, with the SDRplay RSP1B and the `atsc3lib` receiver.  See
`meta/TODO-ldpc-demod-enhancements.md` (follow-on) and
`wiki/analyses/gpu-and-algo-speedups.md`.

**Re-measured and reconciled 2026-10-17** — the serial-floor work landed
2026-10-16/17 and the CPU numbers quoted in the earlier drafts of this file and
of `meta/TODO-ldpc-demod-enhancements.md` were stale (see "Re-measured split"
below).  The decision is unchanged; the arithmetic behind it is now consistent.

## Decision

- **One box, not split.**  GPU and CPU together; the RSP1B (USB) moves to it.
- **CPU: 8 cores / 16 threads** (Ryzen 7 7700X / 9700X; 6c/12t 7600X is the
  acceptable floor).  **Not 16 cores.**  Once the GPU does the FEC arithmetic,
  the CPU only orchestrates and builds the pool, so core count is not the
  lever — high single-thread/IPC matters more, and an 8-core box is several
  hundred dollars cheaper.  16 cores was an artifact of the CPU-only plan.
- **GPU: a modern NVIDIA card on current drivers** — Ada (RTX 40) or Blackwell
  (RTX 50).  No Pascal: CUDA 13 dropped offline compilation below CC 7.5, so a
  GTX 1080 Ti would force the CUDA 12.x line.  A current card avoids that.
- **GPU selection rule: >= ~500 GB/s on a >= 192-bit bus; 8 GB+ VRAM.**  The
  workload is **memory-bandwidth-bound** (batched normalized min-sum LDPC +
  batched max-log demap), so bandwidth decides and VRAM capacity is irrelevant
  (working set < 2 GB, see below).  Do **not** buy on VRAM size.

## Why VRAM is not the constraint

From the actual Tanner graphs (`wiki/analyses/gpu-and-algo-speedups.md`):

| PLP | codeword | graph+workspace / codeword | 117-block frame |
|---|---|---|---|
| PLP-1 256QAM 11/15, 64K | n=64800, 284759 edges | 4.55 MB | ~0.53 GB |
| PLP-0 64QAM 11/15, 64K | n=64800, 278999 edges | 4.60 MB | ~0.54 GB |
| PLP-16 QPSK 2/15, 16K | n=16200, 45359 edges | 0.90 MB | — |

Even with the subframe FFT/channel and double-buffering the set is < 2 GB, so
**8 GB is ample and 12 GB is generous.**  The frame budget is 247.1 ms and the
traffic is ~66.6 GB/frame at 100 iterations.

## What satisfies it

| Component | Requirement | Notes |
|---|---|---|
| CPU | 8 cores / 16 threads (Ryzen 7 7700X / 9700X; 6c 7600X ok) | pool build + pipeline; not the FEC lever |
| GPU | >= ~500 GB/s on >= 192-bit, 8 GB+ VRAM, current drivers | bandwidth-bound LDPC/demap |
| RAM | 32 GB DDR5, dual-channel | small working set; dual-channel feeds CPU |
| I/O | USB 3.0 (RSP1B), NVMe | capture + media write |
| PSU | 750-850 W | GPU + 8-core CPU |

GPU candidates (all current-driver):

| GPU | VRAM | Bus | Bandwidth | Worst case (100 it) | Verdict |
|---|---|---|---|---|---|
| **RTX 5070** | **12 GB** | 192-bit GDDR7 | **672 GB/s** | ~99 ms | **value pick** |
| RTX 4070 Ti Super | 16 GB | 256-bit | 672 GB/s | ~99 ms | equal bandwidth, dearer |
| RTX 5070 Ti | 16 GB | 256-bit GDDR7 | 896 GB/s | ~74 ms | premium new |
| RTX 4070 / 4070 Super | 12 GB | 192-bit | 504 GB/s | ~132 ms | fine |
| (avoid) RTX 5060 Ti | 16 GB | **128-bit** | 448 GB/s | ~149 ms | more VRAM, slower |
| (avoid) RTX 4060 | 8 GB | 128-bit | 272 GB/s | ~245 ms | too slow — at the budget edge |
| (avoid) RTX 4060 Ti | 16 GB | 128-bit | 288 GB/s | ~231 ms | more VRAM, slower |

*Worst case* above = 66.6 GB / bandwidth, i.e. **100% of peak bandwidth and a
zero CPU remainder** — an optimistic floor, not a prediction.  A real batched
min-sum kernel sustains ~70-80% of peak, and the per-frame CPU remainder
(~0.10 s measured 2026-10-17; see "Re-measured split" below) is *serial* with the
FEC unless it is pipelined, so the two terms add.  The >= ~500 GB/s rule is set
~1.85x above the raw 269.5 GB/s (66.6 GB / 247.1 ms) the traffic needs, and that
ratio is exactly what absorbs the sub-peak efficiency and the CPU remainder.

The 12 GB **RTX 5070** matches the 16 GB **4070 Ti Super** on bandwidth
(672 GB/s) for ~$430 less (GPU-only: RTX 5070 ~$972 vs 4070 Ti Super ~$1,400),
and the extra 4 GB of VRAM buys nothing for this workload.

## Candidate machines (2026-10-05)

> **Stale (flagged 2026-10-17):** the eBay/Newegg/Facebook links below are from
> 2026-10-05 and individual listings expire in days — treat the item numbers as
> gone and re-run the saved-search links at the bottom of the section instead.
> The *requirements* above are unaffected; only the specific offers are.

**Selected config, 8-core + RTX 5070 12 GB (~$1,400-1,600):**
- eBay: 7700X + RTX 5070 + 32 GB — `ebay.com/itm/236813627616`,
  `ebay.com/itm/236541182593`, `ebay.com/itm/398249613378` (Skytech Azure 3),
  `ebay.com/itm/407178366699` (7700), `ebay.com/itm/206566187535`.
- eBay: 9700X + RTX 5070 — `ebay.com/itm/206519450957` (32 GB + 2 TB),
  `ebay.com/itm/800600811477`.
- eBay: 7600X (6c) + RTX 5070 + 32 GB — `ebay.com/itm/336797523668`,
  `ebay.com/itm/318148693331`, `ebay.com/itm/147431801720`.
- New Newegg: Skytech Azure 3 Plus, 7700X + RTX 5070 12 GB + 16 GB DDR5 —
  **from $1,599.99** (`newegg.com/p/3D5-000Z-00334`); Hoengager 7600X + 5070 +
  32 GB (`newegg.com/p/3D5-003E-00P00`).
- Local pickup (DC area): Facebook Marketplace — 7700X + 5070 + 32 GB,
  **$1,600** (`facebook.com/marketplace/item/1356613013307032/`,
  `.../1566355718129345/`).

Live search links (stay valid as listings expire):
- `ebay.com/sch/i.html?_nkw=ryzen+7+7700x+rtx+5070`
- `ebay.com/sch/i.html?_nkw=ryzen+7+9700x+rtx+5070`
- `ebay.com/sch/i.html?_nkw=ryzen+5+7600x+rtx+5070`
- `ebay.com/sch/i.html?_nkw=gaming+pc+rtx+5070&_sop=12`

**Superseded (16-core + 4070 Ti Super, ~$1,800-5,500):** kept for reference —
Ryzen 9 7950X/9950X builds.  Not chosen: the 16 cores are not the live lever
with GPU FEC, and the premium Blackwell towers (9950X + 5070 Ti, ~$5,000) are
~3x the price for a bandwidth step this workload will not fully use.

**Evaluated and rejected (2026-10-17)** — cheap used gaming boxes offered as a
stopgap, both Turing:

| Listing | CPU | GPU | Verdict |
|---|---|---|---|
| `800070121616`, $600 | i7-10700, 8c/16t DDR4 | RTX 2080 Hybrid, **448 GB/s**, sm_75 | No — bandwidth equals the "(avoid) 5060 Ti" row |
| `117373825245`, $575 | i7-9700K, 8c/8t DDR4 | RTX 2080 Super, **496 GB/s**, sm_75 | No, but the better of the two — ~1% under the rule, 2 TB + 512 GB m.2 |

Neither is a fit: both are at or below the bandwidth rule, both sit exactly on
the CUDA 13 support floor (sm_75, no forward room — see the software notes), and
on both platforms a later GPU upgrade costs more than the selected box outright.
The CPU side is *not* the objection — with the non-FEC remainder at ~0.10 s, any
8-core CPU is ample; the GPU is the whole decision.  Buying either also
pre-empts the plan's own step 5: prototype `gpubindings` on a rented GPU and
differential-test it *before* buying, since the FEC traffic figure the whole
table above rests on (66.6 GB/frame) is still an estimate, not a measurement.

## Selected

**Ryzen 7 7700X (8c/16t) + RTX 5070 12 GB + 32 GB DDR5, ~$1,400-1,600 (used).**
8 cores/16 threads run the pool build and pipeline; the RTX 5070 does
demap+LDPC+BCH at 672 GB/s (2.5x the bandwidth floor, ~99 ms worst case) on
current Blackwell drivers (sm_120); USB 3.0 for the RSP1B.  The savings versus
the 16-core/4070 Ti Super config go to nothing it needs — the mandate was the
serial-floor fixes (now done — see the re-measured split below), not more cores.

## Prerequisite for true live: the serial-floor fixes (CPU side)

**All landed 2026-10-16/17.**  The original case, kept for the record: with
the GPU on FEC the per-frame CPU work was the blocker, not core count — pool
build ~0.9 s (serial 75x16K FFT + dense channel), `DataPlpChain` setup ~0.4 s
rebuilt every frame/PLP, acquisition ~14 s per live window unless the lock was
held, plus the network layer.  That floor alone exceeded the 247 ms budget, so
true live required (see the follow-on in `meta/TODO-ldpc-demod-enhancements.md`):

1. Cache `DataPlpChain`/tables/plans once per lock; hold the L1 lock across
   frames (no per-window re-acquisition).  **[done 2026-10-16]** — bounded
   per-config chain cache; `LiveReceiver` holds the lock across windows with a
   streaming resampler (real capture: 24 windows, 1 acquisition, 29 frames).
2. Batch the pool build (one 2-D FFT over the subframe body; thread the
   per-symbol channel estimation).  **[done 2026-10-16]** — 2-D FFT carrier
   cache (B3) + threaded per-symbol loop + cached pilot reference sequence;
   pool build 0.883 -> 0.223 s, **0.176 s re-measured 2026-10-17**.
3. Pipeline capture <-> FEC <-> network, so frame N+1 capture overlaps frame N
   decode.  **[done 2026-10-16]** — `LiveReceiver.run_pipelined` (bounded
   capture queue) with an optional bounded network worker
   (`network_async=True`) that runs the ALP/IP/LLS stage off the decode thread.
4. Speed up the 2.7 s BCH stage.  **[done 2026-10-16]** — vectorized the
   received-vector build; BCH 10.3 -> 5.12 ms/block, **5.06 ms/block re-measured
   2026-10-17**.
5. Cache the baseband scrambler (found later; not in the original four).
   **[done 2026-10-17]** — `signaling_fec.randomizer_bits` (A/322 5.2.3) was an
   uncached Python LFSR rebuilt for each of the 117 baseband packets; cached by
   length, `scramble_bits` x117 1.551 -> 0.0006 s.  This was the bulk of what
   had been misread as per-block FEC setup overhead.

These were mandatory regardless of hardware; the GPU only removes the FEC wall.

## Re-measured split (2026-10-17)

`python -m tools.bench_decode --workers 1,2,4 --repeats 5` on the dev host
(i5-4278U, 2c/4t), RF33 subframe-1 PLP-1 (256QAM 11/15, 64800, 117 FEC blocks):

| Stage | 2026-10-05 | 2026-10-17 |
|---|---|---|
| Pool build (75x16K FFT + dense CH) | 0.883 s | **0.176 s** |
| Demap / FEC block | 55.2 ms | **31.05 ms** |
| LDPC / FEC block | 57.1 ms | **51.12 ms** |
| BCH / FEC block | 10.3 ms | **5.06 ms** |
| FEC serial total | 14.34 s | **10.21 s** |
| Whole frame @1 / @2 / @4 workers | 16.00 / 9.29 / 7.35 s | **10.31 / 5.46 / 4.12 s** |

Two consequences for the GPU decision:

- **The non-FEC remainder is ~0.10 s/frame**, not the ~1 s that earlier drafts of
  this file and of `wiki/analyses/gpu-and-algo-speedups.md` quoted (that figure
  predated the scrambler cache).  The frame is now ~97% FEC arithmetic.
- Because the CPU side has been squeezed out, **GPU bandwidth is the single term
  that decides whether 247 ms is reachable.**  The serial-floor work made the GPU
  requirement *more* binding, not less — the >= ~500 GB/s rule above is now a
  pass/fail bar rather than margin-for-comfort, and it must be read together with
  the peak-efficiency caveat on the GPU table (its times assume 100% of peak and
  a zero CPU remainder; a real kernel sustains ~70-80%, and the two terms add).

**Correction:** the whole-frame figures `13.24/7.56/5.63 s` that appeared in the
2026-10-16 summary of `meta/TODO-ldpc-demod-enhancements.md` were a
pre-scrambler-cache snapshot; the table above supersedes them (and the LDPC and
demap deltas are host run-to-run variance, not improvements).

## Software notes for the GPU rung

- Build a raw CUDA extension (`gpubindings`) via `try: import`, alongside the
  existing C kernels, and differential-test it against `_ldpc`/`_demap`/NumPy.
- Target sm_120 (Blackwell / RTX 5070) — or sm_89 for Ada; both on current
  toolkits.  Prefer a raw CUDA extension over CuPy/PyTorch to avoid the
  Python-GPU-library version maze.
- **Confirmed 2026-10-17 (CUDA Toolkit 13.0 release notes):** CUDA 13.0
  "supports all NVIDIA architectures from Turing through Grace Blackwell" and
  dropped *offline compilation* below compute capability 7.5.  Turing (RTX
  20-series, sm_75) therefore still builds today, but it is the **floor** — no
  forward room, and it is the oldest target the kernels would ever carry.  This
  is why a used RTX 2080 / 2080 Super (both sm_75) is a stopgap, not the
  selected card.
- GPU accelerates demap + LDPC + BCH only.  The serial CPU floors above were
  fixed separately (2026-10-16/17); the GPU removes the FEC wall, which with them
  done is now essentially the whole frame.

## Prototype measured 2026-10-18 (rented RTX 2080 Ti)

The optional `gpubindings` package was built on a rented 2080 Ti (sm_75,
CUDA 12.8) and wired into `atsc3lib` behind `ATSC3LIB_GPU=auto|on|off`.  It is
a workspace member (`uv sync --package atsc3lib --extra gpu`, or `make
install-gpu`); a host without `nvcc` still installs it, but without the CUDA
kernels, so `available()` is False and the CPU path runs.  The CPU/C path
remains the required fallback; enabling the GPU leaves the decoded RF33 PLP-1
packets byte-identical (117/117).

What landed for the live path:

- `atsc3lib/accelerator.py` -- lazy optional seam; no import on a CPU-only host.
- Batched demap over all 117 FEC blocks (one call, per-block `sigma2`) and
  `decode_many` LDPC (one enqueue per codeword, one device sync).  Fully
  merged and grouped single-launch variants were measured, found not faster
  (one 64K codeword already fills the GPU), and removed; the saving is the
  per-call host sync/copy, not more parallelism.
- BCH zero-error fast path: packed GF(2) syndrome masks over the whole block
  matrix, falling back to the existing BM/Chien kernel on a non-zero syndrome.
  RF33 frames leave BCH with 0 errors, so this is the receive-path case:
  **~16 ms/frame instead of ~300 ms** for 117 blocks.
- Pool build: persistent worker pool, parallel FFT chunks, and **two** workers
  for per-symbol extraction.  Twelve workers are slower (92 ms vs 36 ms for the
  symbol loop) because that routine is short NumPy calls; the C FEC kernels
  still use the full CPU count.
- `LiveReceiver.run_pipelined(fec_async=True)` runs pool build for frame N+1 on
  a bounded worker while frame N's FEC runs on the GPU.  `atsc3-live
  --pipelined --fec-async` selects it.

Measured on the 2080 Ti node (`tools/bench_live`, RF33 subframe-1 PLP-1,
256QAM 11/15, 117 blocks, 16 frames):

| Stage | Time |
|---|---|
| pool build | 65-84 ms/frame |
| FEC decode (GPU) | 175 ms/frame |
| serial total | 260-268 ms/frame |
| overlapped | **193-198 ms/frame** |
| frame period | 247.1 ms |

Sustained live is reached on this prototype card, with ~50 ms margin.  The GPU
FEC split is demap ~32 ms, LDPC ~40-50 ms (16-iteration fast pass; two blocks
need the full ladder), BCH ~16 ms, and ~30 ms of host-side normalisation,
interleave and packet assembly.

Caveat: this node has no SDR, so the gate here is the RF33 fixture plus stage
timing, not an on-air run.  The RSP1B rig still has to prove the live loop.
