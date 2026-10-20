"""Tests for the ``ATSC3_CUDA_ARCH`` to nvcc flag translation.

The helper is loaded by path so the test also runs from a source tree where
``gpubindings`` has not been installed (the outer directory is a namespace
package until then).
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_cuda_arch",
    Path(__file__).resolve().parents[1] / "gpubindings" / "_cuda_arch.py",
)
_cuda_arch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cuda_arch)

RELEASE_ARCHS = _cuda_arch.RELEASE_ARCHS
nvcc_arch_flags = _cuda_arch.nvcc_arch_flags
parse_arch_list = _cuda_arch.parse_arch_list


def test_unset_uses_native():
    assert nvcc_arch_flags(None) == ["-arch=native"]
    assert nvcc_arch_flags("native") == ["-arch=native"]
    assert nvcc_arch_flags(" NATIVE ") == ["-arch=native"]


def test_list_builds_fat_binary():
    flags = nvcc_arch_flags("sm_75, sm_80;compute_89 90 sm_100,sm_120")
    assert flags == [
        "-gencode", "arch=compute_75,code=sm_75",
        "-gencode", "arch=compute_80,code=sm_80",
        "-gencode", "arch=compute_89,code=sm_89",
        "-gencode", "arch=compute_90,code=sm_90",
        "-gencode", "arch=compute_100,code=sm_100",
        "-gencode", "arch=compute_120,code=sm_120",
        "-gencode", "arch=compute_120,code=compute_120",
    ]


def test_single_arch_is_cubin_plus_ptx():
    assert nvcc_arch_flags("89") == [
        "-gencode", "arch=compute_89,code=sm_89",
        "-gencode", "arch=compute_89,code=compute_89",
    ]


def test_release_list_covers_the_supported_families():
    assert parse_arch_list(RELEASE_ARCHS) == (
        "sm_75", "sm_80", "sm_89", "sm_90", "sm_100", "sm_120",
    )
    assert nvcc_arch_flags("release") == nvcc_arch_flags(RELEASE_ARCHS)


def test_duplicates_and_order():
    assert parse_arch_list("sm_120,75,sm_75") == ("sm_75", "sm_120")


@pytest.mark.parametrize("value", ["", " ", "sm_", "sm_90a", "foo", "8.9"])
def test_invalid(value):
    with pytest.raises(ValueError):
        nvcc_arch_flags(value)
