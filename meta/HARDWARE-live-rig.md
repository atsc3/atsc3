# Hardware target — single-box live receiver (CPU + GPU)

Decision recorded 2026-10-05.  Goal: sub-second (ideally true-live < 247 ms)
decode of the RF33 256QAM PLP on one machine, with the SDRplay RSP1B and the
`atsc3lib` receiver.  See `meta/TODO-ldpc-demod-enhancements.md` (follow-on) and
`wiki/analyses/gpu-and-algo-speedups.md`.

## Decision

- **One box, not split.**  GPU and CPU together; the RSP1B (USB) moves to it.
- **CPU: 16 physical cores** (FEC-block thread pool).
- **GPU: a modern NVIDIA card on current drivers** — Ada (RTX 40) or Blackwell
  (RTX 50).  No Pascal: CUDA 13 dropped offline compilation below CC 7.5, so a
  GTX 1080 Ti would force the CUDA 12.x line.  A current card avoids that.
- The workload is **memory-bandwidth-bound** (batched normalized min-sum LDPC +
  batched max-log demap), so GPU *bandwidth* matters far more than tensor cores.
  Required floor ~500 GB/s and >= 8 GB VRAM.

## What satisfies it

| Component | Requirement | Notes |
|---|---|---|
| CPU | 16 cores / 32 threads (Ryzen 9 9950X / 7950X, or dual 8c Xeon) | FEC pool `_parallel_fec_blocks` |
| GPU | >= 500 GB/s, >= 8 GB VRAM, current drivers | bandwidth-bound LDPC/demap |
| RAM | 32 GB+ DDR5, dual-channel | small FEC working set; dual-channel feeds CPU |
| I/O | USB 3.0 (RSP1B), NVMe | capture + media write |
| PSU | 850 W | GPU + 16-core CPU |

GPU candidates (both current-driver, 16 GB):

| GPU | Arch / CC | Bandwidth | Verdict |
|---|---|---|---|
| RTX 4070 Ti Super | Ada, sm_89 | 672 GB/s | best value used |
| RTX 5070 Ti | Blackwell, sm_120 | 896 GB/s | premium new |
| (avoid) RTX 5060 Ti / 5060 | Blackwell | 448 / 288 GB/s | 128-bit bus, too slow |

## Candidate machines (2026-10-05)

**Best value, single box, used (~$1,800):**
- Ryzen 9 7950X (16c) + RTX 4070 Ti Super 16 GB + 32 GB DDR5.  eBay listings
  e.g. `ebay.com/itm/206598238800` (7950X3D + 4070 Ti Super); Magic Micro
  `ebay.com/itm/395745980876`, `397878543342` (7950X + RTX 4070; add RAM).
- Local (Rockville/DC area): Facebook Marketplace, Columbia MD — 7950X +
  RTX 4070 Ti Super + 32 GB, **$1,825** (local pickup; inspect in person).

**Premium new, Newegg (16c + Blackwell):**
- ASUS ROG GR701: Ryzen 9 9950X + RTX 5070 Ti 16 GB + 64 GB DDR5 + 2 TB —
  **$5,009** (Arsenal PC).
- ASUS TUF GT501: 9950X + RTX 5070 Ti + 128 GB DDR5 + 1 TB — **$5,539**.

**Cheapest (Pascal, needs CUDA 12.x pinning — not chosen):**
- Dell Precision 7820/7920 dual-Xeon (16c) ~$500-900 + used GTX 1080 Ti ~$150.

## Selected

**Ryzen 9 7950X (16c) + RTX 4070 Ti Super 16 GB, 32 GB DDR5, ~$1,800 (used).**
16 cores for the FEC pool; 672 GB/s (2.7x the bandwidth floor) for
demap+LDPC+BCH; Ada sm_89 on current drivers; USB 3.0 for the RSP1B.  New
Blackwell (9950X + 5070 Ti, ~$5,000) is the warranty option at ~2.7x the price
for a modest bandwidth step that this workload will not fully use.

## Software notes for the GPU rung

- Build a raw CUDA extension (`gpubindings`) via `try: import`, alongside the
  existing C kernels, and differential-test it against `_ldpc`/`_demap`/NumPy.
- Target sm_89 (Ada) — or sm_120 for Blackwell; both on current toolkits.
- GPU accelerates demap + LDPC + BCH only.  The serial CPU floors (pool build,
  per-frame chain rebuild, acquisition, network layer) must still be fixed for
  true live — see the TODO follow-on.
