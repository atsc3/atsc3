Live Streaming and the Frame Budget
===================================

This page explains the state of **live (real-time) reception**: the frame
budget, where the time goes, how the accelerated path meets it, and what
remains before an on-air live run.

.. important::

   The accelerated receiver now meets the 247.1 ms frame period in the
   fixture/stage benchmark: **193-198 ms/frame sustained** on a prototype
   RTX 2080 Ti.  That is not yet an on-air proof — the prototype node had no
   SDR — but the compute wall is gone.  The remaining work is the RSP1B live
   run, tuning on the selected RTX 5070 host, and handling link margins.

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
*sustained*.  Latency can be higher than one frame; throughput cannot.

Measured split
--------------

RF33 subframe-1 PLP-1 (256QAM-NUC 11/15, 64,800-bit LDPC, 117 FEC blocks),
RTX 2080 Ti prototype, 2026-10-18:

.. list-table::
   :header-rows: 1
   :widths: 36 18 46

   * - Stage
     - Time
     - What it is
   * - Pool build
     - 65-84 ms
     - 75 x 16K FFT, dense scattered-pilot channel, cell extraction
   * - Demap
     - ~32 ms
     - max-log NUC demap, all 117 blocks in one call
   * - LDPC
     - ~40-50 ms
     - normalized min-sum, 16-iteration fast pass plus the alpha ladder
   * - BCH
     - ~16 ms
     - zero-error fast path; falls back to BM/Chien on a non-zero syndrome
   * - Host assembly
     - ~30 ms
     - normalization, de-interleave, Baseband Packet assembly
   * - **FEC total**
     - **~175 ms**
     -
   * - **Sustained, overlapped**
     - **193-198 ms**
     - pool build for frame N+1 overlaps frame N's FEC

The CPU-only fallback decodes correctly but is not live: roughly
0.6-0.8 s/frame on the same host.  The C kernels remain the required path;
the GPU is an optional accelerator.

How the pipeline overlaps
-------------------------

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

Backend selection
-----------------

.. code-block:: bash

   ATSC3LIB_GPU=auto   # default: GPU when importable and a device exists
   ATSC3LIB_GPU=on     # require the GPU
   ATSC3LIB_GPU=off    # force the C/CPU path

Install the optional kernels with ``make install-gpu`` (or
``uv sync --package atsc3lib --extra gpu``).  A host without ``nvcc`` installs
the Python wrappers only; ``available()`` is False and the CPU path runs.

Choosing a host
---------------

The workload is memory-bandwidth-bound.  The current target is an **8-core /
16-thread CPU plus a GPU with >= ~500 GB/s on a >= 192-bit bus and 8 GB+
VRAM** — an RTX 5070 12 GB is the value pick.  VRAM is not the constraint; the
working set is under 2 GB.  See :doc:`hardware` for the full requirements,
candidate table, and the measured prototype.

Validate a host with:

.. code-block:: bash

   .venv/bin/python -m tools.bench_live --gpu on --frames 32

The tool reports pool build, FEC, serial total and overlapped sustained time,
and exits non-zero if the frame period is missed.  ``tools/bench_decode
--gpu on`` reports the per-stage split for one frame.

What remains
------------

* **On-air live run** with the RSP1B on the selected host.  The prototype's
  number is the RF33 fixture plus stage timing, not a radio run.
* **Link-margin behaviour.**  The fixture is a clean 117/117 frame.  A
  marginal link spends more LDPC iterations; the alpha ladder and early exit
  limit the cost, but the sustained budget should be re-measured there.
* **RTX 5070 tuning.**  Rebuild with ``ATSC3_CUDA_ARCH=sm_120`` and re-run
  ``tools/bench_live``; more bandwidth should widen the margin.

Summary
-------

* Offline capture-to-picture works end to end.
* The accelerated live path meets the 247.1 ms period in the stage benchmark:
  **193-198 ms/frame sustained** on the prototype GPU.
* The CPU-only path remains a correct fallback but is not live.
* On-air validation on the RSP1B rig is the remaining proof.

See also
--------

* :doc:`hardware` — host specification, GPU table and measured prototype.
* :doc:`getting-started` — install and first decode.
* :doc:`python-api` — the live and accelerator entry points.
