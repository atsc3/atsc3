# Hardware target — single-box live receiver (CPU + GPU)

Decision recorded 2026-10-05 (revised same day: 8 cores + RTX 5070).  Goal:
sub-second (ideally true-live < 247 ms) decode of the RF33 256QAM PLP on one
machine, with the SDRplay RSP1B and the `atsc3lib` receiver.  See
`meta/TODO-ldpc-demod-enhancements.md` (follow-on) and
`wiki/analyses/gpu-and-algo-speedups.md`.

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
| (avoid) RTX 4060 | 8 GB | 128-bit | 288 GB/s | ~231 ms | too slow |

The 12 GB **RTX 5070** matches the 16 GB **4070 Ti Super** on bandwidth
(672 GB/s) for ~$430 less (GPU-only: RTX 5070 ~$972 vs 4070 Ti Super ~$1,400),
and the extra 4 GB of VRAM buys nothing for this workload.

## Candidate machines (2026-10-05)

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

## Selected

**Ryzen 7 7700X (8c/16t) + RTX 5070 12 GB + 32 GB DDR5, ~$1,400-1,600 (used).**
8 cores/16 threads run the pool build and pipeline; the RTX 5070 does
demap+LDPC+BCH at 672 GB/s (2.7x the bandwidth floor, ~99 ms worst case) on
current Blackwell drivers (sm_120); USB 3.0 for the RSP1B.  The savings versus
the 16-core/4070 Ti Super config go to nothing it needs — the mandate is the
serial-floor fixes below, not more cores.

## Prerequisite for true live: the serial-floor fixes (CPU side)

With the GPU on FEC, the remaining per-frame CPU work is the blocker, not core
count: pool build ~0.9 s (serial 75x16K FFT + dense channel), `DataPlpChain`
setup ~0.4 s rebuilt every frame/PLP, acquisition ~14 s per live window unless
the lock is held, plus the network layer.  That floor alone exceeds the 247 ms
budget, so true live requires (see `meta/TODO-ldpc-demod-enhancements.md`
follow-on):

1. Cache `DataPlpChain`/tables/plans once per lock; hold the L1 lock across
   frames (no per-window re-acquisition).
2. Batch the pool build (one 2-D FFT over the subframe body; thread the
   per-symbol channel estimation).
3. Pipeline capture <-> FEC <-> network, so frame N+1 capture overlaps frame N
   decode.
4. Speed up the 2.7 s BCH stage.

These are mandatory regardless of hardware; the GPU only removes the FEC wall.

## Software notes for the GPU rung

- Build a raw CUDA extension (`gpubindings`) via `try: import`, alongside the
  existing C kernels, and differential-test it against `_ldpc`/`_demap`/NumPy.
- Target sm_120 (Blackwell / RTX 5070) — or sm_89 for Ada; both on current
  toolkits.  Prefer a raw CUDA extension over CuPy/PyTorch to avoid the
  Python-GPU-library version maze.
- GPU accelerates demap + LDPC + BCH only.  The serial CPU floors above must
  still be fixed for true live.
