# ATSC 3.0 Receiver

Monorepo for an ATSC 3.0 software receiver: the `atsc3lib` demodulation and
service-delivery library, the compiled C-extension kernels it depends on, and
the `openatsc3-pki` certificate-authority / content-protection toolkit.

The receiver is validated against real off-air captures (RF33 / 587 MHz,
WHUT mux, SDRplay RSP1B) and an independent receiver.

## Packages

| Package | Kind | Purpose |
|---|---|---|
| [`atsc3lib/`](atsc3lib/) | Python + C extensions | Physical-layer demodulation, L1 signalling, data-PLP decoding, and A/331 service discovery / ROUTE / MMTP / media. The dense kernels (normalized-min-sum LDPC and BCH, max-log demapper, frequency-interleaver address generator) are built in as `atsc3lib._bindings` |
| [`sdrbindings/`](sdrbindings/) | C extension | SDRplay capture over SoapySDR |
| [`ac4bindings/`](ac4bindings/) | C extension | AC-4 (ETSI TS 103 190) decoder kernels |
| [`openatsc3-pki/`](openatsc3-pki/) | Python (optional) | Own CA, signed-signaling verification, and CENC content protection (A/360). Dependency-light: only `cryptography` + `asn1crypto` |
| [`openatsc3-ca/`](openatsc3-ca/) | Python (optional) | CA operator application: a Django + PostgreSQL ledger over `openatsc3-pki` (persistent revocation, rollover, admin UI) |

The compiled kernels are **required dependencies** of `atsc3lib`, not optional
accelerators. The NumPy equivalents are retained only as the references the C
kernels are differentially tested against. `sdrbindings` links the external
SoapySDR C library and `ac4bindings` is a reusable standalone AC-4
implementation, so both remain separate distributions.

Documentation and status live in [`meta/`](meta/) (`SUMMARY.md`, the project
plan, and `AGENTS.md`).

## Install

The workspace is managed with [uv](https://docs.astral.sh/uv/). One command
creates a single `.venv` with the receiver stack installed editable:

```bash
make              # scoped: atsc3lib + sdrbindings + ac4bindings + openatsc3-pki
make install-all  # every package, including the openatsc3-ca Django app
```

Every package is a first-class workspace member, so the sync can be scoped to
one: `uv sync --package ac4bindings --group dev`. The default `make` install
deliberately leaves out `openatsc3-ca` (Django + psycopg); use `make install-all`
or the separate `openatsc3-ca/.venv-ca` for the CA app.

## Build and test

```bash
make test         # atsc3lib test suite (includes the compiled kernels)
make test-all     # every package's suite
make test-ac4     # ac4bindings (AC-4)
make test-sdr     # sdrbindings (SoapySDR)
make test-pki     # openatsc3-pki crypto (no Django/DB)
make test-ca      # openatsc3-ca Django + PostgreSQL app (Python 3.12)
make check        # py_compile every Python source (no linter is configured)
make clean        # remove build artefacts
```

See [`atsc3lib/README.md`](atsc3lib/README.md) for the receiver itself: the
decode chain, capture instructions, CLI usage, and the full command surface.

## Repository layout notes

- `spec/` (licensed standards PDFs), `raw/`, the `wiki/` knowledge base and
  the `.opencode/` tooling are **gitignored and local-only**. Fetch spec
  material with `atsc3lib/tools/spec_sources.py`.
- `out/` holds large IQ captures and decoded media; it is gitignored.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
