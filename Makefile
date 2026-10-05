# ATSC 3.0 monorepo top-level orchestration.
#
# The workspace is managed with uv (https://docs.astral.sh/uv/).  A single
# root .venv holds the receiver stack editable: atsc3lib (which builds its own
# compiled kernels: LDPC + BCH, max-log demapper, frequency interleaver), the
# separate sdrbindings (SDRplay/SoapySDR) and ac4bindings (AC-4) extensions,
# and the optional openatsc3-pki crypto library.
#
#   make              install the receiver stack (scoped: NO Django/PostgreSQL)
#   make install-all  install every package incl. the openatsc3-ca Django app
#   make test         run the atsc3lib test suite (includes the C kernels)
#   make test-all     run every package's test suite
#   make test-ac4     ac4bindings (AC-4) tests
#   make test-sdr     sdrbindings (SoapySDR) tests
#   make test-pki     openatsc3-pki crypto tests (no Django/DB)
#   make test-ca      openatsc3-ca Django + PostgreSQL CA tests (Python 3.12)
#   make ca-venv      create the Python 3.12 venv the CA app needs
#   make ca-migrate   apply the CA app migrations (embedded PostgreSQL)
#   make check        py_compile every Python source (no linter is configured)
#   make clean        remove build artefacts
#
# The CA operator app (openatsc3-ca) drags in Django + psycopg, so the default
# install scopes the sync to atsc3lib + the light openatsc3-pki library.  Use
# install-all (or the openatsc3-ca/.venv-ca environment) for the app.

UV ?= uv
RUN ?= $(UV) run --no-sync
CA_PYTHON ?= openatsc3-ca/.venv-ca/bin/python

.PHONY: all install install-all test test-all test-ac4 test-sdr test-pki test-ca \
        ca-venv ca-migrate check clean

all: install

install:
	$(UV) sync --package atsc3lib --extra pki --group dev

install-all:
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
	$(UV) venv --python 3.12 openatsc3-ca/.venv-ca
	VIRTUAL_ENV=openatsc3-ca/.venv-ca $(UV) pip install -e './openatsc3-ca[test]'

test-ca:
	$(MAKE) -C openatsc3-ca test PYTHON=$(abspath $(CA_PYTHON))

ca-migrate:
	$(CA_PYTHON) openatsc3-ca/manage.py migrate

check:
	$(RUN) python -m compileall -q atsc3lib/atsc3lib openatsc3-pki/openatsc3_pki openatsc3-ca/openatsc3_ca

clean:
	rm -rf build dist *.egg-info
	find . -path ./.venv -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print -exec rm -rf {} +
	find . -path ./.venv -prune -o -name '*.so' -print -delete
