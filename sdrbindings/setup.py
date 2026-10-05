"""Build the sdrbindings C extension against SoapySDR.

SoapySDR is located with pkg-config when available, falling back to the
default include/library paths (``-lSoapySDR``).
"""

import subprocess
from pathlib import Path

from setuptools import Extension, setup


def _pkg_config(flag):
    try:
        out = subprocess.run(
            ["pkg-config", flag, "SoapySDR"],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return out.stdout.split()


include_dirs = []
library_dirs = []
libraries = ["SoapySDR"]
extra_compile_args = []

for token in _pkg_config("--cflags"):
    if token.startswith("-I"):
        include_dirs.append(token[2:])
for token in _pkg_config("--libs"):
    if token.startswith("-L"):
        library_dirs.append(token[2:])
    elif token.startswith("-l"):
        name = token[2:]
        if name not in libraries:
            libraries.append(name)

setup(
    ext_modules=[
        Extension(
            "sdrbindings._core",
            sources=[str(Path("sdrbindings") / "_core.c")],
            include_dirs=include_dirs,
            library_dirs=library_dirs,
            libraries=libraries,
            extra_compile_args=extra_compile_args,
        )
    ],
)
