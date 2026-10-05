# AGENTS.md

Guidance for opencode when working in this repository (ATSC 3.0 receiver).

## Project context

- **Summary and reference docs live in `meta/`** (`/home/ajonen/atsc3/meta/`):
  `SUMMARY.md` (current status and remaining work) and
  `OPENATSC3_PROJECT_PLAN.md` (the original phased plan).  Read them there
  rather than editing status into this file.  `AGENTS.md` is symlinked at the
  workspace root (`AGENTS.md -> ./meta/AGENTS.md`).  The living, frequently
  updated record is the wiki (`/home/ajonen/atsc3/wiki/`), especially
  `wiki/log.md` and `wiki/index.md`; the wiki is the source of truth when
  `SUMMARY.md` drifts.

## Environment

- Work in `/home/ajonen/atsc3/atsc3lib` (the git repo).
- Tests: `make test` (or `source venv/bin/activate && python -m pytest -q`)
- No linter/typechecker is configured; `py_compile` is the only check.
- The SDRplay capture path is `sdrbindings`, a separate CPython extension
  around the SoapySDR C API in `/home/ajonen/atsc3/sdrbindings` (not the git
  repo).  It is a declared dependency of atsc3lib, installed once with
  `make -C /home/ajonen/atsc3/sdrbindings install`; its tests live in
  `sdrbindings/tests/` (`make -C /home/ajonen/atsc3/sdrbindings test`).  The
  old standalone `tools/soapy_capture` helper and its auto-build path were
  removed.  `sdrbindings` stays a separate distribution because it links the
  external SoapySDR C library and is not ATSC-specific.
- The ATSC kernels are compiled into **atsc3lib itself**, as the private
  `atsc3lib/atsc3lib/_bindings/` subpackage: `_ldpc.c` + `_bch.c` (normalized
  min-sum LDPC and the BCH decoder), `_demap.c` (max-log NUC/QAM demapper) and
  `_fi.c` (frequency-interleaver address generator, A/322 7.3).  They were
  formerly the separate `fecbindings`, `demodbindings` and `ofdmbindings`
  distributions; `atsc3lib/setup.py` builds all four extensions
  (`atsc3lib._bindings._ldpc` etc.), so `pip install -e .` / `uv sync` compiles
  them with the package.  The thin facades `_bindings/fec.py`, `demod.py` and
  `ofdm.py` keep the old module API.  **They are required dependencies, not
  optional accelerators:** `ldpc_exact` and `bch` use `_bindings.fec`, `nuc`
  uses `_bindings.demod`, and `frequency_interleaver` uses `_bindings.ofdm`
  unconditionally; the NumPy implementations are retained only as the
  references the C kernels are differentially tested against.  The binding
  suites live in `atsc3lib/tests/bindings/`.  **C code must carry its own unit
  tests** (API contract, edge cases, input validation) *and* differential tests
  against the Python reference — never differential tests alone, which become
  tautological the moment the Python path delegates to C.
- AC-4 decoding is `ac4bindings`, a separate distribution
  (`/home/ajonen/atsc3/ac4bindings`) because it is a reusable standalone
  implementation (no working open-source AC-4 decoder exists elsewhere);
  `atsc3lib/audio.py` imports it lazily.
- **Acceleration is algorithmic before it is a binding.**  The bootstrap
  acquisition was ~5 s because `_fft_correlate_abs` took one FFT of length
  `n + m - 1` (2**20 for a 100 ms window) to correlate a 3072-tap symbol; the
  fix is overlap-save block convolution at 8192 (exact to ~1e-9, ~4x faster) —
  not a C kernel, since the FFT is already compiled.  Reserve new compiled
  bindings for genuinely dense numerical loops the NumPy path is slow at
  (LDPC min-sum, the high-order demapper).

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
- **Still open — RF33 subframe-1 PLP-1 (256QAM-NUC 11/15)** — was 0/117 on the
  HackRF.  The earlier "21.2 dB MER, above the synthetic 20 dB threshold,
  therefore a chain defect" reasoning was a **units error**, now retracted.
  Nearest-point MER is **not** Es/N0: for 256QAM 11/15 it overstates true SNR
  by ~2.5-4 dB.  Every table is exonerated: the pool closes at 956179 exactly
  like the reference's corrected model; 16K FI, the HTI read order, the
  group/block interleaver tables and the pilot sets match the oracle.
