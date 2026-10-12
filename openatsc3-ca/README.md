# openatsc3-ca

**Operator application for the OpenATSC3 certificate authority.**

This is the Django + PostgreSQL system-of-record layer that sits on top of the
[`openatsc3-pki`](../openatsc3-pki/) crypto toolkit.  Where `openatsc3-pki` is
a dependency-free library (it needs only `cryptography` and `asn1crypto` and
carries no web stack), this package is the heavier, application-shaped product:
a database-backed ledger, an admin UI, and management commands that drive the
CA lifecycle.

The split is deliberate: the receiver and the signer only ever need the light
library, so installing `openatsc3-pki` must not drag in Django, psycopg or a
database driver.

**No Widevine, no A3SA.**  The signaling certificates and the content
protection use our own root and our own DRM scheme.

## Design

- **PostgreSQL is the source of truth** for certificate lifecycle state.
- **Private keys stay on disk (0600)**, referenced by path + SHA-256; the
  database never holds key bytes.  A key may instead be a non-exportable **AWS
  KMS** asymmetric key, referenced by `kms_key_id` (install the `[kms]` extra).
- **Per-tenant intermediate CAs**: the offline root signs one intermediate per
  customer (`tenants/<slug>/…`); each intermediate signs that customer's leaves,
  so revoking it disables only that customer.
- The classic `<base>/*.pem` tree is a **materialized view** written by the
  `export` command and read by the receiver/signer.
- No cryptography is reimplemented here: `openatsc3_ca/catalog/services.py`
  orchestrates the `openatsc3_pki` primitives (`ca`, `x509`, `ocsp`) and records
  the result in the catalog.

## Layout

- `openatsc3_ca/` — Django project (settings, URLs, WSGI/ASGI).
- `openatsc3_ca/catalog/` — the ledger app: models, admin, management commands,
  migrations.  Its Django app label is pinned to `catalog`, so the migration
  history is unchanged by the move.
- `manage.py` — Django management entry point.
- `docker-compose.yml` — optional PostgreSQL service for deployment.

## Install

```bash
pip install -e .
```

The test extra adds a rootless embedded PostgreSQL (no Docker needed):

```bash
pip install -e '.[test]'     # Python 3.12: pgserver has no 3.13 wheel
```

## Database selection

`OPENATSC3_CA_DATABASE_URL`, else `DATABASE_URL`, else a **rootless embedded
PostgreSQL** (`pgserver`).  A `docker-compose.yml` is provided for a real
server (Docker or `podman-compose`).

## Management commands

```bash
export OPENATSC3_CA_BASE_DIR=out/ca
python manage.py migrate
python manage.py createsuperuser      # admin at /admin/
python manage.py init-root
python manage.py issue-ca
python manage.py issue-tenant-ca --tenant acme           # per-tenant intermediate
python manage.py issue-ocsp-responder
python manage.py issue-broadcaster --name WHUT --bsid 540
python manage.py issue-broadcaster --tenant acme --name ACME-TV --bsid 541
python manage.py ocsp --serial <serial>
python manage.py revoke --serial <serial>   # persistent; OCSP now says revoked
python manage.py export                     # DB -> PEM tree
python manage.py import-tree --path out/pkitest   # legacy tree -> DB
python manage.py audit
```

Models: `Tenant`, `Certificate`, `Broadcaster`, `OcspResponse`,
`OcspResponder`, `RolloverPlan` (the window constraint is already enforced),
`PublishedSignaling`, `SpecReference`, and an append-only `AuditEvent`.
`Certificate` records a key either as an on-disk path (+ SHA-256) or as a
`kms_key_id`; `--kms-key-id` on the issue commands installs a non-exportable
KMS key (install the `[kms]` extra).

## Tests

```bash
make test                    # == pytest -q tests
make test PYTHON=.venv-ca/bin/python
```

The suite runs against the embedded PostgreSQL and covers the model
constraints, revocation persistence, OCSP status transitions, the legacy-tree
import (idempotent + reversible), and the end-to-end gate where a **revoked
signer makes the receiver's `verify_certification_data` fail**.  It also covers
**per-tenant CAs**: revoking a tenant intermediate fails verification, while
revoking one tenant leaves another tenant's CDT valid.

## Status

Phase 0/1 is done: persistent revocation, a real `revoke`, the
`export`/`import-tree` tree round trip, and per-tenant intermediate CAs
(`Tenant` model + migration `0004`, `issue-tenant-ca`, KMS-backed keys).  Phase 2
(rollover/renew, `publish`) and Phase 3 (content keys, DRF, offline-root
ceremony) remain.
