# openatsc3-pki build/test entry points.
#
#   make            install the package (editable)
#   make test       run the crypto test suite (no Django/DB needed)
#   make check      py_compile every Python source
#   make clean      remove build artefacts
#
# The CA operator application (Django + PostgreSQL) now lives in the separate
# openatsc3-ca package.

PYTHON ?= python3

.PHONY: all install test check clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q tests

check:
	$(PYTHON) -m compileall -q openatsc3_pki tests

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find openatsc3_pki tests -name '__pycache__' -type d -prune -exec rm -rf {} +
	find openatsc3_pki tests -name '*.pyc' -delete
