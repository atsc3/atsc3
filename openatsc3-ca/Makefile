# openatsc3-ca build/test entry points.
#
#   make            install the package (editable)
#   make test       run the Django/PostgreSQL CA test suite (rootless, no Docker)
#   make migrate    apply the CA app migrations (embedded PostgreSQL)
#   make runserver  start the Django dev server (admin at /admin/)
#   make check      py_compile every Python source
#   make clean      remove build artefacts
#
# The test extra pulls pgserver, a rootless embedded PostgreSQL.  Its wheel is
# Python 3.12 only, so run the suite on 3.12:
#
#   make test PYTHON=.venv-ca/bin/python

PYTHON ?= python3

.PHONY: all install test migrate runserver check clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q tests

migrate:
	$(PYTHON) manage.py migrate

runserver:
	$(PYTHON) manage.py runserver

check:
	$(PYTHON) -m compileall -q openatsc3_ca tests

clean:
	rm -rf build dist *.egg-info .pytest_cache .pgdata .pgdata-test ca-tree
	find openatsc3_ca tests -name '__pycache__' -type d -prune -exec rm -rf {} +
	find openatsc3_ca tests -name '*.pyc' -delete
