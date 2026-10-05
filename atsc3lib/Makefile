# atsc3lib build/test entry points.
#
# sdrbindings (SDRplay capture), fecbindings (LDPC + BCH), demodbindings
# (max-log demapper), ofdmbindings (frequency interleaver) and ac4bindings
# (AC-4 audio) are separate compiled packages and declared dependencies,
# installed with the project rather than built here.
#
#   make             install atsc3lib (editable) and its dependencies
#   make test        run the atsc3lib test suite
#   make test-fec    run the fecbindings test suite
#   make test-demod  run the demodbindings test suite
#   make test-ofdm   run the ofdmbindings test suite
#   make test-ac4    run the ac4bindings test suite
#   make clean       remove build artefacts

PYTHON ?= python3

FECBINDINGS_DIR ?= ../fecbindings
DEMODBINDINGS_DIR ?= ../demodbindings
OFDMBINDINGS_DIR ?= ../ofdmbindings
AC4BINDINGS_DIR ?= ../ac4bindings

.PHONY: all install test test-fec test-demod test-ofdm test-ac4 clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q

test-fec:
	$(MAKE) -C $(FECBINDINGS_DIR) test

test-demod:
	$(MAKE) -C $(DEMODBINDINGS_DIR) test

test-ofdm:
	$(MAKE) -C $(OFDMBINDINGS_DIR) test

test-ac4:
	$(MAKE) -C $(AC4BINDINGS_DIR) test

clean:
	rm -rf build dist *.egg-info atsc3lib/__pycache__ tests/__pycache__
	find atsc3lib tests -name '*.pyc' -delete
