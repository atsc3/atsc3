"""Compiled ATSC 3.0 kernels used by :mod:`atsc3lib`.

The dense numerical loops of the receive chain live here as CPython C
extensions: the normalized-min-sum LDPC and BCH decoders (``fec``), the
max-log NUC/QAM demapper (``demod``) and the frequency-interleaver address
generator (``ofdm``, A/322 7.3).  They are required dependencies of the
library, not optional accelerators; the NumPy implementations in
``atsc3lib.ldpc_exact``, ``atsc3lib.bch``, ``atsc3lib.nuc`` and
``atsc3lib.frequency_interleaver`` are retained only as the references the C
kernels are differentially tested against.

The extension names are ``atsc3lib._bindings._ldpc`` etc.; each facade
submodule (``fec``, ``demod``, ``ofdm``) wraps one family behind the same
signature as its Python reference.
"""
