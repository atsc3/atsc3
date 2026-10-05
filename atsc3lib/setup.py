"""Build the atsc3lib compiled extensions.

The ATSC 3.0 kernels (LDPC + BCH, max-log demapper, frequency-interleaver
address generator) are NumPy consumers, so the NumPy include directory is
discovered at build time.  They are required dependencies of the library: this
build produces ``atsc3lib._bindings._ldpc``, ``._bch``, ``._demap`` and
``._fi`` in one distribution.

SDR capture (``sdrbindings``) and AC-4 decoding (``ac4bindings``) remain
separate distributions.
"""

from pathlib import Path

from setuptools import Extension, setup

import numpy


_BINDINGS = Path("atsc3lib") / "_bindings"


def _kernel(name):
    return Extension(
        "atsc3lib._bindings." + name,
        sources=[str(_BINDINGS / (name + ".c"))],
        include_dirs=[numpy.get_include()],
    )


setup(ext_modules=[_kernel("_ldpc"), _kernel("_bch"), _kernel("_demap"),
                   _kernel("_fi")])
