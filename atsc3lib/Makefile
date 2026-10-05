# atsc3lib build/test entry points.
#
# atsc3lib builds its own compiled kernels (LDPC + BCH, max-log demapper,
# frequency interleaver) as extensions under atsc3lib._bindings; they are
# installed with the package rather than built here separately.  sdrbindings
# (SDRplay capture) and ac4bindings (AC-4 audio) remain separate compiled
# packages and declared dependencies.
#
#   make             install atsc3lib (editable, compiling its kernels)
#   make test        run the atsc3lib test suite
#   make clean       remove build artefacts

PYTHON ?= python3

.PHONY: all install test clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf build dist *.egg-info atsc3lib/__pycache__ tests/__pycache__
	find atsc3lib tests -name '*.pyc' -delete
	find atsc3lib -name '*.so' -delete
