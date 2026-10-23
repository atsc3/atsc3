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

# Per-tenant intermediate CA signed by the root, signing that tenant's leaves:
a.issue_tenant_ca("acme")
acme_signer = a.issue_broadcaster("ACME-TV", (540,), tenant="acme")

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

## Signing backends

Every signing path (X.509 issuance, CMS `SignedData`, OCSP responses) goes
through `openatsc3_pki.signer.Signer`, so the private key can live anywhere:

- `LocalSigner` wraps an in-process `cryptography` key — the default and the
  only backend the offline tests need.
- `KmsSigner` signs with an AWS KMS asymmetric key (`kms:Sign`); the key never
  leaves KMS. `boto3` is imported lazily (`pip install -e '.[kms]'`), so the
  library stays dependency-light and is not locked to AWS.
- `EnvelopeSigner` is the lower-cost leaf backend: an EC data key pair wrapped
  by a KMS symmetric CMK; it calls `kms:Decrypt`, signs in process, and discards
  the private key. The tenant CA key remains a `KmsSigner` in production.

The existing `keys.KeyPair` API is unchanged — any function that took a
`KeyPair` also accepts a `Signer`, and KMS key specs map to the A/360
curve/digest pairs (P-256/SHA-256, P-384/SHA-384; KMS has no P-521).

## Per-tenant intermediate CAs

The root only signs **per-tenant intermediate CAs**; each tenant intermediate
signs that customer's leaves (CurrentCert, NextCert, CDT signer, OCSP
responder).  Revoking one tenant intermediate disables only that customer.

`CertificateAuthority` keeps the legacy single `issuing/` tree as the default,
and additionally supports a tenant-scoped tree:

```
<base>/tenants/<tenant>/issuing.{cert,key}.pem
<base>/tenants/<tenant>/broadcasters/<name>/signing.{cert,key}.pem
```

- `x509.issue_tenant_ca` and `ca.CertificateAuthority.issue_tenant_ca` create a
  tenant CA signed by the root (A/360 5.3.1.3).
- `issue_broadcaster(..., tenant="<slug>")` issues a leaf under that tenant's
  intermediate; `load_tenant` / `load_tenant_key` read it back.
- Any of these accept an external `Signer` (KMS) for the subject key and/or the
  issuing key; when external, no private key file is written.

## CA operator application

The lifecycle layer — persistent revocation, a real `revoke`, and rollover — is
**not** part of this library.  It lives in the separate
[`openatsc3-ca`](../openatsc3-ca/) package (Django + PostgreSQL), which depends on
`openatsc3-pki` and drives these primitives.  Keeping it out means this package
stays a dependency-light crypto library (only `cryptography` and `asn1crypto`),
which is all the receiver and signer need.

## Not yet implemented

- The `atsc3lib` receiver-side integration is in place (`security/` package,
  `--trust-root` / `--security-provider` / `--require-signature`); the synthetic
  A/331-layer gate is `atsc3lib/tests/test_security.py`.
- Phase 2 of the CA app: rollover/renew lifecycle, `publish` (rebuild CDT/SMT
  from live status), and the end-to-end receiver gate over the published tree.
- HSM / offline root key handling (Phase 3); a pluggable `KeyStore` backend.
  The **online-key** half is in place: the `signer` seam plus the `KmsSigner`
  backend (X.509, CMS and OCSP all sign through it), so an online key can be a
  non-exportable KMS key. Offline-root custody is still out of scope here.
- ROUTE SLS fragment signing (A/360 5.2.2.4) and MMTP `signed_mmt_message()`
  (5.2.2.5) beyond the SLT/SLT-table path already covered.
- Read-only DRF API (v1 is admin-only).
