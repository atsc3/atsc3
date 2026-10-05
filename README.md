# ATSC 3.0 Receiver

Monorepo for an ATSC 3.0 software receiver: the `atsc3lib` demodulation and
service-delivery library, the compiled C-extension kernels it depends on, and
the `openatsc3-pki` certificate-authority / content-protection toolkit.

The receiver is validated against real off-air captures (RF33 / 587 MHz,
WHUT mux, SDRplay RSP1B) and an independent receiver.

## Packages

| Package | Kind | Purpose |
|---|---|---|
| [`atsc3lib/`](atsc3lib/) | Python | Physical-layer demodulation, L1 signalling, data-PLP decoding, and A/331 service discovery / ROUTE / MMTP / media |
| [`sdrbindings/`](sdrbindings/) | C extension | SDRplay capture over SoapySDR |
| [`fecbindings/`](fecbindings/) | C extension | Normalized-min-sum LDPC and BCH decoders |
| [`demodbindings/`](demodbindings/) | C extension | Max-log soft demapper |
| [`ofdmbindings/`](ofdmbindings/) | C extension | Frequency-interleaver address generator (A/322 7.3) |
| [`ac4bindings/`](ac4bindings/) | C extension | AC-4 (ETSI TS 103 190) decoder kernels |
| [`openatsc3-pki/`](openatsc3-pki/) | Python (optional) | Own CA, signed-signaling verification, and CENC content protection (A/360) |

The compiled bindings are **required dependencies** of `atsc3lib`, not
optional accelerators. The NumPy equivalents are retained only as the
references the C kernels are differentially tested against.

Documentation and status live in [`meta/`](meta/) (`SUMMARY.md`, the project
plan, and `AGENTS.md`).

## Install

The workspace is managed with [uv](https://docs.astral.sh/uv/). One command
creates a single `.venv` with every package installed editable:

```bash
make            # == uv sync
```

`openatsc3-pki` is an optional extra; the receiver runs without it.

## Build and test

```bash
make test         # atsc3lib test suite
make test-all     # every package's suite
make test-fec     # fecbindings (LDPC + BCH)
make test-demod   # demodbindings (max-log demapper)
make test-ofdm    # ofdmbindings (frequency interleaver)
make test-ac4     # ac4bindings (AC-4)
make test-sdr     # sdrbindings (SoapySDR)
make test-pki     # openatsc3-pki
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
