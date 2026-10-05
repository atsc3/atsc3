# openatsc3-pki build/test entry points.
#
#   make            install the package (editable)
#   make test       run the crypto test suite (no Django/DB needed)
#   make test-ca    run the Django/PostgreSQL CA tests (rootless, no Docker)
#   make web        install the optional Django CA app
#   make migrate    apply the CA app migrations (embedded PostgreSQL)
#   make check      py_compile every Python source
#   make clean      remove build artefacts
#
# The CA tests need Python 3.12 (pgserver has no 3.13 wheel).  They run against
# an embedded, rootless PostgreSQL; set OPENATSC3_CA_DATABASE_URL to use a real
# server instead (e.g. the docker-compose one).
#
#   make test-ca PYTHON=.venv-ca/bin/python

PYTHON ?= python3
CA_PYTHON ?= $(PYTHON)
CA_ENV = OPENATSC3_CA_TEST=1 DJANGO_SETTINGS_MODULE=openatsc3_ca.settings

.PHONY: all install test test-ca web migrate check clean

all: install

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -q tests

test-ca:
	$(CA_ENV) $(CA_PYTHON) -m pytest -q tests

web:
	$(CA_PYTHON) -m pip install -e '.[web]'

migrate:
	$(CA_PYTHON) manage.py migrate

check:
	$(PYTHON) -m compileall -q openatsc3_pki openatsc3_ca catalog tests

clean:
	rm -rf build dist *.egg-info .pytest_cache .pgdata .pgdata-test ca-tree
	find openatsc3_pki openatsc3_ca catalog tests -name '__pycache__' -type d -prune -exec rm -rf {} +
	find openatsc3_pki openatsc3_ca catalog tests -name '*.pyc' -delete
