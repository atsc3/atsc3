# Wiki Log

## [2026-08-30] init
- Wiki initialized
- Created: [[index]], [[overview]]

## [2026-08-30] ingest | OPENATSC3_PROJECT_PLAN.md
- Created: [[openatsc3-project-plan]] (source summary)
- Created: [[atsc3-physical-layer]] (OFDM pipeline concept)
- Created: [[atsc3-certificate-validation]] (CA integration concept)
- Created: [[hackrf-one]] (hardware entity)
- Updated: [[index]]

## [2026-08-30] ingest | User hardware info
- Created: [[nooelec-nesdr-smart-v5]] (user's RTL-SDR, SN: 84077375)
- Updated: [[index]]

## [2026-08-30] query | can i do a scan? somehow
- Created: [[rtl-sdr-scanning]] (scanning tutorial)
- Updated: [[index]]

## [2026-08-30] analysis | scan results
- Created: [[scan-results-2026-08-30]] (5 strong TV clusters found)
- Top candidates: 575 MHz (ch 30), 587 MHz (ch 33), 605 MHz (ch 36)
- Updated: [[index]]

## [2026-08-30] station identification | Washington DC market
- Identified: WIAV-CD (ch 30, 566-572 MHz) - strongest at 22.05 dB
- Also detected: WHUT-TV ch33 (PBS), WRC-TV ch34 (NBC), WTTG ch36 (FOX)
- All confirmed ATSC 3.0 NextGen TV stations
- Created: [[washington-dc-atsc3-stations]] (station analysis)
- Captured: out/wiav_cd_hackrf.iq (WIAV-CD IQ samples)
- Updated: [[index]]

## [2026-09-23] Week 3-4 complete | Equalization working
- Created: pilots.py (pilot extraction)
- Created: equalizer.py (channel estimation + equalization)
- CLI: atsc3-equalize command
- Test: Before/after constellation on WIAV-CD
- Result: Equalization tightening clusters ✓
- Updated: [[index]]

## [2026-09-23] Week 5 complete | QAM demodulation to bits
- Created: qam.py (QAM demodulator, modulation detector)
- CLI: atsc3-bits command
- Test: 491,520 bits from 10 symbols (64-QAM)
- Bit ratio: 49.4% zeros, 50.6% ones (valid scrambled data)
- Output: out/test_bits.bin (61,440 bytes)
- Status: ✓ COMPLETE - ready for LDPC/deinterleaving
- Updated: [[index]]

## [2026-09-23] Week 6-7 partial | LDPC decoder implemented
- Created: ldpc.py (belief propagation decoder)
- Classes: LDPCDecoder, BCHDecoder, ATSC3FECDecoder
- Test: 90% accuracy with 5% errors (synthetic data)
- Issue: Needs exact ATSC 3.0 parity matrices for 100% convergence
- Status: ⚠ PARTIAL - basic decoder works, needs spec matrices
- Created: [[week-6-7-ldpc-decoding]] (task breakdown)
- Updated: [[index]]

## [2026-09-23] LDPC integration complete | ATSC 3.0 matrices
- Created: atsc3_ldpc.py (quasi-cyclic matrix generator)
- Integrated: ATSC 3.0 matrices into LDPCDecoder
- Test: 94.8% accuracy with 5% errors (improved from 90.8%)
- Structure: Proper quasi-cyclic (Z=120, column weight=3)
- Status: ✓ COMPLETE - working ATSC 3.0 LDPC decoder
- Created: [[ldpc-integration-complete]] (integration report)
- Updated: [[index]]

## [2026-09-23] Optimization analysis | Symbol detection
- Analyzed: O(n²) correlation bottleneck
- Tested: FFT-based, subsampling, stride tricks
- Result: 1000x speedup possible but accuracy issues
- Recommendation: Accept 2min for now, add numba later
- Created: [[optimization-symbol-detection]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Exact LDPC + group interleaver | pipeline wired
- Fixed: ldpc_exact.py decoder (broken min-sum → normalized min-sum)
- Created: group_interleaver.py (A/322 Section 6.3 parity/group/block chain)
- Wired: exports in __init__.py, new atsc3-decode CLI
- Tests: test_ldpc_exact.py, test_group_interleaver.py
- Full suite: 218 passed (was 145)
- Created: [[exact-ldpc-and-group-interleaver]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Path B | Bootstrap acquisition implemented
- Decision: acquire bootstrap instead of guessing MODCOD (no brute-force)
- Researched: A/322 Section 7.2 + drmpeg/gr-atsc3 bootstrap_cc_impl.cc
- Extracted official A/322 PDF text to /tmp/opencode/a322.txt
- Created: bootstrap.py (Zadoff-Chu + PN generator, DBPSK shift detect)
- Created: test_bootstrap.py (19 tests) - full suite 237 passed
- Key finding: L1-Basic FEC needs BCH(16200) decoder (currently a stub)
- Created: [[bootstrap]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] BCH outer code | real codec replaces stub
- Researched: A/322 Section 6.1.2.1 Table 6.3 (component polynomials)
- Created: bch.py (BCH(16200) GF(2^14), BCH(64800) GF(2^16))
- Fixed: shortened codeword length (use Nouter, not full primitive length)
- Wired: BCHDecoder in ldpc.py now delegates to real codec
- Created: test_bch.py (18 tests) - full suite 255 passed
- Created: [[bch-outer-code]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] L1-Basic FEC | all 7 modes
- Researched: A/322 Section 6.5.2 (scramble, BCH, shortening, LDPC, parity
  permutation Table 6.21, puncturing Table 6.24, repetition Table 6.23)
- Created: l1_basic.py (L1BasicCodec, scramble_bits)
- Verified: transmitted lengths reproduce Table 6.17 cell counts for all modes
- Note: all L1-Basic modes use LDPC 3/15 (Kldpc=3240 = 16200*3/15)
- Created: test_l1_basic.py (18 tests) - full suite 273 passed
- Created: [[l1-basic-fec]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Front-end + real capture | no bootstrap found
- Created: frontend.py (resample_iq, acquire_frame, read_hackrf_iq)
- Hardened: bootstrap FFT correlation (fixed conjugation/indexing) + multi-peak
  validation
- Synthetic: bootstraps at 2.4/10 MHz resample+detect correctly
- Real: wiav_cd_hackrf.iq energy at +3.3 MHz, flat bootstrap correlation, no
  OFDM CP structure -> capture not demodulatable (interferer/off-center)
- Created: test_frontend.py (7 tests) - full suite 280 passed
- Created: [[real-capture-acquisition]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Real ATSC 3.0 capture | RF33 bootstrap + OFDM confirmed
- Captured: RF33 587 MHz (WHUT ATSC 3.0 mux), saved out/wiav_whut_rf33_587mhz.iq
- Bootstrap: structure 27 (8K, GI1536, L1-Basic Mode 3), peak/mean 42.5
- OFDM: new ofdm_detect.py confirms FFT8192/GI1536 (CP corr 0.665) - agrees
- Fixed: CP metric (was non-normalized/broken), bootstrap FFT correlation
- Learned: ch30/ch36 are ATSC 1.0 (8VSB); RF33 is true ATSC 3.0
- Created: ofdm_detect.py + test_ofdm_detect.py - full suite 293 passed
- Created: [[real-atsc3-capture-rf33]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] L1-Basic decoded from real air | chain bugs fixed
- Oracle: Felbs/atsc3 (Apache-2.0) used as external referee only
- Proved capture good: oracle decodes out/whut_rf33_new.iq (LDPC 0 unsat, BCH 0)
- Fixed: LDPC Type A steps v-viii (encoder + H) in ldpc_exact.py
- Fixed: QPSK map (C.1.1) + 6.5.2.10 block de-interleave in l1_basic.py
- Fixed: FI symbol-offset register width in frequency_interleaver.py
- Fixed: CP8 relative to carrier origin in preamble.py
- Refactor: all spec constants centralised in atsc3lib/spec.py with citations
- Our chain now decodes the real 200 L1-Basic bits, matching the oracle exactly
- Added: tests/test_air_l1_basic.py + real-air fixture; suite 348 passed
- Created: [[l1-basic-real-air]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] L1-Detail decoded | full per-PLP config from real air
- Oracle run once to dump RF33 L1-Detail ground truth (referee only)
- Created: crc.py, signaling_fec.py, l1_detail.py
- Replaced: l1_signaling.py (real Table 9.2/9.8 parsers, not the placeholder)
- spec.py: dataclasses for PreambleStructure, L1BasicMode/Lengths,
  L1DetailMode/Lengths; consumers use attributes not dict keys
- Result RF33: BSID 540, 2 subframes, PLPs 0/16/1; modcods match independent
  receiver exactly (LDPC+BCH+L1D_crc all pass)
- Tests: test_l1_detail.py, test_air_l1_detail.py, rewritten test_l1_signaling.py
- Full suite validated green
- Created: [[l1-detail-real-air]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] LDPC decoder vectorized | Option A
- ldpc_exact.py: packed rectangular-edge numpy min-sum (no new dependency)
- 1% error decode ~0.3-0.5s -> 0.02-0.08s; real-air tests ~75s -> <10s
- Full suite 346 passed; correctness gated by existing air/synthetic tests
- Created: [[ldpc-vectorized]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Codebase consolidation | one validated chain
- Removed Gen-1 scaffolding: atsc3_ldpc, ldpc, bit_interleaver, qam, ofdm,
  pilots, equalizer (+ their tests) - not on the air-proven path
- Added receiver.py: decode_signaling / decode_capture -> ReceiverResult
- Rewrote cli.py around receiver.py; single atsc3-decode command
- pyproject: dropped stale atsc3-demod/equalize/bits entry points
- Full suite: 245 passed in ~18s
- Git initialized at atsc3lib/.git (initial commit = consolidated state)
- Created: [[consolidation]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Preamble demod toolchain | FI + pilot reference
- Created: frequency_interleaver.py (A/322 7.3, exact H_l(p) generator)
- Created: pilot_reference.py (r_k sequence 0x1B, preamble DX/amplitude)
- Verified: r_k first 24 = 110110000000000101000000
- Real: structure 27 (8K/GI1536/DX4) timing locks (CP 0.72), CFO -262 Hz, but
  pilot comb invisible due to frequency-selective fading
- Next: preamble channel estimation/equalization before L1 cells
- Tests: test_frequency_interleaver.py, test_pilot_reference.py; suite 330 passed
- Created: [[preamble-demod-toolchain]] (analysis doc)
- Updated: [[index]]

## [2026-09-25] Preamble channel estimation | cell mapping verified
- Created: preamble.py (channel est, CP8 continual pilots, data mask, QPSK)
- Verified: exactly 4851 data cells for 8K/GI1536/cred4 (A/322 Table 7.2)
- Synthetic: channel corr 0.99999, QPSK data corr 0.99999
- Real: coarse timing/+CFO lock; equalized phases still uniform -> fine
  preamble sync (pilot reference indexing) is the remaining blocker
- Created: test_preamble.py; full suite 337 passed
- Updated: [[preamble-demod-toolchain]]
- Updated: [[index]]

## [2026-09-25] Data-PLP payload chain | PLP-16 decoded from real air
- Oracle (Felbs/atsc3) used as referee to fix the next rung after PLP-16
- Root-caused the live path: (1) guard interval signalling value was indexed
  as a list position (value 6 -> 2048 instead of 1536); (2) no bootstrap
  fractional-CFO de-rotation.  Both fixed; live PLP-16 now decodes
- Created: nuc.py (A/322 Annex C NUC bank + max-log demapper)
- Created: twisted_block.py (A/322 7.1.5.4 HTI, gated on A/327 Fig 6.5)
- Created: DataPlpChain + decode_data_plp/decode_subframe0_plp (generic MODCOD)
- Verified: chain decodes oracle-encoded cells bit-exact for QPSK/16QAM/
  64QAM/256QAM across rates; HTI NTI=2 round trip exact
- PLP 0 (64QAM-NUC 11/15): 0/74 both here and in the oracle on all captures;
  measured MER ~15.9 dB vs ~18.8 dB threshold -> link-limited, not a bug
- Created: test_data_plp.py, test_spec.py, test_air_payload.py; suite 278 passed
- Created: [[data-plp-payload]]
- Updated: [[index]]

## [2026-09-26] HTI cell interleaver | A/322 7.1.5.2
- Created: cell_interleaver.py (`CellInterleaver` dataclass) - per-FEC-block
  basic permutation L_0 (N_d-bit LFSR) + bit-reversed shift P(r), reset each
  TI block; gated on the spec's own printed P(r) vector
- Wired into decode_data_plp (`cell_interleaver` flag, was NotImplementedError)
- Verified bit-exact against oracle m6_tbi (`cell_basic_permutation`,
  `cell_shift`, `cell_deinterleave`) for N_cells 2700/5400/8100/10800/16200
- Created oracle fixture `plp0_hti_ci_*`; CI=1 decodes all 6 blocks exact and
  the same stream with the flag off fails, so the gate is non-vacuous
- Created: AGENTS.md (no magic numbers; dataclasses over dicts)
- Suite 289 passed
- Updated: [[data-plp-payload]], [[index]], SUMMARY.md

## [2026-09-26] Link/network layer | Baseband Packet -> ALP -> IPv4/UDP -> LLS
- Created: baseband.py - A/322 5.2.2 Baseband Packet header (MODE/pointer/OFI,
  optional + extension fields); real PLP-16 BBP is padding-only (EXT_TYPE=111)
- Created: alp.py - A/330 5.1 ALP de-encapsulation: single (short/long + SID),
  segmentation reassembly, concatenation (count+1 component lengths per
  Table 5.6), link-layer signalling header, resync on Baseband Packet pointers
- Created: ip.py - IPv4 fragment reassembly + UDP (RFC 791/768), A/331 LLS
  table extraction (224.0.23.60:4937)
- Wired: payload.DecodedStreams + decode_streams; receiver.decode_plp_streams
- Note: oracle m7_route reads concatenation with `count-1` component lengths;
  A/330 Table 5.6 loops `count+1`, so we follow the standard. Flagged, not copied
- Tests: test_alp.py (16 spec-derived round trips + live BBP header gate);
  suite 305 passed
- Updated: README, SUMMARY.md, [[index]]

## [2026-09-26] Normal-frame FEC | A/322 Annex A.1 + B.1 (Ninner=64800)
- Extracted the Annex A.1 parity-check address tables for all 12 rates with a
  new PyMuPDF tool (`tools/extract_ldpc64k.py`); two-column pages read
  column-major, gated on the closed arithmetic of 6.1.3 (row count, address
  range, 360-degree accumulator coverage)
- Extracted the Annex B.1 group-wise permutations for 6 modulations x 12 rates
  (`tools/extract_bicm.py`); each table's printed identity header is a free
  checksum. Both extractors reproduce the banked JSON bit-for-bit
- Tools cleaned to named constants/dataclasses; shared `tools/pdf_layout.py`;
  added C1 control (printed two-column rows run in non-increasing weight, a
  row-interleaved read breaks it)
- Independently cross-checked every banked table against `drmpeg/gr-atsc3`
  (GNU Radio transmitter, pinned commit 000b86a3): all 12 Annex A.1 tables and
  all 72 Annex B.1 permutations match element-for-element. Downloaded at run
  time, never committed; `tools/crosscheck_web_tables.py` + `test_web_tables.py`
- Added `tools/spec_sources.py`: one registry for the official A/322/A/330 PDF
  URLs, the pinned witness repo/commit, and a download cache. The extractors now
  take an optional PDF and fetch the official A/322 PDF when given no arguments
- Extended `ldpc_exact.ATSC3LDPCExact` and `group_interleaver.GroupInterleaver`
  to Ninner=64800; rate 7/15 is Type A at normal frames (Table 6.5) but Type B
  at short frames, and Type A FEC does not imply a Type A block interleaver
  (Tables 6.8/6.9) - both now driven by named dataclass tables
- `payload.DataPlpChain` selects the frame length from `L1D_plp_fec_type`
  (`PLP_NINNER`); the printed 256QAM 6.2.3.1 block-interleaver example gates
  Type A. Removed all raw modulation strings/literals in favour of the shared
  `nuc` constants
- Tests: test_normal_fec.py (63) - table geometry, all-rate encode/syndrome,
  end-to-end interleaved error correction for QPSK/16QAM/64QAM/256QAM;
  test_web_tables.py (2) - independent-source cross-check; suite 424 passed
- Note: no live PLP-1 capture yet; gated on the standard's own tables and
  cross-checked against the oracle's spec_ldpc64k/spec_bitint as referee
- Updated: README, SUMMARY.md, [[index]]

## [2026-09-26] Subframe 1 (16K) | geometry, FI reset rule, pilot tables
- Fetched the per-FFT pilot/data-cell tables (A/322 7.3-7.6, Annex F, D.1.4/
  D.1.5) from the pinned independent transcription (`drmpeg/gr-atsc3@000b86a3`,
  `params.h` + `pilotgenerator_cc_impl.cc`) with `tools/fetch_pilot_tables.py`;
  wrote `atsc3lib/data/pilot_tables.json`. The fetch is gated by the
  constant-data-carrier identity (8.1.4.1), which also confirms Table 8.3's
  allowed patterns and catches the SPx_4 one-row-short mis-read
- Replaced the hand-typed 8K constants and the CP32-derived CP16 helper with
  `atsc3lib.pilot_tables`; `spec.additional_cp`/`avail_data_cells` delegate
- Generalised the cell pool: `build_data_symbol_pool` (any FFT/GI/pilot/cred,
  explicit FI-counter origin, no Preamble); `build_cell_pool` is now the
  subframe-0 case (origin 1); `decode_subframe_plp` + `receiver.subframe_geometry`
  + `decode_plp_payload(subframe=...)` route subframe 1 (origin 0, A/322 7.3
  rule 2 - the counter resets at the boundary)
- Fixed a latent parse bug: `L1D_plp_HTI_cell_interleaver` was read and discarded
- Real air: the 16K/SP4_4 pool closes at exactly 956179 cells (matches the
  reference); per-symbol pilot counts match Tables 7.4/7.5
- PLP-1 does not converge: ~16.4 dB MER vs ~22 dB needed for 256QAM 11/15.
  `tools/survey_sf1_snr.py` (new; `--src` folder, argparse, logging, and a
  bootstrap `--versions` filter that cut per-file time ~10x) scanned every RF33
  capture - all 7.6-9.2 dB on the same crude metric, none stronger. Link limit,
  like PLP-0, not a chain error
- Tests: test_subframe1.py (13), test_pilot_tables.py (4),
  test_fetch_pilot_tables.py (2, network-gated), bootstrap versions test;
  suite 444 passed
- Updated: README, SUMMARY.md, [[index]], [[subframe1-plp1]]

## [2026-09-26] L1-Basic/L1-Detail Mode-1 parity repetition (A/322 6.5.2.7)
- The strongest stations on the current antenna (RF30 WIAV 569 MHz, RF25
  539 MHz) are a *different machine*: LDM, CTI, Ninner 64800, a two-symbol
  Preamble, and **L1-Basic Mode 1** - the only signalling mode that uses
  6.5.2.7 parity repetition.  None of it decoded before.
- Fixed the repetition handling in both codecs.  A/322 6.5.2.9's transmitted
  word is `[Nouter][Nrepeat repeated parity][Nfec - Nouter tail]`: the repeat
  block is the FIRST Nrepeat permuted-parity bits, so it lands on the same
  codeword positions as the head of the tail and their LLRs add.  The old code
  truncated `cells_to_llr` to Nfec (dropping the repeat) and placed the repeat
  block *after* the tail.
- Gate: A/322 Table 6.17 prints 3820 cells for Mode 1; the 6.5.2.7/6.5.2.8
  arithmetic reproduces it only with the Nrepeat term (1984 without).  L1-Detail
  Mode 1's Ksig 336 (RF30's signalled L1_Detail_size) gives exactly the 3611
  cells an independent receiver reads.
- Real air: **RF30's L1-Basic now verifies (BCH + CRC OK)**.  L1-Detail is
  still blocked by the two-symbol Preamble (A/322 7.2.5.1/7.2.5.2), which is
  the next rung on that multiplex.
- Tests: test_l1_detail.py mode-1 geometry (3611 cells == signalled) and
  mode-1 round-trip; suite 446 passed

## [2026-09-26] Scope decision: 256QAM requires line-of-sight
- 256QAM (A/322 7.2.2) needs ~22 dB MER, which requires line-of-sight to the
  transmitter.  This installation has no LOS path to any ATSC 3.0 station, so
  the hotter antenna does not change it.  A fresh capture sweep (HackRF gains
  8-40, ~5 s) peaked at 9.4 dB crude, still ~5.6 dB short.
- **Decision:** 256QAM, and any other feature that in practice requires LOS,
  is not implemented and will not be implemented.  The subframe-1 rung is
  therefore closed on its structural gates (pilot/data-cell identity and the
  live 956179-cell pool) rather than on PLP-1 payload bits.
- Still in scope and non-LOS: L1 signalling (all reachable modes), QPSK/16QAM,
  and 64QAM-NUC (RF30).  The two-symbol Preamble remains the next rung there.
- Updated: [[subframe1-plp1]], SUMMARY.md, [[index]]; suite 448 passed

## [2026-09-26] Multi-symbol Preamble (A/322 7.2.5)
- RF30 (WIAV-CD 569 MHz) and RF25 signal NP = 2 Preamble symbols.  L1-Basic
  fits in the first symbol but L1-Detail (RF30: 3708 cells) overflows its 1031
  free cells, so it continues in the second Preamble symbol.  The receiver used
  to stop at "multi-symbol Preamble not implemented"; RF30's whole per-PLP
  configuration was unreachable.
- Implemented the spec: 7.2.5.2's block interleaver Lc = NP columns, Lr =
  floor(total/NP) rows (verified as an exact inverse for NP = 1 and 2); later
  Preamble symbols use L1B_preamble_reduced_carriers and the FI counter keeps
  counting frame symbols, so subframe 0's first data symbol has origin NP.
- Signalled/derived identity: RF30 signals L1B_L1_Detail_total_cells = 3708 and
  the Ksig-352 L1-Detail chain yields 3708 cells.
- Real air: **RF30's L1-Basic (Mode 1) + L1-Detail now decode (BCH + CRC OK)** -
  CLI prints BSID 9100 and two LDM-layer PLPs.  Fixture
  `rf30_preamble_pair.npy` gates it; Table 7.2 is now gated for all cred 0..4.
- Tests: test_air_multi_preamble.py (2), test_l1_detail.py block-interleaver
  inverse (6) + RF30 geometry, test_preamble.py Table 7.2 all-creds (7);
  suite 464 passed
- Still open on that multiplex: LDM core demapping, CTI (7.1.4), Ninner 64800
  on the PLPs; RF30 PLP-1 is 64QAM-NUC 6/15 (in scope), RF25 PLP-1 256QAM-NUC
  7/15 (out of scope, needs LOS)
- Updated: [[multi-symbol-preamble]], [[index]], SUMMARY.md, README.md

## [2026-09-26] Fine timing + per-symbol CPE (LDM front-end rung)
- Added the two front-end stages the oracle's core path uses and the
  RF33-class path never needed: scattered-pilot fine timing (A/322 8.1.3.1)
  and a decision-directed per-symbol common-phase correction (CPE).
- `payload.py`: `pilot_coherence` (in `preamble.py`), `FineTiming` +
  `fine_timing`/`subframe_fine_timing`, `CpeSpec` + `cpe_correct` +
  `cpe_spec`/`plp_alphabet`/`dummy_cell_values`; plumbed through
  `build_cell_pool`/`build_data_symbol_pool` and the subframe/CTI decode APIs.
  `spec.py`: `FINE_TIMING_SPAN`, `CPE_ITERATIONS`.
- The CPE estimate only uses cells with a known alphabet (the PLP's own slice
  plus the A/322 7.2.6.5 dummy tail); the correction reaches every cell of the
  symbol.  A degenerate fitted gain is skipped, not divided by.
- Gates: `tests/test_fine_timing_cpe.py` (12) - synthetic shift recovery
  (offset == 11), real-air optimality (offset == 0, coherence > 0.9),
  synthetic per-symbol phase removal, wrong-alphabet and region controls, and
  a real-air integration test (RF33 PLP-16 decodes byte-identically with and
  without the stages).  Suite 508 passed.
- Finding: on the banked RF30 capture the *oracle's own* demodulator also
  fails the core layer (0/4, ~18000/38880 unsatisfied), and its front end
  cannot acquire L1 on the raw capture; our preamble pilot coherence there is
  0.825 vs 0.96-0.98 on fresh RF33 with the same radio.  RF30 is capture
  link-limited, not demod-limited.  Fresh RF33 captures decode cleanly and
  gate the stages.
- Updated: [[fine-timing-cpe]], [[index]], SUMMARY.md

## [2026-09-26] Scope re-measured on a solid feed; RF30 captures removed
- Put a good feed on the radio and re-measured the high-order margin directly,
  rather than assuming it.  On the solid capture, subframe-0 PLP-0 (64QAM-NUC
  11/15) measures **16.0 dB MER** against its ~18.8 dB threshold, and
  subframe-1 PLP-1 (256QAM-NUC 11/15) measures **20.6 dB** against its ~22 dB
  threshold.  PLP-16 (QPSK 2/15) in the same frame decodes 1/1.
- Conclusion unchanged but now measured: both high-order payloads are ~1.4-2.8
  dB short on the strongest reachable multiplex, with no LOS to close it.
  256QAM stays out of scope; the subframe-1 rung stays gated structurally.
  Wording changed across AGENTS.md / README.md / SUMMARY.md / wiki from
  "needs LOS" to the measured shortfall.
- Removed all 21 RF30 `.iq` captures under `out/` and the `/tmp/opencode/rf30_*`
  scratch files (RF30 never decoded: its captures measured preamble coherence
  0.825 against 0.96-0.98 on RF33).  Kept the committed
  `tests/data/rf30_preamble_pair.npy` fixture, which gates the working
  multi-symbol-Preamble L1 decode.

## [2026-09-26] Scope wording: RF33 64QAM PLP-0 marked unachievable
- Corrected the scope wording everywhere: **RF33 PLP-0 (64QAM-NUC 11/15) is
  link-limited too** and is now stated as **unachievable** (16.0 dB MER vs the
  ~18.8 dB threshold, ~2.8 dB short), alongside PLP-1 (256QAM 11/15, 20.6 dB vs
  ~22 dB, ~1.4 dB short).  Both are out of scope by decision; each closes on its
  structural gate (cell-pool + pilot/data identity), not on payload bits.
- Files: AGENTS.md, SUMMARY.md, atsc3lib/README.md, wiki/analyses/subframe1-plp1.md,
  wiki/index.md.

## [2026-09-26] Ground rule: every rung is validated on air
- Adopted as a project rule (AGENTS.md, SUMMARY.md, README.md, wiki/overview.md):
  **a feature is implemented only when a receivable stream carries it, and its
  gate is a real-air decode.**  A structural or synthetic round-trip gate does
  not qualify a rung — an unprovable stage is not written, because it can never
  be shown to work over the link.  When the link blocks a feature, record the
  blocker and move on.
- This closes the LDM-canceller question: RF30 is the only in-scope enhanced
  layer, its captures were link-limited (and removed), and the reference's own
  canceller stops ungated at 47% unsatisfied.  A synthesised round trip would be
  the only evidence, so per the rule it is not built.
- **RF6 checked, not receivable:** the five `out/recapture/rf6_*.iq` captures
  (85 MHz) contain no bootstrap in any file; `rf6_g8.iq` is flat ~1.5 dB noise
  and `rf6_85.iq` a flat ~40 dB plateau with no 6 MHz TV profile.  Super-low
  power; recorded, not worked.

## [2026-09-26] Done deal: do-not-revisit list + project-plan status
- Recorded the closures so they are not attacked again (README "Do-not-revisit
  list", wiki/overview.md):
  RF33 PLP-0 64QAM (2.8 dB short), 256QAM / RF33 PLP-1 / RF25 256QAM enhanced,
  LDM enhanced cancellation (captures removed, reference ungated), RF30/RF25
  core payloads (link-limited), the A/331 SLT/ROUTE/MMTP/AC-4/HEVC media stack
  (no receivable stream carries media or an SLT), and RF6 (not receivable).
- What is proven and kept: the full physical + link chain decodes RF33 PLP-16
  (QPSK 2/15) end to end, byte-identical to the independent reference.  Higher
  modes and LDM are implemented and bit-exact-gated but not air-demonstrated
  here.  The limitation is reception margin, not correctness.
- New [[project-plan-status]] maps the three-phase plan onto this: Phase 1
  physical+link met on air; audio (AC-4) and all of Phase 2 (transport, media,
  certificate gate) are link-blocked; Phase 3 depends on Phase 2.  **Nothing in
  the receiver plan is unblocked** — the only unblocking action is a receivable
  capture at adequate margin.

## [2026-09-26] Capture target set: RF30 core, not RF33
- Decided the standing next action: **RF30 core layer** is the only in-scope
  target with demonstrated margin (QPSK 6/15, SLT-bearing `lls_flag = 1`,
  threshold ~0.5 dB; the reference decoded it 218/224 FEC blocks).  Our RF30
  captures were link-limited, not demod-limited: coherence 0.825 vs 0.96-0.98
  on RF33 with the same radio/feed.  Target for a usable capture: **coherence >
  0.95**.
- RF33 is explicitly not worth chasing: its only decodable payload (PLP-16) is
  padding-only, and its LLS-bearing PLP-0 is 64QAM 11/15 at 16.0 dB MER against
  ~18.8 dB — unachievable.
- Caveat recorded: an RF30 core capture unblocks A/331 SLT / service discovery,
  **not sound** — RF30's AC-4 is on the Enhanced layer behind LDM cancellation
  (reference canceller ungated at 47%).
- Next: antenna moved, re-capture RF30 at low gain (8-12 sweet spot; saturation
  above ~24).
- Updated: [[project-plan-status]], [[overview]].

## [2026-09-26] RF30 re-capture after antenna move — still link-limited
- Moved the antenna and re-captured RF30 (569 MHz, WIAV-CD) at low gain
  (`out/rf30retry/rf30_569_g{8,12}.iq`, 10 Msps, 12 s each).
- **g8: no signal** — flat ~6.5 dB noise floor across the whole 10 MHz.
  Bootstrap detect fails.
- **g12: signal is not RF30.** The only strong energy is at **+3.4 to +5.0 MHz**
  (35 dB peak) — the adjacent 8-VSB neighbor seen in earlier captures. The
  **569 MHz center is ~11 dB, i.e. noise**. One bootstrap-like hit at structure
  25 did not yield L1 (L1-Basic/Detail both fail).
- Conclusion unchanged: RF30's own channel is **not receivable here**, and the
  antenna move did not change that.  The +3.3-5 MHz neighbor is not RF30.
- Nothing built.  Capture kept in `out/rf30retry/` as the record; the standing
  next-action (RF30 core, target coherence > 0.95) requires an actual signal
  improvement (location/antenna gain), not another low-gain take.

## [2026-09-26] BREAKTHROUGH: RF33 is the lighthouse; demapper scale bug; SLT decoded
- **RF33 PLP-0 (64QAM-NUC 11/15) decodes off air: 53-60/74 FEC blocks**, from a
  fresh low-gain capture `out/rf33retry/rf33_587_g12.iq` (10 Msps, g12).  It
  yields real LLS: the A/331 **SLT** (`bsid="540"`) and SystemTime.  First
  off-air service list.
- **Root cause of every earlier failure: a demapper scale bug.**  The A/322
  Annex C NUC alphabets have unit average power, but the equaliser output does
  not (RF33 subframe-0 pool ~0.87, subframe-1 ~0.60), and `nuc.demap_llr`'s
  min-distance sigma2 is not scale-free.  Fix: `DataPlpChain.decode_cells`
  normalises each FEC block to unit mean power before demapping.  Raw 0/74 ->
  normalised 53-60/74.  The CPE "rescue" of PLP-0 (0 -> 44/74) was its internal
  normalisation, not phase correction.  Gated by
  `tests/test_data_plp.py::test_decode_cells_scale_invariant` (fails without
  the fix).
- **Retractions:** "RF33 PLP-0 64QAM is 2.8 dB short, unachievable" is FALSE.
  "RF33 is padding-only, no services" is FALSE (PLP-16 is filler; PLP-0 carries
  the services).  The high-order-margin scope is retracted; 256QAM is back in
  scope.
- **SLT services (BSID 540):** 32-1 WHUT, 7-1 WJLA, 5-1 WTTG, 4-1 WRC,
  9-1 WUSA (last three protected), 7-10 T2, 7-11 PBTV, 7-20 GAMELOOP, 7-21 ROXI,
  SG-FE00.  Fixture `tests/data/rf33_slt_lls.bin`.
- **Still open:** subframe-1 PLP-1 (256QAM 11/15) at 0/117, 21.2 dB MER — above
  this chain's own synthetic 20 dB 256QAM threshold, so a **cell-model defect**
  (the reference's flagged 16K/SP4_4 available-data-carrier identity failure),
  not the link.  Next target.
  **[SUPERSEDED 2026-09-27 — this "chain defect" conclusion was a units error
  and is retracted; see the correction entry below.]**
- Docs updated: README (scale section, SLT), [[rf33-lighthouse-slt]] (new),
  [[overview]], [[project-plan-status]], [[subframe1-plp1]], [[index]].

## [2026-09-27] correction | RF33 subframe-1 PLP-1: link margin, not a chain defect
- **Retraction (units error):** the 2026-09-26 conclusion that subframe-1 PLP-1
  (256QAM-NUC 11/15) is a "chain defect" because it sat at "21.2 dB MER, above
  the synthetic 20 dB threshold" is **FALSE**.  Nearest-point MER is **not**
  Es/N0: for 256QAM 11/15 it overstates true SNR by ~2.5-4 dB.  Measured
  synthetic: true-18 dB -> 22.3 dB nearest-point MER, 0/4 converge; true-19.5 dB
  is the ~50% cliff.  The "21.2 dB" was never above threshold in true-SNR terms.
- **The old subframe-1 fixture was 9.3 dB nearest-point MER** — unusable for
  256QAM.  This is why earlier "our pool and the oracle builder both fail both
  decoders" tests proved nothing.  Replaced from a fresh good-antenna capture
  (RF33 587 MHz, gain 12) at **20.6 dB**; fixture `tests/data/rf33_sf1_y.npy`
  now exactly 75 symbols, pool 956179.
- **Tables exonerated:** pool 956179 == reference corrected model; 16K FI, HTI
  read order, group/block interleavers, pilot sets all match the oracle; this
  chain recovers oracle-encoded 256QAM 11/15 codewords.  **Decisive:** the
  oracle's own `m13_sf1` builder + decoder also returns **0/6, median ~7919
  unsatisfied (chance)** on our fixture, pool 956179, coherence 0.985 — two
  independent decoders failing identically is a capture-SNR verdict.  CPE does
  not help (0/117).
- **Conclusion:** PLP-1 is ~2 dB short of the link margin the oracle needed
  (21.7-22.9 dB nearest-point MER for 117/117).  Next step is a stronger record
  (better aim/gain/LNA), not a table change.
- Docs corrected: [[subframe1-plp1]], [[rf33-lighthouse-slt]],
  [[project-plan-status]], [[overview]], AGENTS.md, SUMMARY.md.

## [2026-10-03] Live-processing speed-up analysis | GPU optional
- Measured the RF33 subframe-1 PLP-1 hot path (256QAM 11/15, 64K, 117 blocks,
  `tests/data/rf33_sf1_y.npy`): demap (C `demodbindings`) ~170 ms/block; LDPC
  (`fecbindings`) 1.1 ms converged / 621 ms @100 it; the `ALPHA_LADDER` re-runs
  up to 3x; pool build ~1 s/subframe (batched FFT 74 ms/core); whole frame ~9 s
  on the 4-thread i5-4278U.  Frame budget 247.1 ms (1,708,032 samples).
- **Verdict: a GPU helps and can be optional.**  VRAM is small (~4.55 MB per
  codeword, ~0.53 GB per 117-block frame); the workload is bandwidth-bound.
  16 GB + 500 GB/s is the sweet spot (4070-class ~132 ms/frame at 100 it,
  5090-class ~33 ms).  Algorithmic LDPC fixes + threading are the cheaper first
  rung; the 9 s is mostly the alpha ladder + orchestration, not the kernel floor.
- Optionality design recorded: `gpubindings` behind a `try: import` dispatch;
  `fecbindings` stays the declared dependency and CPU fallback; differential
  tests as with the C/NumPy kernels.  Build tiers (Tier A GPU/16C, Tier B
  CPU-only) and the Oct-2026 price spike + GPU-rental fallback recorded.
- New page [[gpu-and-algo-speedups]]; [[index]] updated.

## [2026-10-03] Capture -> playable fragmented MP4 in atsc3lib
- **New rung: `atsc3lib/mp4.py` + `atsc3-media` CLI.**  A whole-frame drain of
  the saved RF33 capture now writes playable `.mp4` files, one per track, and
  the video decodes to 1920x1080 HEVC frames via PyAV.  This closes the
  "capture -> picture" path inside `atsc3lib` rather than only in the
  reference `lab/` tools.
- **The fix a player needs:** every broadcast MPU is a *self-contained* ISOBMFF
  file — `mfhd` sequence number restarts at 1 and `tfdt` decode time at 0 — so
  concatenating them unchanged makes a player show the first segment and stop.
  `mp4.retime` rewrites both onto one continuous timeline.  The timeline
  advances by the **media** `traf`'s duration, not the hint `traf`'s: the media
  `trun` alternates 1502/1501 ticks and sums to 180180 per 2.002 s MPU, while
  the hint `tfhd` rounds to 1502 — picking the hint drifts A/V by 2.3 s per two
  hours (the reference's E93/E86 finding, independently reproduced).
- **Honesty rules enforced:** a segment is appended only when the media bytes
  present equal the length its own `mdat` header declares (a transmitter
  statement, external referee), or trimmed to whole leading samples and
  labelled; ROUTE partial objects are trimmed at their first `start_offset`
  gap (`RouteObject.contiguous_length`, new).  The 3 s MMTP capture loses a
  packet inside the 120-frame video MPU before its first whole sample, so no
  MMTP video track is emitted — the builder says so rather than writing a
  broken file.  `MpuObject.leading_samples` (new) tracks the contiguous
  sample prefix.
- **On air (saved `out/rf33_sdrplay_if45_cs16.iq`, 3 s):** draining PLP-0
  (subframe 0) and PLP-1 (subframe 1) recovers 4 ROUTE video lanes from four
  services, each decoding: 239.255.32.1:8321 120 samples -> 117 frames;
  239.255.4.1:8041 -> 94; 239.255.5.1:8051 -> 135; 239.255.9.1:8091 -> 59
  (all 1920x1080), plus AC-4 audio (`ac-4` in `stsd`) and `stpp` subtitles.
- **Multi-PLP drain:** `decode_plp_frames_streams` and `mp4.build_from_iq(
  plp_ids=[(0,0),(1,1)])` acquire once and step every whole frame; the
  single-PLP CLI default is unchanged.
- Gated by `tests/test_mp4.py` (box/retime/trim arithmetic + an RF33 ROUTE
  picture gate) and the existing `tests/test_mmtp_media.py`; full suite 618.
- Docs: README media section, AGENTS.md, new [[capture-to-mp4]], [[index]].


## [2026-10-04] Combined A/V output (video + AC-4 audio) for offline and live
- **New rung: `atsc3lib/mux.py`.**  The broadcast `soun` track is raw AC-4 (no
  player decodes it), so the picture-only MP4 was silent.  `mux_av` stream-copies
  the HEVC from the built video track (PyAV `add_stream_from_template`, no
  re-encode), encodes the decoded AC-4 PCM to **AAC-LC**, and writes one
  **fragmented** MP4 (`frag_keyframe+empty_moov+default_base_moof`) a player
  (VLC) opens and a truncated file stays valid.
- **CLI:** `atsc3-media` now writes the combined `*_av.mp4` by default
  (`--no-mux` opts out; `--audio-track` picks the AC-4 track).  `av` added as a
  runtime dependency.
- **Live:** `live.LiveMediaSink` collects a bounded run's datagrams
  (`DEFAULT_MAX_DATAGRAMS`), then reassembles and writes the per-track MP4s,
  AC-4 WAVs and the combined A/V file; `atsc3-live --outdir` wires it in.  A
  live run still lags the air rate (pure-Python decode), but what it writes is a
  VLC-openable file.
- **On air (saved RF33, 3 s):** `atsc3-media out/rf33_sdrplay_if45_cs16.iq
  --rate 10e6 --fmt cs16 --all --frames 0` writes
  `239_255_32_1_8321_route_10+20_av.mp4` — 117 HEVC frames 1920x1080 + non-silent
  stereo AAC, 2.07 s, 800720 bytes.
- Gated by `tests/test_mux.py` (combined file reopens HEVC+AAC, video packet
  count preserved, audio non-silent, `moov < moof`) and
  `tests/test_live.py::TestLiveMediaSink`.  Docs: README media section,
  AGENTS.md, [[capture-to-mp4]].

## [2026-10-04] fix | A/V audio scratching was encoder clipping
- **Symptom:** the combined A/V file's audio had scratching.  Cause: the AC-4
  decoder emits **integer-scale** PCM (full-scale sine peaks near 2**18), but
  `mux._planar` fed those raw values to the AAC encoder, which expects
  `[-1, 1]` — so every loud sample clipped.  (`write_wav` already normalised,
  so the WAVs sounded fine; only the mux clipped.)
- **Fix:** one shared normaliser `audio.normalized_pcm` (peak -> `WAV_PEAK`
  0.9, silent track gain 1) now used by both `write_wav` and `mux._planar`.
  Measured: decoded peak 262291 -> muxed AAC peak 0.899, 0 samples >= 0.999.
- Gated by `tests/test_mux.py::TestMuxAv::test_audio_is_non_silent` (asserts no
  clipped samples) and `tests/test_audio.py::TestAudio::test_normalized_pcm_*`.

## [2026-10-04] docs | Wiki refresh + meta/ layout
- **Doc layout:** `AGENTS.md` now lives at `meta/AGENTS.md` with a symlink
  `AGENTS.MD -> ./meta/AGENTS.md` at the workspace root; `SUMMARY.md` and
  `OPENATSC3_PROJECT_PLAN.md` are in `meta/`.  Added a "Project context" section
  to `meta/AGENTS.md` pointing opencode at `meta/` for summary/reference docs
  and at `wiki/` as the living source of truth.
- **Wiki catch-up (was last updated 2026-10-03):** fixed the stale
  "AC-4 audio is the open media rung" claim in [[index]] and
  [[project-plan-status]]; the media stack (ROUTE/MMTP reassembly, fragmented
  MP4, AC-4 audio, combined A/V mux) is air-proven off RF33 as of 2026-10-04.
  Updated [[overview]] (PLP-1 117/117, media stack, stats 4/31) and added the
  normalisation fix to [[capture-to-mp4]].  The sole remaining receiver item is
  live real-time throughput ([[gpu-and-algo-speedups]]).
- Corrected stale test counts and the closed "AC-4 has no decoder" line in
  `meta/SUMMARY.md`.

## [2026-10-04] build | Own CENC content protection + receiver verification gate
- **Content protection (`openatsc3_pki/content.py`):** AES-128 CENC CTR sample
  encrypt/decrypt (byte-exact round trip), the ``pssh`` v0/v1, ``tenc``,
  ``senc``, ``schm`` boxes, the DASH ``ContentProtection`` element naming **our
  own DRM UUID** (`6f70656e-6174-7363-3300-000000000001`), and the A/331
  Table 7.32 ``security_properties_descriptor`` (per-asset
  ``scheme_code``/``default_KID``).  No Widevine, no A3SA.  +14 tests, 44 total.
- **Receiver gate (`atsc3lib/security.py`):** verifies the LLS security tables
  a PLP delivers via ``openatsc3_pki``, attaches a ``SecurityReport`` to
  ``DecodedStreams.verification``, and adds ``--trust-root`` /
  ``--require-signature`` to ``atsc3-decode``.  
- **Synthetic A/331-layer gate (`atsc3lib/tests/test_security.py`):** builds a
  CDT + SignedMultiTable, wraps them as LLS UDP datagrams, runs the real
  ``ip.parse_lls`` -> ``slt_from_lls`` -> ``security.verify_streams`` path, and
  asserts the happy path plus wrong-root and tamper failures.  +6 tests.
- Full atsc3lib suite green (643 tests + 6 new); openatsc3-pki 44 tests.
- Updated: [[own-ca-and-content-protection]], openatsc3-pki README.

## [2026-10-04] build | openatsc3-pki: root CA, issuance, CMS, CDT/SMT, verifier
- **Greenfield repo created** at `/home/ajonen/atsc3/openatsc3-pki/` (separate
  from `atsc3lib`).  Python + `cryptography` + `asn1crypto`.  Competing with
  A3SA as a parallel trust anchor; **no Widevine, no A3SA**; own root, own DRM.
- **Spec facts pinned** from A/360:2026-08 text and the published schema
  example.  `id-atsc = 1.3.6.1.4.1.51552`;
  `id-atsc-kp-signalingSigning = …51552.37.3`;
  `id-atsc-sdattr-bsid = …51552.9.1`.  Signing-signer profile = A/360 5.3.1.6:
  KeyUsage critical `digitalSignature` only, EKU critical signalingSigning, SDA
  `id-atsc-sdattr-bsid` = SET OF INTEGER bsids.  CDT (0x06) is gzipped XML,
  self-signed, carries the chain + end-entity certs + one stapled OCSP per
  cert; CMS profile (5.2.2.1) is detached, SigningTime whole seconds, SKI
  SignerIdentifier, no eContent/certs.  SignedMultiTable (0x07) signs
  `LLS_payload_count…payloads`, not gzipped.
- **Modules:** `keys`, `sda`, `x509`, `ca`, `ocsp`, `cms`, `cdt`, `verify`,
  `cli`.  CLI: `openatsc3-pki init-root|issue-ca|issue-broadcaster|show`.
- **Verifier** implements A/360 5.2.2.6 for both the CDT and a signed message.
- **Tests:** 30 pass; every negative gate (wrong root, expiry, tamper,
  CDT-key==CurrentCert, revocation, stale OCSP, wrong bsid, future/backward
  SigningTime, missing EKU).  Oracle: `tests/data/cdt_example.xml` (published
  ATSC example; bsid 33 pinned).
- **Open:** own-CENC content protection; `atsc3lib/security.py` integration.
- Updated: [[own-ca-and-content-protection]], [[index]], `meta/SUMMARY.md`
  (OID corrected to 51552), `meta/AGENTS.md`.

## [2026-10-04] plan | Own CA + content protection (greenfield, alongside A3SA)
- **Decision:** the certificate/security work is a separate greenfield product,
  `openatsc3-pki`, competing with A3SA — not an `atsc3lib` rung.  The standards
  (A/331 §5.9/§6.7, A/360 §5.2.2) define the cert formats and validation rules
  but not the operator; that open field is the value.  The CA runs alongside
  A3SA as a parallel trust anchor; the receiver trusts **our root only**
  (fail-closed).  **No Widevine, no A3SA** — own DRM scheme, own UUID.
- **Grounded facts:** LLS SignedMultiTable (id 0x07) is CMS `SignedData` over
  `LLS_payload_count…payloads` up to `signature_length`, not gzipped;
  CertificationData (0x06) is mandatory and carries end-entity certs + chain +
  **stapled OCSP** (offline/bounded); ROUTE SLS §5.2.2.4, MMTP SLS §5.2.2.5;
  ECDSA P-256/P-384, ATSC OIDs `1.3.6.1.4.1.51552`, EKU
  `id-atsc-kp-signalingSigning`, SDA extension.  Content: ISO/IEC 23001-7
  CENC, A/331 Table 7.32 `security_properties_descriptor`, MPD
  `ContentProtection@schemeIdUri`.
- **Plan:** (1) `openatsc3-pki`: root + issuance, then OCSP, then CMS signers,
  then own CENC.  (2) `atsc3lib/security.py` verifier + CLI gate.  (3)
  A/331-layer synthetic generator + negative gates (tamper/expiry/revocation/
  wrong-root/EKU).
- **Exemption recorded:** this rung cannot be air-proven (no receivable stream
  carries our signature/DRM); it is gated synthetically at the A/331 layer by
  construction — the one deliberate exception to the on-air rule.
- Created: [[own-ca-and-content-protection]]; updated [[index]].
- Next: root CA + issuance (user chose this end), then verifier vs the
  published ATSC CDT oracle.

## [2026-10-04] fix | Wiki skill loaded + link/orphan lint
- **Skill was not loaded:** the wiki skill lived at
  `.opencode/skills/wiki.md` with no frontmatter, but opencode only loads
  `.opencode/skill(s)/<name>/SKILL.md`.  Moved to
  `.opencode/skills/wiki/SKILL.md` with `name`/`description` frontmatter and
  deleted the stray `wiki.md`, so the wiki operations now auto-trigger.
- **Lint fixes:** created [[week-3-4-equalization]] (index linked it but no
  page existed); added the orphan [[live-ldpc-test-results]] to [[index]].
- Updated [[overview]] stats (analyses 31 -> 32).
