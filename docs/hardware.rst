Hardware and the Live Frame Budget
==================================

This page turns the live frame budget into a host specification: what the
receiver has to do per frame, which part of the host decides whether it keeps
up, and what was measured on real hardware.  The engineering record with the
full derivation is ``meta/HARDWARE-live-rig.md``; this page is the
documentation-facing summary.

The budget
----------

One RF33 frame is **247.1 ms** (bootstrap to bootstrap).  Live reception means
decoding a frame in less than that, sustained.  The receive chain is
bandwidth-bound on the FEC arithmetic: a 256QAM 11/15 PLP-1 frame carries 117
FEC blocks of 64,800 bits, and the demap + normalized-min-sum LDPC + BCH
traffic is roughly **66.6 GB/frame** at 100 iterations.  That is the number
the GPU has to move.

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
   * - Cell-pool build
     - 65-84 ms
     - 75 x 16K FFT + dense channel estimate + cell extraction
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

Why a GPU is bandwidth-bound, not VRAM-bound
--------------------------------------------

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

The serial CPU floors that used to dominate (acquisition, pool build,
``DataPlpChain`` setup, the baseband scrambler) were removed in 2026-10-16/17;
the remaining CPU work is the pool build and orchestration shown above.

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

Optional GPU install
--------------------

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

Measure a host before trusting it:

.. code-block:: bash

   .venv/bin/python -m tools.bench_live --gpu on --frames 32

The benchmark prints pool build, FEC, serial total and the overlapped
sustained frame time, and exits non-zero when it misses 247.1 ms.

Live reception is the pipelined path:

.. code-block:: bash

   atsc3-live --pipelined --fec-async --freq 587e6 --plp 1

``--fec-async`` runs the pool build for frame N+1 on a bounded worker while
frame N's FEC runs on the GPU.  Without it the serial total is still near the
budget; with it the measured sustained time is ~194 ms.

Validation status
-----------------

The GPU path is gated on the RF33 air fixture: 117/117 blocks converge and the
decoded packets are byte-identical to the C/CPU path.  The differential CUDA
tests live in ``gpubindings/tests`` and run with ``make test-gpu`` (skipped
when no GPU is present).

The remaining proof is on air.  The prototype node had no SDR, so the live
number above is the fixture plus stage timing, not an RSP1B run.  The live rig
still has to demonstrate sustained reception with the radio attached.

See also
--------

* :doc:`live-streaming` — the live loop and the frame budget in context.
* :doc:`getting-started` — install and first decode.
* :doc:`python-api` — the live and accelerator entry points.