- **PLP-1 now decodes 117/117 off air (2026-10-01).**  Two changes together
  closed it: (1) the SDRplay RSP1B front end (replacing the HackRF) reads the
  subframe-1 cells at **25.8 dB nearest-point MER** (the old fixture was
  20.6 dB); and (2) the data-PLP channel estimator now merges the ``DY``
  scattered-pilot subsets of ``DY`` consecutive symbols into a ``DX``-spaced
  grid (`dense_symbol_channel`, `payload.py`), which resolves the
  frequency-selective (multipath) residual the single-symbol linear
  interpolator left behind.  The measured channel ripple was |H| std 0.19
  (norm 0.85-2.64); under pure AWGN the old estimator decoded 39/39 at 22.9 dB
  MER but a synthetic static channel with the 0.19 ripple read 23.0 dB MER and
  decoded 0/39 — nearest-point MER under-reports multipath residual for 256QAM.
  The single-symbol interpolator under-resolves it; the dense estimator fixes
  it.  **The 256QAM limiter was equaliser residual (multipath), not raw SNR.**
  `decode_subframe_plp` defaults ``dense=True``; `build_data_symbol_pool`
  carries the ``dense`` flag.  Gated by `tests/test_subframe1.py::TestDenseChannel`
  (synthetic frequency-selective channel, corr > 0.999) and on air (117/117,
  358 UDP datagrams from PLP-1).
- **The old subframe-1 fixture was 9.3 dB nearest-point MER** — unusable for
  256QAM; this is why earlier "both decoders fail" tests proved nothing.  It
  was replaced 2026-09-27 from a fresh good-antenna capture at 20.6 dB, then
  **replaced again 2026-10-05**: that 20.6 dB recording measures 21.5 dB and
  decodes **0/117** (~1.5 dB short of the cliff), but its tests gated geometry
  only, so the shortfall was silent.  The new `tests/data/rf33_sf1_y.npy` comes
  from `out/recapture/rf33_rsp1b_05.iq` (SDRplay RSP1B, 587 MHz, IFGR 40 /
  RFGR 4, 8 s), reads **23.0 dB**, and decodes **117/117**;
  `tests/test_subframe1.py::TestSf1Air::test_plp1_decodes` now asserts it, so a
  too-weak fixture fails loudly.
- LDM/CTI (RF30/RF25) remain implemented-and-gated but not air-demonstrated;
  RF25's 256QAM enhanced layer and RF30's core are capture-limited.
- **A/331 service discovery now decodes off RF33.**  The gzip-compressed SLT
  (LLS_table_id 0x01) inflates and parses (`slt.py`), exposing 10 services on
  BSID 540 with their ROUTE/MMTP SLS bootstrap addresses (A/331 6.3, Table
  6.6: 1=ROUTE, 2=MMTP).  `decode_streams` attaches the parsed SLT to
  `DecodedStreams.slt`.  Gated on `tests/data/rf33_slt_lls.bin` and
  `tests/test_slt.py`.
- **ROUTE/ALC delivery parses off air** (`route.py`): RFC 5651 LCT header
  (V=1, C=0, PSI=0b10 source, S=1, O=1, 32-bit TSI/TOI) plus the 32-bit
  ``start_offset`` FEC Payload ID (A/331 A.3.5.1), with object reassembly by
  offset.  The off-air EFDT (A/331 Extended FDT, `parse_efdt`) is recovered
  from the GMLOOP service LCT channel (TSI=0, TOI=0,
  `tests/data/route_efdt_gmloop.bin`) and the SG-FE00 objects
  (`route_object_sgfe00_*.bin`, `route_sls_sgfe00_cp3.bin`); gated by
  `tests/test_route.py`.
