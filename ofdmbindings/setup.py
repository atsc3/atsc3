"""Build the ofdmbindings C extension."""

from setuptools import Extension, setup

import numpy


setup(
    ext_modules=[
        Extension(
            "ofdmbindings._fi",
            sources=["ofdmbindings/_fi.c"],
            include_dirs=[numpy.get_include()],
        )
    ],
)
