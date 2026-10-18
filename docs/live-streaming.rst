Live Reception and the Frame Budget
===================================

This page covers live (real-time) reception end to end: the frame budget,
where the time goes, the CPU/GPU host requirements, the pipeline that meets the
budget, and how to validate a host.  The engineering record with the full
derivation is ``meta/HARDWARE-live-rig.md``; this page is the
documentation-facing summary.

.. important::

   The accelerated receiver now meets the 247.1 ms frame period in the
   fixture/stage benchmark: **193-198 ms/frame sustained** on a prototype
   RTX 2080 Ti.  That is not yet an on-air proof — the prototype node had no
   SDR — but the compute wall is gone.  The remaining work is the RSP1B live
   run, tuning on the selected RTX 5070 host, and handling link margins.

.. contents:: On this page
   :local:
   :depth: 2

What works today
----------------

The receive chain is complete and air-proven **offline**.  A saved capture
decodes to playable 1920x1080 HEVC video with AC-4 audio.  The bounded live
loop (:class:`atsc3lib.live.LiveReceiver`, ``atsc3-live``) runs the same chain
on finite windows from a live SDRplay or a file, holds the L1 lock across
windows, and never scans unbounded (A/322 7.2.2.2).

The frame budget
----------------

One RF33 frame is **247.1 ms** — 1,708,032 main-rate samples at 6.144 MHz,
bootstrap to bootstrap.  Live reception means one frame per period,
*sustained*; latency can be higher than one frame, throughput cannot.

A 256QAM 11/15 PLP-1 frame carries 117 FEC blocks of 64,800 bits, and the
demap + normalized-min-sum LDPC + BCH traffic is roughly **66.6 GB/frame** at
100 iterations.  That is the number the GPU has to move.

The CPU side is not the FEC lever.  Once the GPU (or the threaded C kernels)
owns FEC, the CPU runs acquisition, L1, the cell-pool build and the network
stage.  High single-thread performance matters more than a large core count.

Measured split
--------------

RF33 subframe-1 PLP-1, 256QAM-NUC 11/15, 64,800-bit LDPC, 117 blocks, on a
rented **RTX 2080 Ti** host (Ryzen 9 5900X, 12-core quota), 2026-10-18:

.. list-table::
   :header-rows: 1
   :widths: 34 24 42

   * - Stage
     - Time per frame
     - Notes
   * - Pool build
     - 65-84 ms
     - 2-worker threaded; 75 x 16K FFT + dense channel + cell extraction
   * - Demap
     - ~32 ms
     - batched over all 117 blocks, per-block ``sigma2``
   * - LDPC
     - ~40-50 ms
     - 16-iteration fast pass; two blocks take the full alpha ladder
   * - BCH
     - ~16 ms
     - zero-error fast path (RF33 leaves BCH 0 errors)
   * - Host assembly
     - ~30 ms
     - normalization, de-interleave, packet assembly
   * - **FEC total**
     - **~175 ms**
     -
   * - **Sustained, overlapped**
     - **193-198 ms**
     - pool build for frame N+1 overlaps frame N's FEC

That is inside the 247.1 ms period with ~50 ms of margin.  The CPU-only
fallback is correct but not live: roughly 0.6-0.8 s/frame on the same host.

The live pipeline
-----------------

``LiveReceiver.run_pipelined`` already overlaps capture with decode.  Two
additional stages close the remaining gap:

* ``fec_async=True`` moves pool build + FEC to a bounded worker.  The reader
  thread keeps extending the stream and building the next frame's pool while
  the current frame's FEC runs on the GPU.
* ``network_async=True`` runs the Baseband Packet -> ALP/IP/LLS stage on its
  own bounded worker, overlapping frame N's network work with frame N+1.

All queues are bounded, so a slow consumer back-pressures the source instead
of buffering without limit.

.. code-block:: bash

   atsc3-live --pipelined --fec-async --freq 587e6 --plp 1
   atsc3-live --pipelined --fec-async --network-async --file out/capture.iq --plp 16

Why the GPU is bandwidth-bound, not VRAM-bound
----------------------------------------------

From the actual Tanner graphs:

.. list-table::
   :header-rows: 1
   :widths: 30 22 24 24

   * - PLP
     - Codeword
     - Graph + workspace
     - 117-block frame
   * - PLP-1 256QAM 11/15, 64K
     - n=64800, 284759 edges
     - 4.55 MB
     - ~0.53 GB
   * - PLP-0 64QAM 11/15, 64K
     - n=64800, 278999 edges
     - 4.60 MB
     - ~0.54 GB
   * - PLP-16 QPSK 2/15, 16K
     - n=16200, 45359 edges
     - 0.90 MB
     - —

Even with the subframe FFT, channel state and double buffering the working set
stays under 2 GB.  **8 GB of VRAM is ample; 12 GB is generous.**  Buy on
memory bandwidth and bus width, not capacity.

CPU
---

The selected CPU is an **8-core / 16-thread** part with strong single-thread
performance (Ryzen 7 7700X / 9700X class; a 6-core 7600X is the acceptable
floor).  Sixteen cores are not the live lever once FEC is on the GPU.

Recommended system:

* 8 cores / 16 threads, high IPC
* 32 GB DDR5, dual channel
* NVMe for captures and media
* USB 3.0 for the RSP1B
* 750-850 W PSU with a modern GPU

The pool build is **done and no longer a serial floor**.  The per-symbol
extraction runs on a persistent worker pool (two workers; more workers are
slower because each symbol is a short NumPy call), the FFT carrier cache is one
batched 2-D transform over the subframe body, and the scattered-pilot reference
sequence is cached by length.  Measured 65-84 ms/frame on the prototype host
(Ryzen 9 5900X, 12-core quota); the same stage fell from 0.883 s to 0.176 s on
the 2-core dev host.  With ``--fec-async`` it overlaps frame N's FEC and is off
the critical path.

