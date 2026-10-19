# AGENTS.md

Guidance for opencode when working in this repository (ATSC 3.0 receiver).

## Project context

- **Summary and reference docs live in `meta/`** (`./meta/`):
  `SUMMARY.md` (current status and remaining work) and
  `OPENATSC3_PROJECT_PLAN.md` (the original phased plan).  Read them there
  rather than editing status into this file. 
  The living, frequently
  updated record is the wiki (`./wiki/`), especially
  `wiki/log.md` and `wiki/index.md`; the wiki is the source of truth when
  `SUMMARY.md` drifts.  

## Environment

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
- **Never catch the base exception.** Do not write `except Exception:`,
  `except BaseException:`, or bare `except:`. Catch the specific exception
  type you mean (e.g. `FileNotFoundError`) or test the precondition (e.g.
  `os.path.exists`) instead. Swallowing every exception hides real corruption,
  permission and I/O failures behind a silent fallback; the only legitimate use
  of a broad handler is a documented top-level boundary that logs and re-raises.
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
