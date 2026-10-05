# openatsc3-pki

**Greenfield ATSC 3.0 certificate authority and content-protection toolkit.**

This is a separate product from the `atsc3lib` receiver.  It is a self-owned
trust anchor that runs **alongside** A3SA as a parallel CA: the standards
(A/360, A/331) define the certificate formats and validation rules, but nothing
says who must own or operate the CA.  That is the open field.

**No Widevine, no A3SA.**  The signaling certificates and the content
protection use our own root and our own DRM scheme.  A receiver configured with
our root trusts it, and only it (fail-closed).

## What is implemented

Following A/360 5.2.2 and 5.3.1:

| Piece | Spec | Module |
|---|---|---|
| Root CA (ECDSA P-384) | A/360 5.3.1.2 | `keys.py`, `x509.py`, `ca.py` |
| Issuing CA | A/360 5.3.1.3 | `x509.py`, `ca.py` |
| Broadcast signaling signer (EKU `id-atsc-kp-signalingSigning`, SDA `id-atsc-sdattr-bsid`) | A/360 5.3.1.6 | `x509.py`, `sda.py` |
| OCSP responder cert + stapled OCSP responses | A/360 5.3.1.7, RFC 6960 | `ocsp.py` |
| Detached CMS SignedData (`SigningTime`, SKI signer, no eContent/certs) | A/360 5.2.2.1, RFC 5652/5753 | `cms.py` |
| CertificationData LLS table (0x06, gzipped XML) | A/360 5.2.2.2 | `cdt.py` |
| LLS SignedMultiTable (0x07) | A/331 6.7 | `cdt.py` |
| Receiver-side verification of both | A/360 5.2.2.6 | `verify.py` |
| Content protection: CENC AES-128 CTR, `pssh`/`tenc`/`senc`/`schm`, MPD `ContentProtection`, A/331 security descriptor | ISO/IEC 23001-7, A/331 7.2.4.1, A/360 5.7 | `content.py` |

Registered OIDs (A/360 Annex A, `id-atsc = 1.3.6.1.4.1.51552`):

- `id-atsc-kp-signalingSigning` = `1.3.6.1.4.1.51552.37.3`
- `id-atsc-sdattr-bsid` = `1.3.6.1.4.1.51552.9.1`

## Install

```bash
pip install -e .
```

Requires `cryptography` and `asn1crypto`.

## Command line

```bash
openatsc3-pki init-root          --dir out/ca
openatsc3-pki issue-ca           --dir out/ca
openatsc3-pki issue-broadcaster  --dir out/ca --name WHUT --bsid 540
openatsc3-pki show               --dir out/ca --name WHUT
```

## Library

```python
from openatsc3_pki import ca, cdt, cms, ocsp, verify

a = ca.CertificateAuthority("out/ca")
a.init_root(); a.issue_issuing()
signer = a.issue_broadcaster("WHUT", (540,))

# Sign an LLS table set as a SignedMultiTable (A/331 6.7)
smt = cdt.signed_multitable(
    [cdt.LlsTable(0x01, 13, slt_xml_bytes)],
    signer, a.load_broadcaster_key("WHUT"))

# Assemble a CertificationData table with the chain and stapled OCSP
# (see tests/conftest.py for the full build), then on the receiver:
parsed = cdt.parse_certification_data(cdt_bytes)
verdict, certified = verify.verify_certification_data(parsed, [a.load_root()])
msg = cdt.parse_signed_multitable(smt)
print(verify.verify_signed_message(msg.cms, msg.signed_extent, certified, [540]))
```

## Verification rules (A/360 5.2.2.6)

`verify.verify_certification_data` enforces:

1. every certificate chain reaches a trusted root;
2. every certificate is within its validity period;
3. the CDT `CMSSignedData` signature is valid and authenticated by a carried
   certificate;
4. the CDT signing key differs from the CurrentCert/NextCert signaling keys;
5. every certificate has a matching, authentic, `good` OCSP response within the
   producedAt/nextUpdate/10-day/OCSPRefresh window.

`verify.verify_signed_message` enforces: signature validity; SigningTime not in
the future and not backward; the signer has `id-atsc-kp-signalingSigning`; its
`id-atsc-sdattr-bsid` set equals the SLT's bsid set; its SKI matches
CurrentCert/NextCert; the signing time is within the rollover window; and the
OCSP response is good and fresh.

## Tests

```bash
pytest -q
```

`tests/data/cdt_example.xml` is the **published ATSC CertificationData example**
(schema repo `atsc-schemas.org`), used as an external oracle: it pins the
`id-atsc-sdattr-bsid` encoding and the real-ATSC certificate profile.

## Django + PostgreSQL CA management app (Gap A closure)

The lifecycle gap — persistent revocation, a real `revoke`, and rollover — is
closed by an optional Django app that lives in this package.  It is the
operator / system-of-record layer; **the crypto library above needs none of
it**, and PostgreSQL is the source of truth.

- `openatsc3_ca/` — Django project (settings, URLs, WSGI/ASGI).
- `catalog/` — the ledger app: models, admin, management commands, migrations.

**Keys stay on disk (0600)** and are referenced by path + SHA-256; the DB never
holds key bytes.  The classic `<base>/*.pem` tree is materialized from the
database by the `export` command (the receiver/signer still read it).

Install (Python 3.12; the database driver is optional):

```bash
pip install -e '.[web]'
```

Database selection: `OPENATSC3_CA_DATABASE_URL`, else `DATABASE_URL`, else a
**rootless embedded PostgreSQL** (`pgserver`, no Docker).  A `docker-compose.yml`
is provided for a real server (Docker or `podman-compose`).

```bash
export OPENATSC3_CA_BASE_DIR=out/ca
python manage.py migrate
python manage.py createsuperuser      # admin at /admin/
python manage.py init-root
python manage.py issue-ca
python manage.py issue-ocsp-responder
python manage.py issue-broadcaster --name WHUT --bsid 540
python manage.py ocsp --serial <serial>
python manage.py revoke --serial <serial>   # persistent; OCSP now says revoked
python manage.py export                     # DB -> PEM tree
python manage.py import-tree --path out/pkitest   # legacy tree -> DB (data migration)
python manage.py audit
```

Models: `Certificate`, `Broadcaster`, `OcspResponse`, `OcspResponder`,
`RolloverPlan` (Phase 2 drives it, the window constraint is already enforced),
`PublishedSignaling`, `SpecReference`, and an append-only `AuditEvent`.

Tests: `make test-ca` runs the Django/PostgreSQL suite against the embedded
server (Python 3.12; `pgserver` has no 3.13 wheel).  It covers the model
constraints, revocation persistence, OCSP status transitions, the legacy-tree
data migration (and its reversal), and the end-to-end gate where a **revoked
signer makes the receiver's `verify_certification_data` fail**.  `make test-pki`
stays database-free.

## Not yet implemented

- The `atsc3lib` receiver-side integration is in place (`security/` package,
  `--trust-root` / `--security-provider` / `--require-signature`); the synthetic
  A/331-layer gate is `atsc3lib/tests/test_security.py`.
- Phase 2 of the CA app: rollover/renew lifecycle, `publish` (rebuild CDT/SMT
  from live status), and the end-to-end receiver gate over the published tree.
- HSM / offline root key handling (Phase 3); a pluggable `KeyStore` backend.
- ROUTE SLS fragment signing (A/360 5.2.2.4) and MMTP `signed_mmt_message()`
  (5.2.2.5) beyond the SLT/SLT-table path already covered.
- Read-only DRF API (v1 is admin-only).
