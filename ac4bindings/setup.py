"""Build the ac4bindings C extension.

The AC-4 kernel returns NumPy arrays, so the NumPy include directory is
discovered at build time (the extension is a NumPy consumer, not a build-time
dependency beyond that header).
"""

from setuptools import Extension, setup

import numpy


setup(
    ext_modules=[
        Extension(
            "ac4bindings._ac4",
            sources=["ac4bindings/_ac4.c"],
            include_dirs=[numpy.get_include()],
        ),
    ],
)
