# ATSC 3.0 monorepo top-level orchestration.
#
# The workspace is managed with uv (https://docs.astral.sh/uv/).  A single
# root .venv holds every package editable: atsc3lib plus the compiled
# bindings (sdrbindings, fecbindings, demodbindings, ofdmbindings,
# ac4bindings) and the optional openatsc3-pki.
#
#   make              install the whole workspace (uv sync)
#   make test         run the atsc3lib test suite
#   make test-all     run every package's test suite
#   make test-fec     fecbindings (LDPC + BCH) tests
#   make test-demod   demodbindings (max-log demapper) tests
#   make test-ofdm    ofdmbindings (frequency interleaver) tests
#   make test-ac4     ac4bindings (AC-4) tests
#   make test-sdr     sdrbindings (SoapySDR) tests
#   make test-pki     openatsc3-pki tests
#   make check        py_compile every Python source (no linter is configured)
#   make clean        remove build artefacts

UV ?= uv
RUN ?= $(UV) run --no-sync

.PHONY: all install test test-all test-fec test-demod test-ofdm test-ac4 \
        test-sdr test-pki check clean

all: install

install:
	$(UV) sync

test:
	$(RUN) python -m pytest atsc3lib/tests -q

test-all: test test-fec test-demod test-ofdm test-ac4 test-sdr test-pki

test-fec:
	$(RUN) python -m pytest fecbindings/tests -q

test-demod:
	$(RUN) python -m pytest demodbindings/tests -q

test-ofdm:
	$(RUN) python -m pytest ofdmbindings/tests -q

test-ac4:
	$(RUN) python -m pytest ac4bindings/tests -q

test-sdr:
	$(RUN) python -m pytest sdrbindings/tests -q

test-pki:
	$(RUN) python -m pytest openatsc3-pki/tests -q

check:
	$(RUN) python -m compileall -q atsc3lib/atsc3lib openatsc3-pki/openatsc3_pki

clean:
	rm -rf build dist *.egg-info
	find . -path ./.venv -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print -exec rm -rf {} +
	find . -path ./.venv -prune -o -name '*.so' -print -delete
