# ATSC 3.0 monorepo top-level orchestration.
#
# The workspace is managed with uv (https://docs.astral.sh/uv/).  A single
# root .venv holds every package editable: atsc3lib (which now builds its own
# compiled kernels: LDPC + BCH, max-log demapper, frequency interleaver), the
# separate sdrbindings (SDRplay/SoapySDR) and ac4bindings (AC-4) extensions,
# and the optional openatsc3-pki.
#
#   make              install the whole workspace (uv sync)
#   make test         run the atsc3lib test suite (includes the C kernels)
#   make test-all     run every package's test suite
#   make test-ac4     ac4bindings (AC-4) tests
#   make test-sdr     sdrbindings (SoapySDR) tests
#   make test-pki     openatsc3-pki tests
#   make test-ca      openatsc3-pki Django + PostgreSQL CA tests (Python 3.12)
#   make ca-venv      create the Python 3.12 venv the CA app needs
#   make ca-migrate   apply the CA app migrations (embedded PostgreSQL)
#   make check        py_compile every Python source (no linter is configured)
#   make clean        remove build artefacts

UV ?= uv
RUN ?= $(UV) run --no-sync
CA_PYTHON ?= openatsc3-pki/.venv-ca/bin/python

.PHONY: all install test test-all test-ac4 test-sdr test-pki test-ca \
        ca-venv ca-migrate check clean

all: install

install:
	$(UV) sync --all-packages

test:
	$(RUN) pytest atsc3lib/tests -q

test-all: test test-ac4 test-sdr test-pki

test-ac4:
	$(RUN) pytest ac4bindings/tests -q

test-sdr:
	$(RUN) pytest sdrbindings/tests -q

test-pki:
	$(RUN) pytest openatsc3-pki/tests -q

# Python 3.12 is required: pgserver (the rootless embedded PostgreSQL the CA
# tests use) has no 3.13 wheel.
ca-venv:
	$(UV) venv --python 3.12 openatsc3-pki/.venv-ca
	VIRTUAL_ENV=openatsc3-pki/.venv-ca $(UV) pip install -e 'openatsc3-pki[web,test-ca]'

test-ca:
	$(MAKE) -C openatsc3-pki test-ca CA_PYTHON=$(abspath $(CA_PYTHON))

ca-migrate:
	$(CA_PYTHON) openatsc3-pki/manage.py migrate

check:
	$(RUN) python -m compileall -q atsc3lib/atsc3lib openatsc3-pki/openatsc3_pki openatsc3-pki/openatsc3_ca openatsc3-pki/catalog

clean:
	rm -rf build dist *.egg-info
	find . -path ./.venv -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print -exec rm -rf {} +
	find . -path ./.venv -prune -o -name '*.so' -print -delete
