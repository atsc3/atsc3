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
python manage.py issue-root-responder                     # root-delegated revocation responder
python manage.py issue-ocsp-responder
python manage.py issue-ocsp-responder --tenant acme       # responder for a tenant's leaves
python manage.py issue-broadcaster --name WHUT --bsid 540
python manage.py issue-broadcaster --tenant acme --name ACME-TV --bsid 541
python manage.py issue-cdt-signer --tenant acme --name ACME-TV --bsid 541
python manage.py publish --name ACME-TV                    # CDT 0x06
python manage.py publish --name ACME-TV --tenant acme --lls 0x01:13:slt.xml   # CDT + signed SMT 0x07
python manage.py start-rollover --current <serial> --next <serial>
python manage.py complete-rollover --plan <id>
python manage.py create-checkout --id <order>     # Stripe Checkout Session
python manage.py approve-order --id <order>       # issue tenant CA + deliver
python manage.py ocsp --serial <serial>
python manage.py revoke --serial <serial>   # persistent; OCSP now says revoked
python manage.py export                     # DB -> PEM tree (root + tenants + responders)
python manage.py import-tree --path out/pkitest   # on-disk tree -> DB (legacy + tenants + responders)
python manage.py audit
```


Models: `Tenant`, `Certificate`, `Broadcaster`, `Order`, `OcspResponse`,
`OcspResponder`, `RolloverPlan` (the window constraint is already enforced),
`PublishedSignaling`, `SpecReference`, and an append-only `AuditEvent`.
`Certificate` records a key either as an on-disk path (+ SHA-256) or as a
`kms_key_id`; `--kms-key-id` on the issue commands installs a non-exportable
KMS key (install the `[kms]` extra). Broadcaster names are **scoped per tenant**
(unique within a tenant, and separately unique in the legacy `tenant=None`
tree), so two customers may use the same station name.

## Sales + delivery (Phase 2)

- **Orders.** An `Order` (email/organization/broadcaster/bsids/amount) moves
  `pending -> paid -> fulfilled`. `create_checkout` opens a **Stripe Checkout
  Session** (`[stripe]` extra; the `stripe` SDK is injected so tests run
  offline); `POST /stripe/webhook/` verifies the signature and marks the order
  `paid`.
- **Manual approval.** The admin `Order` action (or `approve-order`) issues the
  tenant CA + leaves, `publish`es the signed bundle, and **delivers** it. The
  bundle (`certs.pem`, `cdt.xml.gz`, `ocsp-NN.der`, optional `signing.smt`) is
  written to `OPENATSC3_CA_DELIVERY_DIR`, or uploaded to S3 with a **presigned
  URL** when `OPENATSC3_CA_S3_BUCKET` is set (`[aws]` extra). A CRL is not
  produced — revocation is carried by the stapled OCSP.
- Settings: `OPENATSC3_CA_STRIPE_SECRET_KEY`, `..._WEBHOOK_SECRET`,
  `..._PUBLIC_BASE_URL`, `..._DELIVERY_DIR`, `..._S3_BUCKET`/`_S3_REGION`,
  `..._DELIVERY_TTL`.

## Signed bundle (`publish`)

`publish` assembles the customer's **CertificationData (`0x06`)** from the
ledger — the issuing/tenant CA chain, the CurrentCert, the distinct CDT signer,
and a fresh stapled OCSP per certificate — and, when `--lls` tables are given,
signs them into an **LLS SignedMultiTable (`0x07`)** with the CurrentCert
(A/331 6.7). An open rollover window adds the `CertReplacement` element and
carries the NextCert. Output files land under `published/<broadcaster>/` and a
`PublishedSignaling` snapshot is recorded. (A customer-facing upload endpoint is
Phase 2; today the operator supplies tables via `--lls`.)
## Tests

```bash
make test                    # == pytest -q tests
make test PYTHON=.venv-ca/bin/python
```

The suite runs against the embedded PostgreSQL and covers the model
constraints, revocation persistence, OCSP status transitions, the legacy-tree
import (idempotent + reversible), the end-to-end gate where a **revoked
signer makes the receiver's `verify_certification_data` fail**, **per-tenant
CAs** (revoking a tenant intermediate fails verification; revoking one tenant
leaves another valid), the signed bundle (`publish` builds a verifiable CDT
and signs an SMT; a rollover window carries the NextCert), and **sales**
(Checkout session, webhook -> paid, approval issues + delivers the bundle,
S3 presign).

## Status

Phases 0–2 are done: per-tenant intermediate CAs (`Tenant` + migration `0004`,
`issue-tenant-ca`, KMS-backed keys); the signed bundle (`publish` CDT `0x06` +
signed SMT `0x07`, `issue-cdt-signer`, `start-rollover`/`complete-rollover`);
and sales/delivery (`Order` + migration `0005`, Stripe Checkout + webhook,
manual approval -> issue + publish + deliver, S3 presigned URLs). Phase 3
(infra: VPC/RDS/App Runner/S3+CloudFront/IAM; offline-root ceremony) and Phase 4
(hardening) remain.
