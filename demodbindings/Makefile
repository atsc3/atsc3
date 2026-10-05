# Build, install and test the demodbindings C extension.
#
#   make            build in place (demodbindings/_demap*.so)
#   make install    install into the active Python environment
#   make test       run the test suite
#   make wheel      build a wheel into dist/
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
	rm -rf build dist demodbindings/*.so demodbindings/__pycache__ tests/__pycache__
