# Wiki Index

*Last updated: 2026-10-04*

## Sources

- [[openatsc3-project-plan]] - 12-16 week project to build ATSC 3.0 receiver and CA platform

## Entities

- [[hackrf-one]] - Supported SDR hardware for ATSC 3.0 reception (6 MHz+ needed)
- [[nooelec-nesdr-smart-v5]] - User's RTL-SDR dongle; **not supported** (2.4 MHz, too narrow)

## Concepts

- [[atsc3-physical-layer]] - OFDM signal processing pipeline (Weeks 1-8)
- [[atsc3-certificate-validation]] - Certificate authority integration (Week 12+)
- [[rtl-sdr-scanning]] - How to scan for ATSC 3.0 signals with RTL-SDR
- [[rtl-sdr-scan-format]] - How to read scan.csv output

## Analyses

- [[scan-results-2026-08-30]] - UHF scan found 5 strong TV signal clusters (20+ dB)
- [[washington-dc-atsc3-stations]] - Identified WIAV-CD ch30 as strongest (22.05 dB)
- [[week-1-2-ofdm-demodulation]] - Week 1-2: OFDM demodulation ✓
- [[week-3-4-equalization]] - Week 3-4: Channel estimation & equalization ✓
- [[week-5-qam-demodulation]] - Week 5: QAM demodulation to bits ✓
- [[week-6-7-ldpc-decoding]] - Week 6-7: LDPC error correction ⚠ Partial
- [[live-ldpc-test-results]] - First live LDPC run (2026-09-23, partial); early result, superseded
- [[ldpc-integration-complete]] - LDPC with ATSC 3.0 matrices ✓ COMPLETE
- [[optimization-symbol-detection]] - Symbol detection optimization analysis
- [[l1-signaling-implementation]] - L1-Basic/L1-Detail parser
- [[bit-deinterleaver-descrambler]] - Bit deinterleaver & PRBS descrambler
- [[integration-test]] - Full decode chain integration test on real data
- [[exact-ldpc-and-group-interleaver]] - Exact A/322 LDPC codec + group interleaver ✓
- [[bootstrap]] - Bootstrap acquisition (Path B) ✓; L1-Basic FEC next
- [[bch-outer-code]] - Exact BCH(16200/64800) outer code ✓
- [[l1-basic-fec]] - L1-Basic FEC chain (all 7 modes) ✓
- [[real-capture-acquisition]] - Front-end + real capture: no bootstrap found ⚠
- [[real-atsc3-capture-rf33]] - Real ATSC 3.0 on RF33: bootstrap + OFDM confirmed ✓
- [[l1-basic-real-air]] - L1-Basic decoded from real air; chain bugs fixed ✓
- [[l1-detail-real-air]] - L1-Detail decoded; full per-PLP config from real air ✓
- [[ldpc-vectorized]] - LDPC decoder vectorized (numpy), ~15x faster ✓
- [[consolidation]] - Gen-1 scaffolding removed; single validated chain + CLI ✓
- [[preamble-demod-toolchain]] - Frequency interleaver + pilot reference ✓; channel est. next
- [[data-plp-payload]] - Data-PLP payload chain (NUC/HTI/BICM/cell interleaver); PLP-16 off air ✓
- Link/network layer - A/322 BBP header, A/330 ALP, IPv4/UDP, A/331 LLS ✓
- [[normal-frame-fec]] - Normal-frame FEC (Ninner=64800); Annex A.1/B.1 extracted + gated ✓
- [[subframe1-plp1]] - Subframe 1 (16K) geometry + FI reset + pilot tables; RF33 PLP-0 (64QAM) and PLP-1 (256QAM) unachievable, gated structurally ✓
- [[multi-symbol-preamble]] - Multi-symbol Preamble (A/322 7.2.5); RF30 L1-Basic+L1-Detail decode ✓
- [[fine-timing-cpe]] - Fine timing + per-symbol CPE (A/322 8.1.3.1/7.2.6.5); gated on RF33 ✓
- [[rf33-lighthouse-slt]] - RF33 is the DC lighthouse; demapper scale fix; off-air SLT decoded ✓
- [[capture-to-mp4]] - Saved RF33 capture -> playable fragmented MP4; ROUTE video decodes 1920x1080 ✓; one combined A/V MP4 (HEVC + AAC from AC-4) ✓
- [[project-plan-status]] - Three-phase plan status vs the on-air rule
- [[gpu-and-algo-speedups]] - Live-processing speed-up analysis; GPU optional vs CPU/algorithmic fixes
- [[own-ca-and-content-protection]] - Greenfield own CA (alongside/competing with A3SA) + own DRM; A/331/A/360 formats define the certs, not the operator

## Closed (air-proven)

- [[capture-to-mp4]] - AC-4 audio decodes off RF33 (`ac4bindings`); `mux.py`
  writes one playable A/V MP4 (HEVC stream-copied + AAC-LC), offline and live.
  A/V audio clipping fixed via `audio.normalized_pcm`.  The remaining open item
  is **live real-time throughput**, not any missing media feature.

## Closed (not receivable)

- LDM enhanced-layer cancellation - only in-scope layer (RF30 PLP-1) is behind
  removed link-limited captures; synthetic gate does not qualify a rung
- RF6 (85 MHz) - no bootstrap, flat noise; super-low power
- A/331 SLT / ROUTE / MMTP / AC-4 / HEVC - see [[rf33-lighthouse-slt]] and
  [[capture-to-mp4]]: the SLT, ROUTE/MMTP/media reassembly, the fragmented MP4
  build, AC-4 audio decode and the combined A/V mux are all air-proven off RF33
  (2026-10-04).  No media rung remains open here; only live real-time
  throughput ([[gpu-and-algo-speedups]]) is outstanding.
