SDRplay Setup
=============

The supported and air-proven front end is the **SDRplay RSP1B**.  This page
covers installing the three pieces it needs — the SDRplay vendor API, the
SoapySDR module, and the ``sdrbindings`` extension — then verifying capture
and tuning gain.  See :doc:`getting-started` for the receiver install and
:doc:`live-streaming` for host requirements.

The capture path at a glance
----------------------------

.. code-block:: text

   SDRplay RSP1B (USB)
     -> SDRplay API service          (libsdrplay_api, /opt/sdrplay_api)
     -> SoapySDR + sdrplay module     (libsdrPlaySupport.so)
     -> sdrbindings (CPython ext)     (atsc3lib's capture path)
     -> atsc3-capture / atsc3-live

Each layer must be present before the next can see the radio.

1. SoapySDR core
----------------

On Debian/Ubuntu:

.. code-block:: bash

   sudo apt-get install -y libsoapysdr-dev soapysdr-tools pkg-config

``sdrbindings`` is built against the SoapySDR C library and finds it with
``pkg-config``.  Verify the core is visible:

.. code-block:: bash

   SoapySDRUtil --info

2. SDRplay vendor API
----------------------

SoapySDR's SDRplay module links the **SDRplay API**, a vendor binary
distributed by SDRplay (not packaged in Debian/Ubuntu).  Download the API
for your platform from `sdrplay.com <https://www.sdrplay.com/downloads/>`_,
extract, and run its installer.  It installs the shared library and the API
service:

.. code-block:: bash

   # from the extracted SDRplay API directory
   sudo ./install-scripts/install.sh
   sudo systemctl enable --now sdrplay

The API v3 installs ``libsdrplay_api.so.3`` (under ``/usr/local/lib``) and
``sdrplay_apiService``, typically started by ``sdrplay.service``.  Confirm it
is running:

.. code-block:: bash

   systemctl status sdrplay

3. SoapySDR SDRplay module
--------------------------

Install the SDRplay SoapySDR module matching the API major version (the RSP1B
uses the SDRplay3 module):

.. code-block:: bash

   sudo apt-get install -y soapysdr0.8-module-sdrplay3

On other distributions install the ``SoapySDRPlay3`` module from source
against the vendor API.  Confirm the module is loaded and the factory is
offered:

.. code-block:: bash

   SoapySDRUtil --info      # look for "Module found: ... libsdrPlaySupport.so"
   SoapySDRUtil --find="driver=sdrplay"

With the RSP1B connected, ``--find`` should print a device and its serial.

4. Build and install sdrbindings
--------------------------------

``sdrbindings`` is the CPython extension that drives SoapySDR from Python.
It lives beside the receiver in the workspace and is a **declared dependency
of atsc3lib**.  Install it once into the active environment:

.. code-block:: bash

   make -C sdrbindings install

(The top-level ``make`` in :doc:`getting-started` already builds it as part
of the workspace sync.)  Verify the module can see the radio:

.. code-block:: bash

   python -c "import sdrbindings; print(sdrbindings.probe('sdrplay'))"
   python -m sdrbindings --probe

Capture
-------

.. code-block:: bash

   atsc3-capture -f 587 -o out/capture.iq -d 10
   atsc3-capture -f 587 -g 45 --rf-gain 3 -o out/capture.iq

The device streams its native **CS16** (interleaved int16 IQ, 4 bytes per
sample); ``--cs8`` down-converts to int8 (2 bytes per sample) on the fly.
``atsc3-decode`` auto-detects which format was written, so either is fine.
The default sample rate is 10 MS/s and the baseband filter bandwidth is set
to 8 MHz for the 6 MHz channel.

Gain
----

The RSP1B exposes two gain elements, both of which ``atsc3-capture`` drives:

* ``-g`` / ``--gain`` — **IFGR**, the IF gain (default 40).
* ``--rf-gain`` — **RFGR**, the RF gain (default 4).

Higher gain raises the signal but risks overload and intermodulation in the
presence of strong adjacent channels.  On the RF33 lighthouse the settings
``-g 45 --rf-gain 3`` are the tuned capture that raises PLP-0 to 74/74.

Troubleshooting
---------------

**``SoapySDRUtil --find="driver=sdrplay"`` returns nothing.**
Check, in order: the device is enumerated by the OS (``lsusb`` should show
``1df7:3050 SDRplay RSP1B``); the SDRplay API service is running
(``systemctl status sdrplay``); and the SDRplay module was built against the
same API major version as the installed library.

**``capture()`` raises "No SDRplay found".**
``atsc3lib.capture`` reports this when neither ``sdrbindings`` nor
``SoapySDRUtil`` is present.  Build/install ``sdrbindings`` (step 4) or add
``soapysdr-tools`` to ``PATH``.

**``sdrbindings`` is not installed.**
Build it in ``sdrbindings/`` and install it into the active environment
(step 4).  It stays a separate distribution because it links the external
SoapySDR C library and is not ATSC-specific.

**Overload / clipping.**
The receiver decodes best with the strongest *clean* signal, not the loudest.
If you see constellation distortion or the decoder regresses as gain rises,
lower ``-g`` first, then ``--rf-gain``.

Why not other radios
--------------------

An ATSC 3.0 channel occupies 6 MHz, so the front end must deliver at least
6.144 Msps of raw IQ.  RTL-SDR (2.4 MHz) is too narrow.  The HackRF Pro cannot
meet the floor: its high-dynamic-range mode decimates a 40 MHz ADC by at
least 16x, giving 2.5 Msps.  Use an SDRplay (the RSP1B is air-proven) or
another device that delivers 6.144+ Msps natively.
