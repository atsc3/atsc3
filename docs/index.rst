OpenATSC3 Receiver Documentation
================================

**An open ATSC 3.0 software receiver.**

OpenATSC3 demodulates real off-air ATSC 3.0 broadcasts, decodes all L1
signalling and data PLP payloads, reassembles the A/331 service layer
(ROUTE / MMTP / media), and builds playable audio/video.  It is validated
against off-air captures (RF33 / 587 MHz, BSID 540, SDRplay RSP1B) and
cross-checked against an independent reference receiver.

The workspace is a small monorepo:

.. list-table::
   :header-rows: 1
   :widths: 22 18 60

   * - Package
     - Kind
     - Purpose
   * - ``atsc3lib``
     - Python + C extensions
     - Physical-layer demodulation, L1 signalling, data-PLP decoding, and
       A/331 service discovery / ROUTE / MMTP / media.  The dense kernels
       (normalized-min-sum LDPC and BCH, max-log demapper, frequency
       interleaver) are built in as ``atsc3lib._bindings``.
   * - ``sdrbindings``
     - C extension
     - SDRplay capture over SoapySDR.
   * - ``ac4bindings``
     - C extension
     - AC-4 (ETSI TS 103 190) audio decoder kernels.

Start here
----------

* :doc:`getting-started` — install the stack, capture IQ from an SDRplay,
  and decode a real broadcast from the command line and from Python.
* :doc:`sdrplay` — set up the SDRplay RSP1B front end (SDRplay API, SoapySDR,
  ``sdrbindings``), tune gain, and troubleshoot capture.
* :doc:`live-streaming` — the current state of *live* (real-time) reception,
  the frame budget, and what CPU/host is needed to keep up.

Reference
---------

* :doc:`python-api` — selected ``atsc3lib`` public entry points (built from
  the source docstrings).

.. note::

   The receiver works **offline** end to end today: a saved capture decodes
   to playable 1920x1080 HEVC video with AC-4 audio.  **Live real-time
   throughput is the one open receiver item** — see :doc:`live-streaming`
   before planning a live deployment.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting-started
   sdrplay
   live-streaming
   python-api

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
