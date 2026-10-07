Certificate authority, transmission, and reception
===================================================

This guide is the round trip for **your own trust anchor**: create a
certificate authority, issue a broadcaster certificate, build a signed ATSC
3.0 frame that carries it, and receive that frame and verify the signature —
all from the command line, with a Python equivalent at the end.

.. important::

   **This is an offline file round trip.  Nothing is transmitted over the
   air.**  ``atsc3-transmit`` writes a frame to an IQ file on disk; the
   receiver opens that same file.  There is no SDR, no antenna, and no RF
   carrier involved — the sender and receiver are the two ends of one file.
   The point is to exercise the *real* transmit and receive code paths
   (modulation, physical layer, LLS parsing, certificate verification)
   byte-for-byte, not to radiate a signal.  Driving a real radio (HackRF,
   USRP) from the generated IQ is left to the reader and is out of scope
   here.

The pieces are the two optional packages in the workspace:

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Component
     - Role
   * - ``openatsc3-pki``
     - The greenfield CA and signing toolkit (A/360 certificate profiles, CMS
       ``SignedData``, the LLS CertificationData ``0x06`` and SignedMultiTable
       ``0x07``).  It owns its root and its content-protection scheme and runs
       *alongside* A3SA as a parallel trust anchor.
   * - ``atsc3lib`` (``transmit``)
     - The synthetic transmitter — the inverse of the validated receive chain.
       It builds a real 8K/GI1536/SP4_2 QPSK 2/15 frame, carries the signed LLS
       tables in a data PLP, and **writes the frame as an IQ file** that
       ``atsc3-decode`` then **reads back from that file**.

.. note::

   This is a **synthetic, inspectable reference**, not a consumer-broadcast
   replacement.  No receivable stream carries our signature or DRM, so the
   round trip is gated by construction rather than proven on air; the receiver
   trusts **our root only** (fail-closed).  For the persistent, database-backed
   operator application see the ``openatsc3-ca`` Django app in the repository;
   the library CLI below is enough for the walkthrough.

.. contents:: On this page
   :local:

Prerequisites
-------------

The default workspace install already includes ``openatsc3-pki`` and the
``atsc3-transmit`` / ``atsc3-decode`` commands:

.. code-block:: bash

   cd atsc3
   make            # installs atsc3lib + openatsc3-pki and builds the kernels

Work in the ``out/`` scratch tree (it is gitignored) so the generated keys,
frames, and media stay with the project.

1. Create the certificate authority
-----------------------------------

A root trust anchor and an issuing CA:

.. code-block:: bash

   openatsc3-pki init-root --dir out/ca
   openatsc3-pki issue-ca  --dir out/ca

The first command writes a self-signed root; the second writes an issuing CA
signed by it:

.. code-block:: text

   root written: out/ca/root/root.cert.pem
     subject: CN=OpenATSC3 Root CA,OU=ATSC Trust Authority,O=OpenATSC3,C=US
   issuing CA written: out/ca/issuing/issuing.cert.pem

Private keys are written owner-only (``0600``) under ``out/ca/root/`` and
``out/ca/issuing/``.  ``out/ca/root/root.cert.pem`` is the file the receiver
will be told to trust.

2. Issue a broadcaster certificate
----------------------------------

A **broadcast signaling signer** for one broadcaster, carrying its broadcast
stream id (bsid) in the A/360 Subject Directory Attribute:

.. code-block:: bash

   openatsc3-pki issue-broadcaster --dir out/ca --name WHUT --bsid 540

.. code-block:: text

   broadcaster signer written: out/ca/broadcasters/WHUT
     subject: CN=WHUT-LLS-Signer,OU=ATSC Broadcast Signaling Signer,O=WHUT,C=US
     bsids:   (540,)
     EKU id-atsc-kp-signalingSigning: True

Inspect it at any time:

.. code-block:: bash

   openatsc3-pki show --dir out/ca --name WHUT

The signer key lives at ``out/ca/broadcasters/WHUT/signing.key.pem`` and the
certificate at ``out/ca/broadcasters/WHUT/signing.cert.pem``.

3. Write the transmission to a file
-----------------------------------

``atsc3-transmit`` builds one frame and **writes it to an IQ file** — it does
not key a radio.  With ``--signed`` it assembles the A/360 CertificationData
(``0x06``, gzipped XML with the chain and a stapled OCSP response per
certificate) and the LLS SignedMultiTable (``0x07``, a CMS ``SignedData`` over
the SLT and SystemTime), then modulates them into the data PLP:

.. code-block:: bash

   atsc3-transmit -o out/tx_whut.iq --fmt cs8 \
       --signed --ca-dir out/ca --signer WHUT --bsid 540 --plp 16

