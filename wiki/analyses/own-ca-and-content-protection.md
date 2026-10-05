---
created: 2026-10-04
updated: 2026-10-04
sources: [ATSC A/331, ATSC A/360, published ATSC CDT example, A3SA signing guidance]
tags: [pki, certificate-authority, security, a360, a331, cms, cenc, content-protection, greenfield]
---

# Own CA and Content Protection (greenfield, alongside A3SA)

## Position

The certificate/security work is a **separate product**, not an `atsc3lib`
receiver rung.  It is **greenfield** and it **competes with A3SA** (the ATSC 3.0
Security Authority).  Nothing in A/331 or A/360 says who must build or own the
crypto framework — the standards define the **formats and validation rules**,
not the operator.  That is the open field: the value is in correctly
generating and validating the certificates, not in owning a monopoly.

This CA runs **alongside** A3SA: it is a parallel trust anchor, not a
replacement.  The receiver is configured to trust **our root only**
(fail-closed).  A3SA-signed content stays outside our trust by design.

**We do not use Widevine.**  We do not use A3SA.  We define and operate our own
DRM scheme and our own certificate authority.  The protected RF33 services
currently advertise Widevine (`urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed`)
and are irrelevant to this work.

## What the standards actually require (the part we must follow)

This is the important part: the certs and their validation are specified even
though the operator is not.  All of the following was verified directly from
A/360:2026-08 text and the published ATSC schema example.

- **LLS signing (A/331 §5.9, §6.7).**  LLS tables are signed with an LLS
  **SignedMultiTable** (`LLS_table_id` 0x07): a CMS `SignedData` computed over
  the bytes from `LLS_payload_count` up to but not including
  `signature_length`.  The SignedMultiTable itself is **not** gzip-compressed;
  each contained table may be.  Validator: `LLS_payload_id` 0x00/0xFE reserved.
- **CertificationData (A/360 §5.2.2.2).**  Mandatory once signaling is signed.
  LLS `0x06`, **gzipped XML**, self-signed (not inside the SignedMultiTable).
  Its `ToBeSignedData` (signed by a key *distinct* from CurrentCert/NextCert)
  carries the CA chain + end-entity certs and a **stapled OCSP response for
  every certificate**, so a receiver validates offline and bounded.  The
  signature covers the `ToBeSignedData` element including its tags.
- **ROUTE SLS** signed per A/360 §5.2.2.4; **MMTP SLS** per §5.2.2.5.
- **Certificate profile (A/360 §5.2-5.3).**  RFC 5280 base; ECDSA
  P-256/P-384/P-521 (or RSA >= 2048); ATSC OIDs under `id-atsc =
  1.3.6.1.4.1.51552`; for a broadcast signaling signer: KeyUsage critical
  `digitalSignature` **only**, EKU critical `id-atsc-kp-signalingSigning`
  (`…51552.37.3`), and a Subject Directory Attribute `id-atsc-sdattr-bsid`
  (`…51552.9.1`) whose values are a **SET OF INTEGER** bsids.  The published
  ATSC example CDT is the oracle: RC1 root anchor + RCA1/OCSP intermediates +
  LC3/LC4 broadcaster leaves + OCSP responder cert + CRL.  Signatures use the
  permitted curve/digest pairs (P-256/SHA-256, P-384/SHA-384, P-521/SHA-512).
- **Verification rules (A/360 §5.2.2.6).**  For the CDT: chain to a trusted
  root, all certs in date, CMSSignedData valid and authenticated by a carried
  cert, the CDT key differs from the signaling keys, and a matching authentic
  “good” OCSP per cert within producedAt±1 h / nextUpdate / +10 days /
  +OCSPRefresh.  For a message: signature valid; SigningTime not future and not
  backward; signer EKU present; SDA bsid set equals the SLT’s; SKI matches
  CurrentCert/NextCert; the rollover window is respected; OCSP good and fresh.
- **Content protection (A/331 §7.5, Table 7.32; ISO/IEC 23001-7).**  Encrypted
  per-asset signalling carries a `security_properties_descriptor` with
  `scheme_code` (default `cenc`) and `default_KID` (16 bytes).  The DASH MPD
  `ContentProtection@schemeIdUri` names the DRM system UUID.  **We define our
  own scheme and UUID; no Widevine.**

## The plan

### Deliverable 1 — `openatsc3-pki` (new repo, greenfield)
No dependency on `atsc3lib`.  Python + `cryptography` (X.509, AES) +
`asn1crypto` (CMS).  Phases:

1. **Root + issuance.** ECDSA keygen; self-signed root, issuing CA, and
   per-broadcaster **CDT signer** and **SMT/SLS signer** leaves (distinct
   keys).  ATSC-profiled extensions (EKU/SDA/OIDs/keyUsage/validity).  A
   private parallel profile is acceptable, but mimic A/360's shape so the code
   stays standards-shaped and can interoperate later.
2. **Revocation.** Signed OCSP responses + an issued/revoked status store.
3. **Signers.** CMS `SignedData` for LLS SignedMultiTable (0x07),
   CertificationData (0x06, with stapled OCSP), signed ROUTE SLS, signed MMTP
   SLS.
4. **Own content protection.** AES-128 CTR/CBC sample encryption, `sinf`/
   `tenc`/`senc`, `pssh` with our own DRM UUID, MPD `ContentProtection`, and
   the A/331 security descriptor — our scheme, not Widevine.

**Status (2026-10-04):** steps 1-4 built and tested.  `openatsc3-pki/` at the
workspace root implements `keys`, `sda`, `x509`, `ca`, `ocsp`, `cms`, `cdt`,
`verify`, `content` and a `openatsc3-pki` CLI (init-root / issue-ca /
issue-broadcaster / show).  44 tests pass, including every A/360 5.2.2.6
negative gate (wrong root, expiry, tamper, CDT-key-equals-CurrentCert,
revocation, stale OCSP, wrong bsid, future/backward SigningTime, missing EKU)
and the CENC content-protection round trip.  The published ATSC CDT example
(`tests/data/cdt_example.xml`) is used as an external oracle and pins the SDA
encoding (bsid 33) and the real profile.

**Deliverable 2 (receiver gate) is also in place:** `atsc3lib/security/`
verifies the LLS security tables a PLP delivers, attaches the verdict to
`DecodedStreams.verification`, and the `atsc3-decode` CLI gained
`--trust-root` / `--security-provider` / `--require-signature`.
`atsc3lib/tests/test_security.py` gates the whole thing synthetically at the
A/331 layer (real `ip.parse_lls` -> `slt_from_lls` ->
`security.verify_streams`), with wrong-root and tamper negative gates.

**The receiver is provider-agnostic.**  `atsc3lib/security/` is a seam:
`base.py` defines the `SecurityProvider` protocol, the `SecurityReport` /
`TableVerdict` result types, a name registry and `ATSC3LIB_SECURITY_PROVIDER`
selection; `openatsc3.py` is the built-in provider wrapping `openatsc3_pki`.
A3SA / Widevine / any other CA is a drop-in provider — the receiver never
imports a vendor package directly, and the `openatsc3_pki` extra is optional.
Gated by `tests/test_security_provider.py` (register/select, environment
selection, stub provider).

**Deliverable 3 (content protection) is in `content.py`:** AES-128 CENC CTR
sample encrypt/decrypt, the ``pssh``/``tenc``/``senc``/``schm`` boxes, the DASH
``ContentProtection`` element with our own DRM UUID
(`6f70656e-6174-7363-3300-000000000001`), and the A/331 Table 7.32
``security_properties_descriptor``.  **No Widevine.**

### Deliverable 2 — receiver verification gate (`atsc3lib`)
`security.py` parses CDT, builds the chain to the configured trust store using
**stapled OCSP only**, verifies CMS signatures on SMT/SLS, enforces EKU/SDA/
validity.  Wired beside `slt` on `DecodedStreams`; `--require-signature` CLI.
Content: parse `ContentProtection`/`pssh`/`tenc` and the security descriptor,
and decrypt our own scheme.

### Deliverable 3 — synthetic generator + gates
RF33 is unsigned and Widevine-locked, so validation is **A/331-layer
injection**: feed generated CDT + SMT + signed SLS through `ip.parse_lls` →
`slt_from_lls` → `mmtp`/`route`/`media`, with no PHY modulation.  Negative
gates: tamper, expiry, revocation, wrong-root, missing EKU.  Content gate:
encrypt → signal → decrypt round-trip.

## Ground rule note

This rung is **explicitly exempt from the on-air ground rule** — no receivable
stream carries our own signature or our own DRM, so it can never be
air-proven.  It is gated synthetically at the A/331 layer by construction.
This is the one deliberate exception, recorded so it is not mistaken for a
violation.

## See Also

- [[atsc3-certificate-validation]] — the earlier concept page (pre-greenfield).
- [[project-plan-status]] — three-phase plan status.
- [[overview]].
