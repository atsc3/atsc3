# openatsc3-pki build/test entry points.
#
#   make          install the package (editable)
#   make test     run the test suite
#   make clean    remove build artefacts

PYTHON ?= python3

.PHONY: all install test clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find openatsc3_pki tests -name '__pycache__' -type d -prune -exec rm -rf {} +
	find openatsc3_pki tests -name '*.pyc' -delete
