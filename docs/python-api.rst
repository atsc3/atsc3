Python API
==========

This page documents selected public entry points of ``atsc3lib``, built
directly from the source docstrings.  It is not the full surface; the
package exports many lower-level building blocks (bootstrap, Preamble, LDPC,
NUC demapping, interleavers) that are useful when wiring a custom pipeline.

Decoding
--------

.. autofunction:: atsc3lib.decode_capture

.. autofunction:: atsc3lib.receiver.decode_signaling

.. autofunction:: atsc3lib.decode_plp_streams

.. autofunction:: atsc3lib.receiver.decode_plp_payload

Results
-------

.. autoclass:: atsc3lib.receiver.ReceiverResult
   :members:

.. autoclass:: atsc3lib.payload.PlpPayload
   :members:

.. autoclass:: atsc3lib.payload.DecodedStreams
   :members:

Capture
-------

.. autofunction:: atsc3lib.capture.capture

.. autoclass:: atsc3lib.capture.DetectedSdr
   :members:

Bounded live receive
--------------------

.. autoclass:: atsc3lib.live.LiveReceiver
   :members:

.. autoclass:: atsc3lib.live.LiveConfig
   :members:

.. autoclass:: atsc3lib.live.SdrplayIqSource
   :members:

.. autoclass:: atsc3lib.live.FileIqSource
   :members:

.. autoclass:: atsc3lib.live.LiveMediaSink
   :members:

.. autoclass:: atsc3lib.live.LiveStats
   :members:
