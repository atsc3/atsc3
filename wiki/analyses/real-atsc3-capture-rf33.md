# Real ATSC 3.0 Capture Acquired - RF 33 (587 MHz)

## Date
2026-09-25

## Summary
Found and validated a **real, demodulatable ATSC 3.0 capture**. This unblocks
the Path B integration that the previous WIAV-CD file could not support.

## Capture
```
hackrf_transfer -r out/recapture/at3_587.iq \
  -f 587000000 -s 10000000 -g 28 -l 28 -a 1 -n 15000000
```
- Center 587 MHz = RF channel 33 (584-590 MHz)
- Station: **WHUT-TV / ATSC 3.0 multiplex** (per RabbitEars, DC market ch33)
- 10 MS/s, 15M samples (~1.5 s), HackRF, average power ~0.0043
- Preserved as `out/wiav_whut_rf33_587mhz.iq`

Note on encryption: subchannels 04-1/05-1 are DRM-encrypted at the *service*
layer. That is above the physical layer - L1 signaling and PLPs remain
decodable regardless.

## Confirmed by two independent methods

### 1. Bootstrap detection (`detect_bootstrap`)
- **Result:** major=0, minor=0, **preamble_structure=27**
- Correlation peak/mean = **42.5** (very strong lock), start sample 1,133,426
  at the 6.144 MHz bootstrap rate.
- Signaling bytes: [12, 2, 27].

### 2. OFDM parameter detection (new `ofdm_detect.py`)
- **Result:** FFT=8192, GI=1536, CP correlation **0.665**
  (other FFT sizes score 0.15-0.16, i.e. noise).
- Independently reproduces the bootstrap-derived geometry.

Both agree with A/322 Table H.1.1: **structure 27 = 8K, GI 1536, Preamble
Pilot DX=4, L1-Basic Mode 3**.

## Frame timing
- Bootstrap (12288 samples at 6.144 MHz) maps to 13,824 samples at the 6.912 MHz
  main rate.
- Preamble OFDM starts at main-rate sample 1,288,928.
- First preamble symbol aligns at offset 0 from that point with CP correlation
  0.72; ~933 OFDM symbols available in the capture.

## New tooling added
- `atsc3lib/ofdm_detect.py`: correct, efficient CP-based FFT/GI detector
  (`detect_ofdm_params`, `cp_correlation`, `find_symbol_start`). The earlier
  `ofdm.py` used an O(n*cp) loop and a broken non-per-normalized metric that
  returned the same value for every GI.
- Fixed `_fft_correlate_abs` in `bootstrap.py` (conjugation + indexing); now
  exactly matches `np.correlate`.
- Hardened `detect_bootstrap` to validate multiple correlation peaks.

## Why earlier captures failed
- Previous HackRF captures (WIAV-CD ch30 = 569 MHz) showed the ATSC pilot at
  +3.31 MHz and no recoverable OFDM, because those channels carry **ATSC 1.0
  (8VSB)**, not 3.0. RF 33 is the true ATSC 3.0 multiplex.
- RF 36 (605 MHz) was also tested: no bootstrap (not 3.0 / overloaded).

## Status
✓ Real ATSC 3.0 capture obtained and preserved
✓ Bootstrap detected (structure 27) + OFDM params confirmed (FFT8192/GI1536)
✓ Acquisition chain validated end-to-end on live air signal
✓ Full suite: 293 passed (added `tests/test_ofdm_detect.py`)

## Next
1. Preamble demodulation: extract data cells (pilot pattern DX=4), QPSK demap.
2. L1-Basic decode (Mode 3) using the validated `L1BasicCodec`.
3. L1-Detail decode -> per-PLP MODCOD, then PLP payload extraction.
4. Service layer (ROUTE/MMT) is out of scope for encrypted subchannels.

## See Also
- [[bootstrap]]
- [[l1-basic-fec]]
- [[real-capture-acquisition]]
- [[washington-dc-atsc3-stations]]
