"""Optional CUDA accelerators for the ATSC 3.0 receiver.

Never a required dependency.  The receiver's compiled C kernels
(``atsc3lib._bindings``) are the required path and every call site reaches this
package through ``try: import``, so a machine with no GPU still runs -- it just
runs slower.  See ``meta/HARDWARE-live-rig.md``.

Each submodule exposes ``available()`` so a caller can tell "not installed" or
"no CUDA device" from "installed and ready".
"""

from . import demap, ldpc

__all__ = ["demap", "ldpc"]
__version__ = "0.1.0"
