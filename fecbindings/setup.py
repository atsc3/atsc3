"""Build the fecbindings C extension.

The kernels include ``numpy/arrayobject.h``, so the NumPy include directory is
discovered at build time (the extension is a NumPy consumer, not a runtime
dependency of the build itself beyond that header).
"""

from setuptools import Extension, setup

import numpy


setup(
    ext_modules=[
        Extension(
            "fecbindings._ldpc",
            sources=["fecbindings/_ldpc.c"],
            include_dirs=[numpy.get_include()],
        ),
        Extension(
            "fecbindings._bch",
            sources=["fecbindings/_bch.c"],
            include_dirs=[numpy.get_include()],
        )
    ],
)
