"""Build the optional CUDA accelerators in ``gpubindings``.

``gpubindings`` is a monorepo workspace member, but the CUDA kernels are
optional: the receiver must install and run on a machine with no toolkit, and
every call site reaches this package through ``try: import``.  The required
path stays the C kernels in ``atsc3lib._bindings``.

A normal install on a host without ``nvcc`` succeeds and ships only the Python
wrappers (``available()`` is then False, so the CPU path runs).  Set
``ATSC3_CUDA_REQUIRED=1`` on a GPU host to make a missing toolkit a hard error.

Build on a GPU host with::

    uv sync --package atsc3lib --extra gpu     # or: make install-gpu
    # in place:
    python setup.py build_ext --inplace

The ``.cu`` translation unit carries both the kernels and the CPython glue, so
nvcc builds the extension directly instead of going through a setuptools
``Extension`` (which would need a separate C shim to link the two).

Architecture defaults to ``-arch=native`` (the build host's GPU).  Pin it with
``ATSC3_CUDA_ARCH`` when building off-box: a single ``sm_XX`` (for example
``sm_89`` for Ada or ``sm_120`` for Blackwell), a comma-separated list, or
``release`` for the fat wheel covering
``sm_75,sm_80,sm_89,sm_90,sm_100,sm_120``.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


_HERE = Path(__file__).resolve().parent

_ARCH_SPEC = importlib.util.spec_from_file_location(
    "_cuda_arch", _HERE / "gpubindings" / "_cuda_arch.py")
_cuda_arch = importlib.util.module_from_spec(_ARCH_SPEC)
_ARCH_SPEC.loader.exec_module(_cuda_arch)
# setuptools rejects absolute paths in Extension.sources, so keep the declared
# sources relative and resolve them against the package root at build time.
_CUDA_SOURCES = [
    "gpubindings/_gpu_demap.cu",
    "gpubindings/_gpu_ldpc.cu",
]

_REQUIRED = os.environ.get("ATSC3_CUDA_REQUIRED", "").strip().lower() in (
    "1", "on", "true", "yes")


class _CudaBuildExt(build_ext):
    """Compile each ``.cu`` straight to an extension module with nvcc."""

    pkg_root = _HERE

    def build_extensions(self):
        import numpy

        if shutil.which("nvcc") is None:
            message = (
                "nvcc not found; building gpubindings without the optional "
                "CUDA kernels. Install the CUDA toolkit and rerun with "
                "ATSC3_CUDA_REQUIRED=1 to require them "
                "(see meta/HARDWARE-live-rig.md)."
            )
            if _REQUIRED:
                raise SystemExit(message)
            print("warning: " + message, file=sys.stderr)
            return

        python_include = sysconfig.get_paths()["include"]
        suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        arch_flags = _cuda_arch.nvcc_arch_flags(
            os.environ.get("ATSC3_CUDA_ARCH"))

        for ext in self.extensions:
            out = Path(self.build_lib) / (
                ext.name.replace(".", "/") + suffix
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            sources = [str((Path(self.pkg_root) / s).resolve())
                       for s in ext.sources]
            cmd = [
                "nvcc",
                "-O3",
                *arch_flags,
                "-shared",
                "-Xcompiler", "-fPIC",
                "-I", python_include,
                "-I", numpy.get_include(),
                "-o", str(out.resolve()),
            ] + sources
            self.announce("nvcc " + " ".join(cmd), level=2)
            subprocess.run(cmd, check=True)


setup(
    ext_modules=[
        Extension("gpubindings." + Path(src).stem, sources=[src])
        for src in _CUDA_SOURCES
    ],
    cmdclass={"build_ext": _CudaBuildExt},
)
