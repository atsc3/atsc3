# Build, install and test the ac4bindings C extension.
#
#   make            build in place (ac4bindings/_ac4*.so)
#   make install    install into the active Python environment
#   make test       run the test suite
#   make wheel      build a wheel into dist/
#   make clean      remove build artefacts
#
# The build goes through pip (PEP 517) so the build requirements declared in
# pyproject.toml -- setuptools, wheel and numpy's headers -- are installed into
# an isolated build environment.  Calling setup.py directly would skip them and
# fail on a bare interpreter (Python 3.12 no longer bundles setuptools).

PYTHON ?= python3

.PHONY: all install test wheel clean

all:
	$(PYTHON) -m pip install -e . --quiet

install:
	$(PYTHON) -m pip install .

test: all
	$(PYTHON) -m pytest tests -q

wheel:
	$(PYTHON) -m pip wheel . -w dist --no-deps

clean:
	rm -rf build dist ac4bindings/*.so ac4bindings/__pycache__ tests/__pycache__
