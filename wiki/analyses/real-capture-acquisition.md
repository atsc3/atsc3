# Real-Capture Acquisition Findings

## Date
2026-09-25

## Goal
Run the Path B acquisition chain (resample -> bootstrap detect -> frame geometry)
on the real WIAV-CD capture (`out/wiav_cd_hackrf.iq`, 12.5M samples, 10 MS/s).

## What was built
`atsc3lib/frontend.py`:
- `resample_iq(iq, fs_in, fs_out)` - rational polyphase resampling.
- `read_hackrf_iq(path)` - int8 interleaved -> complex64.
- `acquire_frame(iq, fs)` - resample to 6.144 MHz, detect bootstrap, decode
  preamble structure, map timing to the 6.912 MHz main rate.
- Rate constants from A/322 Annex N.2.2: bootstrap 6.144 MHz, main
  `0.384*(bsr+16)` MHz = 6.912 MHz for bsr=2.

Also upgraded the bootstrap detector:
- `_fft_correlate_abs` - FFT-based correlation (fixed conjugation/indexing;
  now matches `np.correlate` exactly), making full-capture search feasible.
- `detect_bootstrap` now validates **multiple correlation peaks** and requires
  the recovered cyclic shifts to yield valid signaling, instead of trusting the
  single maximum (which always locks onto noise).

Validated: synthetic bootstraps at 2.4/10 MHz capture rates, structures
6/10/30/90, recovered correctly through the resample->detect path.

## Result on the real capture: NO BOOTSTRAP FOUND

Evidence the capture is not usable as-is:
1. **Energy is not centered**: PSD peak at **+3.3 MHz** (~59 dB) instead of DC.
   The ATSC signal should be centered at the tuned frequency.
2. **Bootstrap correlation is flat**: best peak/mean ratio ~4-5 for all 16
   version hypotheses, and independent of coarse frequency offset (-600..+600 kHz
   scanned). A real bootstrap gives a sharp peak (ratio >> 10).
3. **No OFDM cyclic-prefix structure**: the CP autocorrelation metric is
   essentially identical (~0.79) for every candidate (FFT 8192 with GI
   1/4, 1/8, 1/16, 1/32). Real OFDM shows a clear peak at the true (FFT, GI).
4. **No symbol periodicity**: the lag autocorrelation has no peaks at OFDM
   symbol lengths; the only notable structure is a low-lag tone.
5. Notching the +3.3 MHz tone does not reveal OFDM underneath.

Conclusion: the capture is dominated by a CW interferer and does not contain a
demodulatable ATSC 3.0 waveform at the expected center. This is a **capture
problem, not a decoder problem** - consistent with the earlier integration test
(`wiki/analyses/integration-test.md`) where LDPC never converged on this file.

## Likely causes
- Wrong center frequency / DC offset in the original capture (tuned off by
  ~3.3 MHz, or a strong adjacent/broadcast tone).
- HackRF DC/LO leakage and no front-end filtering.
- Possibly captured during a period with no ATSC 3.0 frame, or at insufficient
  SNR for this antenna/position.

## Recommendation
Recapture cleanly before further integration testing:
1. Confirm WIAV-CD center (ch 30 = 569 MHz per `scan.csv`) and that the SDR is
   tuned to it; verify the spectral peak sits at DC.
2. Reduce gain to avoid overload; enable/keep the front-end filter.
3. Capture at 10 MS/s (covers the 6 MHz channel with margin) for >= 2 s.
4. Re-run `acquire_frame`; a valid bootstrap should give a sharp correlation
   peak and a decoded preamble structure.

## Status
✓ Front-end (resampling + robust bootstrap acquisition) implemented and tested
✓ Detector hardened (FFT correlation + multi-candidate validation)
✗ Real WIAV-CD capture contains no recoverable bootstrap/OFDM (interferer)
⚠ Full real-capture integration blocked pending a clean capture

## Full suite
280 passed (added `tests/test_frontend.py`).

## See Also
- [[bootstrap]]
- [[l1-basic-fec]]
- [[integration-test]]
- [[optimization-symbol-detection]]
