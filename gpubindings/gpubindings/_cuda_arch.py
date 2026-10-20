"""Translate ``ATSC3_CUDA_ARCH`` into nvcc architecture flags.

CUDA 12.8's nvcc expands ``-arch=sm_XX`` to a cubin plus PTX for that one
architecture (NVCC manual 4.2.7.1).  A list therefore emits one cubin per
entry and PTX for the newest entry: a fat binary that runs natively on each
listed generation and lets the driver JIT the kernels on later GPUs.

The release wheel uses :data:`RELEASE_ARCHS`: Turing (the CUDA 13 floor),
Ampere, Ada, Hopper, datacenter Blackwell, and consumer Blackwell.  Ampere's
``sm_80`` cubin is binary-compatible with ``sm_86``/``sm_89``, so those need no
separate cubin; ``sm_89`` is kept for native Ada code.
"""

from __future__ import annotations

import re

NATIVE = "native"
RELEASE = "release"

RELEASE_ARCHS = "sm_75,sm_80,sm_89,sm_90,sm_100,sm_120"

_ARCH_RE = re.compile(r"(?:sm_|compute_)?(\d+)")


def parse_arch_list(value: str) -> tuple[str, ...]:
    """Return normalised ``sm_XX`` targets, sorted by capability."""
    tokens = [token for token in re.split(r"[,;\s]+", value.strip()) if token]
    if not tokens:
        raise ValueError("ATSC3_CUDA_ARCH is empty")
    arches = set()
    for token in tokens:
        match = _ARCH_RE.fullmatch(token)
        if match is None:
            raise ValueError(
                "unsupported ATSC3_CUDA_ARCH entry {!r}; use native or "
                "sm_XX entries such as sm_75,sm_120".format(token)
            )
        arches.add(int(match.group(1)))
    return tuple("sm_{}".format(arch) for arch in sorted(arches))


def nvcc_arch_flags(value: str | None) -> list[str]:
    """Return the nvcc flags for an ``ATSC3_CUDA_ARCH`` value."""
    if value is None or value.strip().lower() == NATIVE:
        return ["-arch=native"]
    value = value.strip()
    if value.lower() == RELEASE:
        value = RELEASE_ARCHS
    arches = parse_arch_list(value)
    flags: list[str] = []
    for arch in arches:
        number = arch.removeprefix("sm_")
        flags += ["-gencode", "arch=compute_{0},code=sm_{0}".format(number)]
    newest = arches[-1].removeprefix("sm_")
    flags += ["-gencode", "arch=compute_{0},code=compute_{0}".format(newest)]
    return flags
