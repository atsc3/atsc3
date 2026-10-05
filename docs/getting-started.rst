Getting Started
===============

This page takes you from a clean machine to decoding a real ATSC 3.0
broadcast.  It covers installing the receiver stack, capturing IQ from an
SDRplay, and running the command-line tools and the Python API.

.. contents:: On this page
   :local:

What you need
-------------

* A **Linux host** (the tools are developed and tested on Linux).
* An **SDR wide enough for the 6 MHz channel** — the SDRplay RSP1B is the
  supported and air-proven front end.  The library itself is
  hardware-agnostic and consumes raw IQ, but the bundled capture path drives
  SDRplay over SoapySDR.  RTL-SDR (2.4 MHz) is too narrow and is **not
  supported**.
* A TV antenna aimed at an ATSC 3.0 transmitter.  In the Washington DC DMA
  the reference signal is **RF33 / 587 MHz** (WHUT mux, BSID 540).
* Python 3.10-3.13 and a C toolchain.

See :doc:`sdrplay` for front-end setup and :doc:`live-streaming` before
planning a *live* (real-time) deployment.

System prerequisites
--------------------

The compiled bindings need a C compiler and headers.  On Debian/Ubuntu:

.. code-block:: bash

   sudo apt-get update
   sudo apt-get install -y build-essential pkg-config libsoapysdr-dev

The ``sdrbindings`` extension links the **SoapySDR** C library
(``libsoapysdr-dev`` provides the headers and ``SoapySDR.pc``).  The SDRplay
itself additionally needs the vendor API and its SoapySDR module — see
:doc:`sdrplay`.

Install the workspace
---------------------

The workspace is managed with `uv <https://docs.astral.sh/uv/>`_.  One
command creates a single ``.venv`` with the receiver stack installed
editable:

.. code-block:: bash

   git clone <repository-url> atsc3
   cd atsc3
   make

``make`` syncs ``atsc3lib`` plus its declared dependencies ``sdrbindings``
and ``ac4bindings`` and the optional ``openatsc3-pki`` library, and compiles
the C kernels (LDPC + BCH, max-log demapper, frequency interleaver) as
``atsc3lib._bindings``.  The compiled kernels are **required**, not optional
accelerators.

The default install deliberately leaves out the ``openatsc3-ca`` Django app;
use ``make install-all`` if you want it.  The certificate/PKI track is out of
scope for this guide.

Verify the install
------------------

.. code-block:: bash

   make check       # byte-compile every Python source
   make test        # the atsc3lib suite (includes the compiled kernels)

Capture IQ
----------

With an SDRplay connected, record a few seconds of the channel as raw IQ.
The device's native stream is CS16 (interleaved int16 IQ):

.. code-block:: bash

   atsc3-capture -f 587 -o out/capture.iq -d 10
   atsc3-capture -f 587 -g 45 --rf-gain 3 -o out/capture.iq   # tuned on RF33

``-f`` is the centre frequency in MHz, ``-d`` the duration in seconds,
``-g`` the IF gain (IFGR) and ``--rf-gain`` the RF gain (RFGR).  ``atsc3-decode``
auto-detects the on-disk sample format.  See :doc:`sdrplay` for the gain
elements and troubleshooting.

Decode L1 signalling and PLP configuration
------------------------------------------

.. code-block:: bash

   atsc3-decode out/capture.iq --rate 10e6 --fmt cs16

This runs the validated chain *bootstrap -> Preamble -> L1-Basic ->
L1-Detail -> per-PLP configuration* and prints the multiplex's PLP table.
Add ``--plp`` to also decode one PLP's payload through to ALP/IP/UDP/LLS:

.. code-block:: bash

   atsc3-decode out/capture.iq --rate 10e6 --fmt cs16 --plp 0
   atsc3-decode out/capture.iq --rate 10e6 --fmt cs16 --plp 1 --subframe 1

On RF33, PLP-0 (64QAM-NUC 11/15) converges 74/74 and yields the A/331 SLT;
PLP-16 (QPSK 2/15) decodes byte-identical to the reference; PLP-1
(256QAM-NUC 11/15) converges 117/117.

Capture to playable media
--------------------------

.. code-block:: bash

   atsc3-media out/capture.iq --rate 10e6 --fmt cs16

``atsc3-media`` drains every layer-0 PLP for a bounded number of frames,
reassembles ROUTE/MMTP, and writes one fragmented MP4 per track (video by
default; ``--all`` writes audio/subtitles too), the decoded AC-4 WAVs, and a
combined A/V MP4.  ``--frames 4`` is the default (the shortest drain that
yields a playable segment on the saved RF33 capture); ``--frames 0`` consumes
the whole capture.

The bounded live loop
---------------------

.. code-block:: bash

   atsc3-live --file out/capture.iq --plp 16 --units 12 --max-frames 1
   atsc3-live --freq 587e6 --plp 0                 # live SDRplay

``atsc3-live`` runs the same receive chain on finite windows from a live
SDRplay or a saved capture.  It never scans unbounded: each run tries a
bounded number of acquisition windows and a miss advances to the next
(A/322 7.2.2.2).  **It does not yet keep up with the air rate** — see
:doc:`live-streaming`.

Python API
----------

The canonical entry point decodes signalling from a saved capture:

.. code-block:: python

   from atsc3lib import decode_capture, decode_plp_streams

   result = decode_capture('out/capture.iq', fs_main=10e6, fmt='cs16')
   if result.l1_detail_ok:
       for subframe, plp in result.plps:
           print(subframe, plp.plp_id, plp.modulation, plp.code_rate)

To carry one PLP through to the link layer, read the IQ and pass the same
``result`` back in:

.. code-block:: python

   from atsc3lib.receiver import _read_iq

   iq = _read_iq('out/capture.iq', 'cs16')
   result, streams = decode_plp_streams(
       iq, fs_main=10e6, plp_id=16, result=result)
   for table in streams.lls:
       print(table.name, len(table.data))
   for datagram in streams.datagrams:
       ...

``decode_capture`` accepts ``fmt='auto'`` (the default), which distinguishes
CS8 from CS16 by byte variance and needs no explicit format.  ``--rate`` is
the capture sample rate, not the ATSC main rate; the front end resamples to
the 6.144 MHz bootstrap rate and 6.912 MHz main rate internally.

See :doc:`python-api` for the selected public surface.

Next steps
----------

* :doc:`sdrplay` — front-end setup, gain, and troubleshooting.
* :doc:`live-streaming` — current live-reception status and host requirements.
* :doc:`python-api` — the Python entry points.
