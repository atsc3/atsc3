"""Build the demodbindings C extension."""

from setuptools import Extension, setup

import numpy


setup(
    ext_modules=[
        Extension(
            "demodbindings._demap",
            sources=["demodbindings/_demap.c"],
            include_dirs=[numpy.get_include()],
        )
    ],
)
