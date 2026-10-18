"""Optional CUDA accelerators for the ATSC 3.0 receiver.

A workspace member of the monorepo, but never a required dependency: a normal
install on a host without the CUDA toolkit ships the Python wrappers only
(``available()`` is False), and the receiver's compiled C kernels
(``atsc3lib._bindings``) are the required path.  Every call site reaches this
package through ``try: import``, so a machine with no GPU still runs -- it just
runs slower.  See ``meta/HARDWARE-live-rig.md``.

Each submodule exposes ``available()`` so a caller can tell "not installed" or
"no CUDA device" from "installed and ready".
"""

from . import demap, ldpc

__all__ = ["demap", "ldpc"]
__version__ = "0.1.0"
