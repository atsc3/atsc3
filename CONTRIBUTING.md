# Contributing

## Environment

One workspace, one environment:

```bash
make          # scoped sync: atsc3lib + sdrbindings + ac4bindings + openatsc3-pki
make install-all  # every package, including the openatsc3-ca Django app
make test-all # run every package's test suite
make docs     # build the Sphinx documentation into docs/_build/html
```

Every package is a first-class workspace member, so any one can be synced on
its own (`uv sync --package sdrbindings --group dev`). `openatsc3-pki` (crypto
library) and `openatsc3-ca` (its Django app) are optional; the receiver and the
compiled bindings install without them.

## Layout

Packages live at the repository root, flat: `atsc3lib/`, `sdrbindings/`,
`ac4bindings/`, `openatsc3-pki/`, `openatsc3-ca/`. The ATSC kernels (LDPC + BCH,
max-log demapper, frequency interleaver) are built inside `atsc3lib` under
`atsc3lib/atsc3lib/_bindings/`, not as separate packages. `openatsc3-pki` is the
dependency-light CA/crypto library; `openatsc3-ca` is the Django + PostgreSQL
operator app that depends on it. Documentation is in `docs/` (reStructuredText,
built with Sphinx; `make docs`); notes and status are in `meta/`.
`spec/`, `raw/`, `wiki/` and `.opencode/` are local-only and gitignored.

## Conventions

- **No magic numbers.** Every constant is named once and derived from the
  spec, a table, or a fixture; reference the module-level table rather than
  inlining a literal.
- **Cite the spec.** Modules and non-obvious functions carry a docstring with
  the relevant A/322 (or A/327/A/331/A/360) section and the test vector or
  capture that gates the logic.
- **Dataclasses over dicts** for structured records and configuration.
- **No comments** unless asked.
- **Every rung must be validated on air.** A feature that no receivable stream
  carries is not implemented; a structural round-trip gate is not sufficient
  evidence. Record the blocker and move on instead.
- **All processing must be bounded.** Live air feeds mean no unbounded scan,
  buffer, or iteration: every search has an explicit spec-derived bound, and
  reaching it stops and reports a miss. Offline callers may opt into a wider
  bound (`full_search=True`), but it must remain finite and non-default.
- **C code carries its own unit tests** (API contract, edge cases, input
  validation) *and* differential tests against the Python reference — never
  differential tests alone.

## Tests

Add a test for every new table, permutation, or algorithm. Gate on the
standard's own printed vectors where available and on real-air fixtures under
`tests/data/`. There is no linter or typechecker configured; `make check`
(`py_compile`) is the static check.

## Commits and PRs

- Run `make test-all` (or the affected `make test-<pkg>`) before opening a PR.
- Keep the PR template checklist complete, citing spec sections and the
  capture/fixture used as evidence.
