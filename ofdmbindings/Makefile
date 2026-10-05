# Build, install and test the ofdmbindings C extension.
#
#   make            build in place (ofdmbindings/_fi*.so)
#   make install    install into the active Python environment
#   make test       run the test suite
#   make clean      remove build artefacts

PYTHON ?= python3

.PHONY: all install test wheel clean

all:
	$(PYTHON) setup.py build_ext --inplace

install:
	$(PYTHON) -m pip install .

test: all
	$(PYTHON) -m pytest tests -q

wheel:
	$(PYTHON) -m pip wheel . -w dist --no-deps

clean:
	rm -rf build dist ofdmbindings/*.so ofdmbindings/__pycache__ tests/__pycache__
