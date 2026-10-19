"""Build the optional CUDA accelerators in ``gpubindings``.

Deliberately **not** part of the uv workspace: the receiver must install and
run on a machine with no CUDA toolkit, so ``gpubindings`` is a standalone
optional package that call sites reach through ``try: import``.  The required
path stays the C kernels in ``atsc3lib._bindings``.

Install on a GPU host with::

    pip install ./gpubindings
    # or, in place:
    python setup.py build_ext --inplace

The ``.cu`` translation unit carries both the kernels and the CPython glue, so
nvcc builds the extension directly instead of going through a setuptools
``Extension`` (which would need a separate C shim to link the two).

Architecture defaults to ``-arch=native`` (the build host's GPU).  Pin it with
``ATSC3_CUDA_ARCH``, e.g. ``ATSC3_CUDA_ARCH=sm_89`` (Ada) or ``sm_120``
(Blackwell) when building off-box.
"""

import os
import subprocess
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


_HERE = Path(__file__).resolve().parent
# setuptools rejects absolute paths in Extension.sources, so keep the declared
# sources relative and resolve them against the package root at build time.
_CUDA_SOURCES = [
    "gpubindings/_gpu_demap.cu",
    "gpubindings/_gpu_ldpc.cu",
]


class _CudaBuildExt(build_ext):
    """Compile each ``.cu`` straight to an extension module with nvcc."""

    pkg_root = _HERE

    def build_extensions(self):
        import numpy

        python_include = sysconfig.get_paths()["include"]
        suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        arch = os.environ.get("ATSC3_CUDA_ARCH")
        arch_flag = "-arch=native" if not arch else "-arch={}".format(arch)

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
                arch_flag,
                "-shared",
                "-Xcompiler", "-fPIC",
                "-I", python_include,
                "-I", numpy.get_include(),
                "-o", str(out.resolve()),
            ] + sources
            self.announce("nvcc " + " ".join(cmd), level=2)
            try:
                subprocess.run(cmd, check=True)
            except FileNotFoundError:
                raise SystemExit(
                    "nvcc not found. gpubindings is optional: install it only "
                    "on a host with the CUDA toolkit "
                    "(see meta/HARDWARE-live-rig.md)."
                )


setup(
    ext_modules=[
        Extension("gpubindings." + Path(src).stem, sources=[src])
        for src in _CUDA_SOURCES
    ],
    cmdclass={"build_ext": _CudaBuildExt},
)
