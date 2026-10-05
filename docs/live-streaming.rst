Live Streaming and CPU/Host Requirements
=========================================

This page explains the state of **live (real-time) reception**: what works
today, the frame budget it must meet, where the time actually goes, and what
host hardware is likely to keep up.

.. important::

   Live real-time throughput is **the one open receiver item**.  The bounded
   live loop is correct and produces valid output, but on the reference host
   it runs at roughly **9 seconds per frame against a 247 ms budget**.  Do not
   plan a live deployment expecting a working real-time receiver today; this
   page documents the measured baseline and the hardware rationale so a
   deployment can be sized and the remaining acceleration work prioritised.

What works today
----------------

The receive chain itself is complete and air-proven **offline**.  A saved
capture decodes to playable 1920x1080 HEVC video with AC-4 audio, and the
bounded live loop (:class:`atsc3lib.live.LiveReceiver`, ``atsc3-live``) runs
the same chain on finite windows from a live SDRplay or a file.  What is
missing is *speed*, not correctness or features.

The bounded loop never scans: each run tries a bounded number of acquisition
windows and a miss advances to the next (A/322 7.2.2.2).  That is the wire
behaviour — it is the right shape for live reception; it simply needs the hot
paths fast enough to fit the frame.

The frame budget
-----------------

One RF33 frame is **247.1 ms** — 1,708,032 main-rate samples at 6.144 MHz,
bootstrap to bootstrap (measured on air).  Live processing means decoding a
frame in less than that.  The acquisition search window is two A/322 7.2.2.2
minimum frame periods (a 52.7 ms minimum on RF33); the budget to meet is the
full-frame 247.1 ms.

Measured baseline
-----------------

Measured on the RF33 subframe-1 PLP-1 fixture (256QAM-NUC 11/15, 64800-bit
LDPC, 117 FEC blocks) on the reference host, an **Intel Core i5-4278U**
(2 cores / 4 threads):

.. list-table::
   :header-rows: 1
   :widths: 52 30 18

   * - Stage
     - Measured
     - Per 117-block frame
   * - Max-log demap (C kernel)
     - ~170 ms/block (256QAM, 8100 cells)
     - ~20 s
   * - LDPC min-sum, converged
     - 1.1 ms/block
     - ~0.13 s
   * - LDPC min-sum, 100 iterations, not converged
     - 621 ms/block
     - ~73 s per alpha try
   * - Pool build (75 x 16K FFT + dense channel)
     - ~1 s/subframe
     - ~1 s
   * - Whole frame (current code, 4-thread FEC pool)
     - —
     - ~9 s

Where the time actually goes
-----------------------------

* The **LDPC alpha ladder** dominates.  ``DataPlpChain.decode_cells`` re-runs
  the whole decode for each of ``ALPHA_LADDER = (1.0, 0.85, 0.75)`` until one
  converges, so a non-converged block can cost up to ``3 x 621 ms``.  A
  *converged* block is only 1.1 ms, so on a clean link the honest converged
  frame cost is closer to ``117 x 1.1 ms + demap`` (~300 ms), not 9 s.  Most
  of the observed 9 s is the alpha ladder plus Python orchestration.
* The **pool-build loop is sequential** — a Python ``for`` over 75 symbols,
  each a 16K FFT.  Batched as one 2-D FFT it is 74 ms on a single core.
* The C kernels already release the GIL and independent FEC blocks are
  threaded across cores, so scaling is available today.

Why a many-thread CPU is the first answer
-----------------------------------------

For a clean lighthouse link (74/74 PLP-0, 117/117 PLP-1 per frame), a
16-core host should bring the converged frame under the 247 ms budget on CPU
alone.  The earlier ~1000x gap is a *marginal-link* number (many-iteration
LDPC), not the clean-link number.

The recommended order of work is algorithmic first, threading second, GPU
last:

1. **Measure honestly first.**  Run all 117 blocks through the current
   converged path and the single-alpha path and establish the real per-frame
   wall time and its split.
2. **Algorithmic LDPC fixes** (no new dependency): a single-alpha fast path
   with a ladder fallback only when near convergence, plus early exit.
3. **Batch the 75-symbol FFT** into one block FFT.
4. **Thread / pipeline**: saturate FEC-block parallelism and pipeline frame
   N+1 capture against frame N decode.
5. **Only if 1-4 miss the budget**, prototype an optional GPU binding.
6. **Then buy**, or stay CPU-only if step 2 already won.

Choosing a host CPU
-------------------

Because the heavy work is FEC and demap, a GPU is **optional and last**; a
large core count is also not the first lever.  Once FEC and demap are off the
critical path, the CPU only runs acquisition, L1 and orchestration — all
light.  Sizing guidance from the analysis:

* **CPU-only (portable, cheaper).**  A mainstream 16-core desktop part (e.g. a
  Ryzen 9 9950X-class chip) with 64 GB DDR5 is the CPU-only target.  The
  measured converged frame is close to the budget at 16 cores; the wins in
  step 2-4 above should put it comfortably inside 247 ms.
* **GPU-assisted (CPU-fallback capable).**  A 16-core CPU plus a mid-range GPU
  with **16 GB VRAM and 500+ GB/s** memory bandwidth (e.g. an RTX 4070-class
  card) is the sweet spot for a *marginal* link.  The workload is bandwidth-
  and SM-bound, not VRAM-bound; anything above that is wasted here.  The GPU
  would be a ``try: import`` dispatch, never a required dependency, and
  differential-tested against the C kernels exactly as they are tested against
  NumPy.

The RSP1B is a USB device, so the simplest architecture is **one box**: move
the SDRplay to the host and let it do capture and decode.  A **split** setup —
capture on one machine, ship IQ or equalised cell pools over LAN to a
FEC/demap host — keeps ``sdrbindings`` where the radio is and the GPU
dependency out of the capture path.

Summary
-------

* Offline capture-to-picture works end to end today.
* Live reception is bounded and correct but **compute-bound**: ~9 s/frame vs
  a 247 ms budget on the reference 2-core host.
* The cheapest wins are algorithmic (LDPC alpha ladder, batched FFT), then
  threading; a 16-core CPU is the likely CPU-only answer for a clean link.
* A GPU is an optional accelerator for marginal links, not a requirement.

This page summarises ``wiki/analyses/gpu-and-algo-speedups.md`` and
``wiki/analyses/project-plan-status.md``.

See also
--------

* :doc:`getting-started`
* :doc:`sdrplay`