.. code-block:: text

   Wrote out/tx_whut.iq: 364032 samples (52.667 ms), structure 27, PLP 16, ...
     decode with: atsc3-decode out/tx_whut.iq --rate 6912000 --fmt cs8 --plp 16

The output file is main-rate (6.912 MHz) interleaved IQ on disk.  Without
``--signed`` the transmitter still writes a frame, but with only an unsigned
SLT — useful for a plain receiver loopback:

.. code-block:: bash

   atsc3-transmit -o out/tx_clear.iq --fmt cs8

The frame is bounded by the signalled frame length (one 52.7 ms frame here);
nothing scans or buffers without a limit.  **The file is the entire transport
— step 4 opens this exact file.**

4. Receive the file and verify
------------------------------

Decode the file exactly as an off-air capture, and hand the receiver the root
so it can verify the chain.  ``--require-signature`` makes the exit code
non-zero unless verification succeeds:

.. code-block:: bash

   atsc3-decode out/tx_whut.iq --rate 6912000 --fmt cs8 --plp 16 \
       --trust-root out/ca/root/root.cert.pem --require-signature

.. code-block:: text

   Capture: out/tx_whut.iq @ 6.912 MHz
     L1-Basic: version 0, CRC OK
     L1-Detail: version 0, BSID 540, CRC OK
     Payload: PLP 16, 16/16 FEC blocks converged
     Streams: 3 ALP packet(s), 3 UDP datagram(s), 3 LLS table(s)
       LLS table 0x06 (CertificationData): 2860 bytes
       LLS table 0x07 (SignedMultiTable): 574 bytes
       LLS table 0x01 (SLT): 261 bytes
     Security [openatsc3]: signed signaling verified (1 table(s))

The receive path is the real one — ``decode_signaling`` then
``decode_plp_streams`` then ``security.verify_streams``, all reading the IQ
file — so the signature is verified through the full physical-layer receive
chain, not injected at the network layer.

Negative gate: trust a different root and verification fails
-------------------------------------------------------------

Create a second, unrelated CA and try to verify the same frame against it.
The chain does not reach the trusted root, so verification fails (and
``--require-signature`` returns non-zero):

.. code-block:: bash

   openatsc3-pki init-root --dir out/other-ca
   atsc3-decode out/tx_whut.iq --rate 6912000 --fmt cs8 --plp 16 \
       --trust-root out/other-ca/root/root.cert.pem --require-signature

.. code-block:: text

   Security [openatsc3]: CertificationData NOT verified: ...

Tampering the SignedMultiTable (any byte inside its signed extent) likewise
fails, as does a missing or stale CertificationData.  The full negative set
(tamper, expiry, revocation, wrong root, missing EKU) is exercised by
``atsc3lib/tests/test_transmit_security.py`` and ``test_security.py``.

The same round trip from Python
-------------------------------

Build and write a signed frame:

.. code-block:: python

   from atsc3lib import transmit
   from openatsc3_pki import ca

   authority = ca.CertificateAuthority("out/ca")
   signer = authority.load_broadcaster("WHUT")
   signer_key = authority.load_broadcaster_key("WHUT")

   # ... build the CDT (0x06) / SignedMultiTable (0x07) exactly as the CLI does
   # (atsc3lib.cli._signed_tables), then wrap each as an LLS table payload:
   #   bytes([table_id, 0, 0, 1]) + body
   # and hand them to the transmitter:
   frame, plp = transmit.build_lls_frame(tables, bsid=540, plp_id=16)
   iq = frame.iq            # main-rate (6.912 MHz) complex samples

Receive and verify the same frame:

.. code-block:: python

   import numpy as np
   from atsc3lib import security
   from atsc3lib.receiver import decode_plp_streams

   result, streams = decode_plp_streams(iq.astype(np.complex64), 6.912e6,
                                        plp_id=16)
   report = security.verify_streams(streams, [authority.load_root()])
   assert report.ok, security.describe(report)

``security.verify_streams`` accepts a pluggable provider; ``openatsc3`` is the
default and the trust store is a list of root certificates (here the PEM
loaded from ``out/ca/root/root.cert.pem``).

Operating the CA beyond the walkthrough
---------------------------------------

The library CLI creates and reads a file-backed CA.  For persistent
revocation, rollover/renewal, export, and an admin UI, use the separate
``openatsc3-ca`` application (Django + PostgreSQL; ``make install-all`` and
``make test-ca``).  Both share the same on-disk PEM layout and the
``openatsc3-pki`` crypto.

See also
--------

* :doc:`getting-started` — the receiver quick start.
* :doc:`python-api` — the public Python entry points.
* The repository ``wiki/analyses/transmitter.md`` and
  ``wiki/analyses/own-ca-and-content-protection.md`` for the design record.