- **MMTP SLS now decodes off RF33** (`mmtp.py`).  WJLA (serviceId 2,
  ``slsProtocol=2``) is the MMTP service: its MMTP packets to 239.255.7.1:8071
  parse per ISO/IEC 23008-1 (version 1, 12-byte base header, ``payload_type``
  0 = MPU media, 2 = signaling).  A `payload_type=2` packet carries a 4-byte
  signaling payload header then a signaling message; ``packet_id=0`` carries
  ``message_id`` 0x8100 ``mmt_atsc3_message()`` (A/331 7.2.3.1): a gzip
  `UserServiceDescription` (`mmtPackageId="TRI-PID-2"`, name WJLA) and HELD.
  `packet_id=0x20` carries the MP Table (MPT, package ``TRI-PID-2``, 4
  assets).  Gated by `tests/test_mmtp.py` on the off-air fixtures
  `mmtp_media_*.bin`, `mmtp_sls_usbd.bin`, `mmtp_sls_held.bin`, `mmtp_mpt.bin`.
  (The MPT per-asset rows need ISO/IEC 23008-1 10.3.4 and are not decoded; the
  ATSC asset list comes from the USBD.)
- **Media reassembles and builds to a playable MP4 off RF33** (`media.py`,
  `mp4.py`).  A whole-frame drain of the saved
  `out/rf33_sdrplay_if45_cs16.iq` writes fragmented-MP4 files, one per track;
  the ROUTE video lanes decode to **1920x1080 HEVC** (239.255.32.1:8321 120
  samples -> 117 frames, 239.255.4.1:8041, 239.255.5.1:8051, 239.255.9.1:8091),
  and AC-4 (`ac-4` in `stsd`) / `stpp` audio+subtitle tracks reassemble.  The
  key fact a player needs: every MPU is a *self-contained* ISO-BMFF file whose
  `mfhd` sequence and `tfdt` decode time restart, so `mp4.retime` puts them on
  one timeline, advancing by the **media** `traf`'s duration, not the hint's
  (the hint rounds 60000/1001 and drifts A/V 2.3 s per two hours).  Segments
  are appended only when `mdat_got == mdat_declared` (the transmitter's own
  header) or trimmed to leading whole samples and labelled; ROUTE partials
  trim at the first `start_offset` gap.  `tests/test_mp4.py` (arithmetic + an
  RF33 picture gate) and `tests/test_mmtp_media.py` (reference byte-identity)
  gate it.  CLI `atsc3-media` defaults to a bounded **4-frame** drain of every
  layer-0 PLP (~35 s on the 3 s capture; `--frames 0` for the whole capture).
  `mux.py` then combines the longest video track and the decoded AC-4 PCM into
  one **fragmented A/V MP4** (HEVC stream-copied, AC-4 PCM -> AAC-LC), written
  by default beside the per-track files and WAVs (`--no-mux` opts out); the
  live loop writes the same via `live.LiveMediaSink` (`atsc3-live --outdir`).
  `tests/test_mux.py` gates the combined file (HEVC+AAC, fragmented, non-silent).
- **Bootstrap acquisition is search-bounded by default.**  `detect_bootstrap`
  FFT-correlates every candidate offset; scanning a whole multi-second capture
  over 16 hypotheses took ~45 s/s of IQ.  `_detect_bootstrap_bounded` searches
  only two A/322 7.2.2.2 minimum frame periods (100 ms) — the wire behaviour,
  where a miss means "advance and retry the next window".  Offline captures of
  long-frame emitters pass `full_search=True` to retry the whole buffer.  On
  RF33 (52.7 ms frames) this cut `decode_signaling` on a 3 s capture from 188 s
  to ~14 s with identical L1 results.  Gated by
  `tests/test_bootstrap.py::TestBoundedAcquisition`.
- **The live receive loop is window-bounded** (`live.py`).  `LiveReceiver`
  consumes finite windows from an `IqSource` (live SDRplay via `sdrbindings`,
  or file replay); `run()` tries at most `max_windows` acquisition windows and
  `run_once()` advances exactly one — a miss advances, it never scans.  L1
  config is held across frames (`decode_plp_frame` decodes a frame from
  pre-acquired main samples), so a lock is reused.  `LiveConfig` rejects
  non-positive bounds.  Gated by `tests/test_live.py` on the real-air slice.
  **It does not yet keep up with the air rate** — the pure-Python decoders are
  far slower than 52.7 ms/frame; acceleration of the bootstrap correlation and
  LDPC/BCH is the next rung.

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
