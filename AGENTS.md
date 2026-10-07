# AGENTS.md

Guidance for opencode when working in this repository (ATSC 3.0 receiver).

## Project context

- **Summary and reference docs live in `meta/`** (`/home/ajonen/atsc3/meta/`):
  `SUMMARY.md` (current status and remaining work) and
  `OPENATSC3_PROJECT_PLAN.md` (the original phased plan).  Read them there
  rather than editing status into this file.  `AGENTS.md` is symlinked at the
  workspace root (`AGENTS.md -> ./meta/AGENTS.md`).  The living, frequently
  updated record is the wiki (`./atsc3/wiki/`), especially
  `wiki/log.md` and `wiki/index.md`; the wiki is the source of truth when
  `SUMMARY.md` drifts.  `meta/TODO-ldpc-demod-enhancements.md` holds the
  queued LDPC fast-path and demapper/pool speed-up work (bit-identical, plus
  the separate sub-second-live follow-on); `meta/HARDWARE-live-rig.md` records
  the selected single-box live target (8-core CPU + modern NVIDIA GPU,
  RTX 5070 class; bandwidth-bound, VRAM not the constraint).

## Environment

- Work in `./atsc3/atsc3lib` (the git repo).
- Tests: `make test` (or `source venv/bin/activate && python -m pytest -q`)
- No linter/typechecker is configured; `py_compile` is the only check.
- **Scratch and temporary files go in `./out/`, not `/tmp`.** `out/` is
  gitignored; use `out/` subfolders for ad-hoc scripts, captures and
  experiments so they stay with the project.
- **Run pytest from a fresh folder, not the repo root.** `python -m pytest`
  puts the cwd on `sys.path[0]`, and the repo's outer `ac4bindings/` directory
  (no `__init__.py`) then shadows the installed editable `ac4bindings`
  package as a namespace package (`ac4bindings.__file__ is None`), so the
  AC-4 tests fail on `from . import synthesise`. The `pytest` console script
  and `make test` are not affected; for a raw module invocation use
  `cd out/scratch && <venv>/bin/python -m pytest <repo>/atsc3lib/tests -q`.
## Scope

