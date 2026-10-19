# gpubindings: optional CUDA kernels for atsc3lib.
#
# The package installs without the CUDA toolkit (the Python wrappers are still
# importable and available() is False); ATSC3_CUDA_REQUIRED=1 makes a missing
# nvcc a hard error on a GPU host.

UV ?= uv
PYTHON ?= python3

.PHONY: all install install-gpu test clean

all: install

install:
	$(UV) pip install .

install-gpu:
	ATSC3_CUDA_REQUIRED=1 $(UV) pip install .

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf build dist *.egg-info
	find . -name '__pycache__' -prune -exec rm -rf {} +
	find . -name '*.so' -delete
