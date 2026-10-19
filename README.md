# ATSC 3.0 Receiver

Monorepo for an ATSC 3.0 software receiver: the `atsc3lib` demodulation and
service-delivery library, the compiled C-extension kernels it depends on, an
optional CUDA acceleration package for live throughput, and the
`openatsc3-pki` certificate-authority / content-protection toolkit.

The receiver is validated against real off-air captures (RF33 / 587 MHz,
WHUT mux, SDRplay RSP1B) and an independent receiver.  The accelerated live
path meets the 247.1 ms frame period in the stage benchmark on a prototype
GPU; an on-air live run is the remaining proof.

## Packages

| Package | Kind | Purpose |
|---|---|---|
| [`atsc3lib/`](atsc3lib/) | Python + C extensions | Physical-layer demodulation and **synthetic transmission** (the inverse chain), L1 signalling, data-PLP decoding, and A/331 service discovery / ROUTE / MMTP / media. The dense kernels (normalized-min-sum LDPC and BCH, max-log demapper, frequency-interleaver address generator) are built in as `atsc3lib._bindings` |
| [`sdrbindings/`](sdrbindings/) | C extension | SDRplay capture over SoapySDR |
| [`rsp-open/`](rsp-open/) | C + Soapy module | Independent userspace RSP1B driver (libusb, no vendor API); SoapySDR driver `rsp-open` |
| [`ac4bindings/`](ac4bindings/) | C extension | AC-4 (ETSI TS 103 190) decoder kernels |
| [`gpubindings/`](gpubindings/) | CUDA (optional) | Optional CUDA demap + LDPC + BCH kernels for live throughput. The C kernels remain the required fallback |
| [`openatsc3-pki/`](openatsc3-pki/) | Python (optional) | Own CA, signed-signaling verification, and CENC content protection (A/360). Dependency-light: only `cryptography` + `asn1crypto` |
| [`openatsc3-ca/`](openatsc3-ca/) | Python (optional) | CA operator application: a Django + PostgreSQL ledger over `openatsc3-pki` (persistent revocation, rollover, admin UI) |

The C kernels in `atsc3lib._bindings` are **required dependencies**, not
optional accelerators. The NumPy equivalents are retained only as the
references the C kernels are differentially tested against. `sdrbindings`
links the external SoapySDR C library and `ac4bindings` is a reusable
standalone AC-4 implementation, so both remain separate distributions.
`gpubindings` is the one optional accelerator: it is a workspace member, but
installs without the CUDA toolkit (Python wrappers only, `available() ==
False`) so a CPU-only host still builds and runs.

## Live performance

The workload is memory-bandwidth-bound.  With the optional CUDA path, the RF33
subframe-1 PLP-1 frame (256QAM 11/15, 117 FEC blocks) measures **193-198
ms/frame sustained** against the 247.1 ms period on a prototype RTX 2080 Ti,
with pool build for frame N+1 overlapped against frame N's FEC.  The CPU-only
fallback is correct but not live (~0.6-0.8 s/frame on the same host).

```bash
make install-gpu     # requires nvcc; normal `make` stays CPU-only
.venv/bin/python -m tools.bench_live --gpu on --frames 32
atsc3-live --pipelined --fec-async --freq 587e6 --plp 1
```

Runtime selection is `ATSC3LIB_GPU=auto` (default), `on`, or `off`.  See
[`docs/live-streaming.rst`](docs/live-streaming.rst) for the host requirements
and the measured split.


## Documentation

The user documentation is built with Sphinx and published to GitHub Pages:
**https://atsc3.github.io/atsc3/**. It covers getting started, SDRplay setup,
live reception and the frame budget (including CPU/GPU host requirements and
the pipeline), and the Python API. Build
it locally with `make docs` (output in `docs/_build/html`).

Status and planning notes live in [`meta/`](meta/) (`SUMMARY.md`, the project
plan, `HARDWARE-live-rig.md`, and `AGENTS.md`); the frequently updated working
record is the local `wiki/` knowledge base.

## Install