- **All processing must be bounded. This runs on live air feeds, so no code
  path may scan or buffer without a limit.** Every search, correlation,
  reassembly and iteration must have an explicit, spec-derived bound; when the
  bound is reached the receiver stops and reports a miss (e.g. "advance and
  retry the next window"), it does not fall back to an unbounded scan. A
  caller may opt into a wider bound (offline captures, `full_search=True`) but
  the bound must be finite and must not be the default. See
  `_detect_bootstrap_bounded` for the pattern. Reassembly buffers are bounded
  by the object length signalled in the FDT/EXT_TOL, never by "keep reading".
- **Every rung must be validated on air. If no receivable stream carries a
  feature, do not implement it.** A structural/round-trip gate is not
  sufficient evidence for a rung; a feature whose only evidence would be
  synthetic is not worth writing, because it can never be proven to work. When
  a feature is blocked by the link, record that and move on — do not build it.
- **RF33 (587 MHz, BSID 540) is the DC lighthouse multiplex and it decodes off
  air.**  With the SDRplay RSP1B front end (replacing the HackRF), PLP-0
  (64QAM-NUC 11/15) now converges **74/74** (was 53-60/74 on HackRF) and
  yields the A/331 SLT; PLP-16 (QPSK 2/15) decodes byte-identical to the
  reference.  An earlier "64QAM unachievable, 2.8 dB short" verdict was
  **wrong** — the cause was a demapper scale bug (unit-power NUC alphabets vs
  non-unit equaliser output), now fixed in `DataPlpChain.decode_cells`.
  **Do not re-add blanket "link limits" scope decisions without measuring on a
  clean capture first.**

## Second track: own PKI / content protection

- **`openatsc3-pki` is a separate greenfield product**, not an `atsc3lib`
  rung: a certificate authority plus its own content-protection scheme.  It
  **competes with A3SA** and runs **alongside** it as a parallel trust anchor.
  The standards define the cert formats and validation rules (A/331
  §5.9/§6.7 LLS SignedMultiTable `0x07`; A/360 §5.2.2 mandatory
  CertificationData `0x06` with chain + stapled OCSP; ECDSA P-256/P-384, ATSC
  OIDs `1.3.6.1.4.1.51552`, EKU `id-atsc-kp-signalingSigning`) but **not the
  operator** — that open field is the value.  **No Widevine, no A3SA**: own
  DRM scheme and UUID (ISO/IEC 23001-7 CENC compatible).  The receiver trusts
  our root only (fail-closed).
- **This track is exempt from the on-air ground rule**: no receivable stream
  carries our signature or DRM, so it is gated **synthetically at the A/331
  layer** — generated CDT/SMT/signed-SLS through `ip.parse_lls` →
  `slt_from_lls` → `mmtp`/`route`/`media` — with negative gates (tamper,
  expiry, revocation, wrong-root, missing EKU).  Recorded in
  `wiki/analyses/own-ca-and-content-protection.md` and `meta/SUMMARY.md`.
- **Built:** `openatsc3-pki/` (root/issuing CA, the A/360 5.3.1.6 signer
  profile, OCSP + stapled responses, detached CMS `SignedData`, CDT `0x06`,
  SignedMultiTable `0x07`, A/360 5.2.2.6 verifier, own CENC in `content.py`);
  `atsc3lib/security/` is a **pluggable provider seam** — `base.SecurityProvider`
  protocol + registry, with `openatsc3.py` the default provider — so the CA/DRM
  backend can be swapped for A3SA/Widevine without touching the receiver.  It
  verifies the LLS security tables a PLP delivers (verdict on
  `DecodedStreams.verification`; `atsc3-decode --trust-root
  --security-provider --require-signature`).  `openatsc3_pki` is an **optional**
  atsc3lib extra (`pip install -e .[pki]`), not a hard dependency; without it
  the security gate reports "unavailable" and the receiver still runs.
  `id-atsc = 1.3.6.1.4.1.51552`.
- **`openatsc3-ca` is the operator application**, split out of `openatsc3-pki`
  so the crypto library stays dependency-light (only `cryptography` +
  `asn1crypto`): a Django + PostgreSQL system-of-record (the `openatsc3_ca`
  project, the `openatsc3_ca.catalog` app with its label pinned to `catalog` so
  migration history is preserved).  PostgreSQL is authoritative, keys stay on
  disk, and it depends on `openatsc3-pki`.  Its test extra (`pgserver`, Python
  3.12 only) runs rootless; `make test-ca`.
- Open gaps **A–F** (register in `wiki/analyses/own-ca-and-content-protection.md`):
  A = CA operation lifecycle (persistent revocation, real `revoke`, rollover/
  renewal) — closes with the **Django + PostgreSQL CA app in `openatsc3-ca`**
  (DB authoritative, keys on disk); B = ROUTE/MMTP SLS signing; C = receiver
  content-protection parse/decrypt; D = HSM/offline root; E = receiver seam;
  F = docs drift.

## Synthetic transmission (TX)

- **The transmitter is the inverse of the validated receiver, in-repo.**  Reuse
  every table and primitive (`bootstrap.generate_bootstrap`, LDPC/BCH `encode`,
  the interleavers, `nuc.points`, the pilot reference); do not re-derive
  constants or copy values.  The middle layer `atsc3lib/ofdm.py` is the single
  façade over the A/322 tables (`spec`, `pilot_tables`, `preamble`,
  `pilot_reference`, `payload._pilots`) that both RX geometry and TX symbol
  assembly consume, so they cannot drift.
- **`drmpeg/gr-atsc3` (`000b86a3`) is the optional external referee, not a
  dependency.**  It is a GPL-3 GNU Radio C++ OOT module (not pip-installable,
  needs GNU Radio 3.10); it is already pinned as a table witness.  Use it to
  generate an independent capture for `atsc3-decode`, never import it.
- **First rung geometry:** 8K / GI1536 / SP4_2 / QPSK 2/15 / short frame (the
  RF33 PLP-16 shape).  Gate a TX rung by RX loopback
  (`decode_signaling` -> `decode_plp_streams`, plus the security verify for the
  certificate hook) under AWGN and a static multipath channel.  The loopback is
  a real gate but is still *synthetic content*; it does not air-prove our
  signature.  See `wiki/analyses/transmitter.md`.

## Code style

- **No magic numbers.** Every constant must be defined once, named, and
  derived from the spec, a table, or a fixture. Do not inline unexplained
  literals in logic. If a value is signalled or tabulated, reference the
  module-level table (e.g. `spec.GI_SAMPLES`) rather than hard-coding.
- **Use dataclasses instead of dicts** for structured records and
  configuration. Prefer `@dataclass` (frozen where possible) over bare
  `dict`/`tuple` returns for anything with named fields. Include a
  docstring naming the spec source (section/table) for each field.
- **No comments** unless asked.
- **Cite the spec.** Modules and non-obvious functions carry a docstring
  with the A/322 (or A/327) section and a note on what test vector or
  capture gates the logic. Reference the oracle (`/tmp/opencode/felbs-ref`)
  as referee only — do not copy code from it.
- Follow existing patterns: numpy vectorisation, `numpy` dtypes explicit,
  module-level lookup tables in `spec.py`/`*_signaling.py`.

## Testing

- Add a test for every new table/permutation/algorithm.
- Gate on the standard's own printed test vectors where available
  (e.g. A/327 Fig 6.5, A/322 7.1.5.2 shift vector) and on real-air
  fixtures in `tests/data/`.
- Cross-check against the oracle when a live path is involved.