The other per-frame CPU floors are also closed: ``DataPlpChain``/tables/plans
are cached once per L1 lock, the lock is held across frames, and the A/322
5.2.3 baseband scrambler is cached by length.  No further CPU-side optimization
is required to meet 247.1 ms.  The dense per-symbol channel loop is still a
threaded NumPy loop rather than fully vectorized; that is optional headroom,
not required work.  The CPU-only path remains FEC-bound and is not live.

GPU
---

Selection rule: **>= ~500 GB/s on a >= 192-bit bus, 8 GB+ VRAM, current
drivers.**  The rule sits ~1.85x above the raw 269.5 GB/s the traffic needs
(66.6 GB / 247.1 ms) so a real kernel at 70-80% of peak plus the CPU
remainder still fits.

.. list-table::
   :header-rows: 1
   :widths: 24 12 14 18 22 10

   * - GPU
     - VRAM
     - Bus
     - Bandwidth
     - Worst case (100 it)
     - Verdict
   * - **RTX 5070**
     - **12 GB**
     - 192-bit GDDR7
     - **672 GB/s**
     - ~99 ms
     - **value pick**
   * - RTX 4070 Ti Super
     - 16 GB
     - 256-bit
     - 672 GB/s
     - ~99 ms
     - equal bandwidth, dearer
   * - RTX 5070 Ti
     - 16 GB
     - 256-bit GDDR7
     - 896 GB/s
     - ~74 ms
     - premium new
   * - RTX 4070 / 4070 Super
     - 12 GB
     - 192-bit
     - 504 GB/s
     - ~132 ms
     - fine
   * - (avoid) RTX 5060 Ti
     - 16 GB
     - **128-bit**
     - 448 GB/s
     - ~149 ms
     - more VRAM, slower
   * - (avoid) RTX 4060
     - 8 GB
     - 128-bit
     - 272 GB/s
     - ~245 ms
     - at the budget edge
   * - (avoid) RTX 4060 Ti
     - 16 GB
     - 128-bit
     - 288 GB/s
     - ~231 ms
     - more VRAM, slower

Worst case is 66.6 GB / bandwidth — 100% of peak and a zero CPU remainder, so
it is a floor, not a prediction.

**Selected configuration:** Ryzen 7 7700X (8c/16t) + RTX 5070 12 GB + 32 GB
DDR5, USB 3.0 for the RSP1B.  The 12 GB 5070 matches the 16 GB 4070 Ti Super
on bandwidth for less money, and the extra VRAM buys nothing here.

**Turing is a stopgap.**  CUDA 13 dropped offline compilation below compute
capability 7.5, so an RTX 2080 / 2080 Super (sm_75) still builds but has no
forward room.  The 2026-10-18 prototype used a 2080 Ti (616 GB/s, below the
selection rule) and still met the budget; the 5070 is the card to buy.

Optional GPU install and backend selection
------------------------------------------

``gpubindings`` is a monorepo workspace member but remains an **optional
install**: the required path is the C kernels in ``atsc3lib._bindings``.  On a
host without ``nvcc`` the package installs without the CUDA kernels, and
``available()`` is False so the CPU path runs.

.. code-block:: bash

   # GPU host (fails loudly if nvcc is missing)
   make install-gpu

   # equivalent
   uv sync --package atsc3lib --extra gpu

   # CPU-only host: the normal install is unchanged
   make

Backend selection is runtime, not build-time:

.. code-block:: bash

   ATSC3LIB_GPU=auto   # default: use the GPU when importable and a device exists
   ATSC3LIB_GPU=on     # require it; raise when unavailable
   ATSC3LIB_GPU=off    # never import gpubindings

Validating a host
-----------------

.. code-block:: bash

   .venv/bin/python -m tools.bench_live --gpu on --frames 32
   .venv/bin/python -m tools.bench_decode --gpu on

The live benchmark prints pool build, FEC, serial total and the overlapped
sustained frame time, and exits non-zero when it misses 247.1 ms.  The decode
benchmark reports the per-stage split for one frame.

Validation status and what remains
----------------------------------

The GPU path is gated on the RF33 air fixture: 117/117 blocks converge and the
decoded packets are byte-identical to the C/CPU path.  The differential CUDA
tests live in ``gpubindings/tests`` and run with ``make test-gpu`` (skipped
when no GPU is present).

The remaining proof is on air:

* **On-air live run** with the RSP1B on the selected host.  The prototype node
  had no SDR, so the number above is the RF33 fixture plus stage timing, not a
  radio run.
* **Link-margin behaviour.**  The fixture is a clean 117/117 frame.  A
  marginal link spends more LDPC iterations; the alpha ladder and early exit
  limit the cost, but the sustained budget should be re-measured there.
* **RTX 5070 tuning.**  Rebuild with ``ATSC3_CUDA_ARCH=sm_120`` and re-run
  ``tools.bench_live``; more bandwidth should widen the margin.

Summary
-------

* Offline capture-to-picture works end to end.
* The accelerated live path meets the 247.1 ms period in the stage benchmark:
  **193-198 ms/frame sustained** on the prototype GPU.
* The CPU-only path remains a correct fallback but is not live.
* On-air validation on the RSP1B rig is the remaining proof.

See also
--------

* :doc:`getting-started` — install and first decode.
* :doc:`sdrplay` — the RSP1B front end.
* :doc:`python-api` — the live and accelerator entry points.