The workspace is managed with [uv](https://docs.astral.sh/uv/). One command
creates a single `.venv` with the receiver stack installed editable:

```bash
make              # scoped: atsc3lib + sdrbindings + ac4bindings + openatsc3-pki
make install-gpu  # the same, plus the optional CUDA kernels (requires nvcc)
make install-all  # every package except gpubindings, incl. the openatsc3-ca app
```

Every package is a first-class workspace member, so the sync can be scoped to
one: `uv sync --package ac4bindings --group dev` (or `--package atsc3lib
--extra gpu`). The default `make` install deliberately leaves out
`openatsc3-ca` (Django + psycopg) and the GPU package; use `make install-all`
or the separate `openatsc3-ca/.venv-ca` for the CA app.

## Build and test

```bash
make test         # atsc3lib test suite (includes the compiled kernels)
make test-all     # every package's suite
make test-ac4     # ac4bindings (AC-4)
make test-sdr     # sdrbindings (SoapySDR)
make test-gpu     # gpubindings CUDA differential tests (skipped without a GPU)
make test-pki     # openatsc3-pki crypto (no Django/DB)
make test-ca      # openatsc3-ca Django + PostgreSQL app (Python 3.12)
make docs         # build the Sphinx documentation into docs/_build/html
make check        # py_compile every Python source (no linter is configured)
make clean        # remove build artefacts
```

See [`atsc3lib/README.md`](atsc3lib/README.md) for the receiver itself: the
decode chain, capture instructions, CLI usage, and the full command surface.

## Releases

GitHub releases are **tag-driven** by
[`.github/workflows/release.yml`](.github/workflows/release.yml). Pushing an
annotated `v*` tag runs the full CI suite, builds the release artifacts, and
creates a **draft** GitHub release:

- pure Python wheels: `openatsc3-pki`, `openatsc3-ca`
- manylinux x86_64 wheels (CPython 3.10–3.13): `atsc3lib`, `ac4bindings`,
  `sdrbindings` (SoapySDR is built in the manylinux container first)
- `rsp-open` source and Intel binary tarballs (it is a C/SoapySDR project, not
  a Python wheel; the Python Soapy wrapper is `sdrbindings`)
- optional CUDA wheels for `gpubindings` for `sm_75` and `sm_120`
- `SHA256SUMS` for the `rsp-open` tarballs

Versions for the Python packages are derived from the release tag by
`setuptools-scm`; the same `v*` tag stamps every Python package (lockstep).
No `[project].version` edits are needed. The tag must be annotated and point
at the release commit; the workflows check out full history for this.

The workflow reuses `ci.yml` through `workflow_call`, uses
`softprops/action-gh-release` with `draft: true`, and grants
`contents: write` to the release job. No local `gh` login is needed; Actions
uses the repository `GITHUB_TOKEN`.

To cut a release:

```bash
git switch main
git pull --ff-only

# Versions are derived from the tag by setuptools-scm; no manual bump.
# The tag must be annotated and point at the release commit.

git tag -a v0.1.0 -m "v0.1.0 Intel release"
git push origin v0.1.0
```

Then open the `Release` workflow run in GitHub Actions. When it succeeds, the
`v0.1.0` draft release is created with the artifacts above. Review the assets,
then publish it:

```bash
gh release edit v0.1.0 --draft=false
```

Do not move or recreate a published tag; use a new patch tag for a new build.

PyPI publishing is separate from the GitHub draft release. Upload the pure and
manylinux wheels plus the sdists with `uv publish` or `twine`, and keep the
CUDA `gpubindings` wheels as a separate optional release asset. Runtime notes
for the wheel set: `sdrbindings` links the external SoapySDR library;
`openatsc3-ca` expects `OPENATSC3_CA_DATABASE_URL` (its `pgserver` test extra is
Python 3.12-only); and `gpubindings` is the optional CUDA extra for `atsc3lib`.

## Repository layout notes

- `spec/` (licensed standards PDFs), `raw/`, the `wiki/` knowledge base and
  the `.opencode/` tooling are **gitignored and local-only**. Fetch spec
  material with `atsc3lib/tools/spec_sources.py`.
- `out/` holds large IQ captures and decoded media; it is gitignored.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
