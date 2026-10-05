# Front-end stages: fine timing and per-symbol CPE

The RF33-class path demodulates cleanly without either stage: the bootstrap
anchors the frame, the scattered pilots equalise each symbol, and the LDPC
locks. The LDM/CTI multiplexes (RF30/RF25) are a harder machine, and the
independent receiver (`/tmp/opencode/felbs-ref`) applies two front-end stages
ours did not have. This rung adds them, in our own style, and gates each.

## Fine timing (A/322 8.1.3.1)

The bootstrap fixes the frame to within one guard interval. The residual
sampling instant is a few samples, and because it is a linear phase ramp across
carriers it de-correlates a scattered-pilot channel estimate enough to randomise
a QPSK constellation.

`payload.fine_timing` scans the scattered-pilot coherence of one non-boundary
data symbol over `±spec.FINE_TIMING_SPAN` samples and returns the offset that
maximises it. The statistic

```
C = |Σ_j q_j conj(q_{j+1})| / Σ_j |q_j|²,    q = carrier / known_reference
```

fits no parameter, so the search that reads it cannot inflate it. It is scored
on the *scattered* pilots only, not the common continual pilots, so the lattice
is uniform. A subframe boundary symbol uses DY = 1 and a different cell count,
so `subframe_fine_timing` picks the first normal symbol as the ruler.

**The ruler's limit, measured.** For SP4_2 the scattered pilots are DX·DY = 8
carriers apart, so the coherence is flat across more than the ±24-sample search
window; on a well-timed capture the offset lands at 0 and the coherence is
high. That is the point: the stage *confirms* the bootstrap timing and corrects
a gross slip, and it does not pretend to sub-sample resolution. The oracle
records the same property for the sparse pilot patterns.

## Per-symbol CPE

After equalisation each OFDM symbol can still carry a residual common phase:
the channel estimate is built from that symbol's own pilots, and the phase noise
within the symbol is not. `payload.cpe_correct` fits, per symbol, the two real
parameters of a complex gain `c` against the alphabet each cell is already known
to carry:

```
c = <hard, z> / <hard, hard>,   z ← z / c          (spec.CPE_ITERATIONS times)
```

This is common-phase removal, not a search: it cannot rescue a wrong
constellation, and the LDPC remains the only oracle. Two facts make it safe and
effective:

- The estimate may only use cells whose true alphabet is known. `CpeSpec`
  carries the PLP's own `start..size` slice plus, where it exists, the A/322
  7.2.6.5 dummy tail whose `±1` values are known exactly from the baseband
  scrambler. For subframe 0 the Preamble spare cells precede the slice and
  carry no known alphabet, so they are excluded.
- The correction itself — one complex gain per symbol — is applied to every cell
  of that symbol, because the gain is common to the whole symbol.

A degenerate fitted gain is skipped rather than divided by (the pool can contain
a notch that drives `c` to zero).

## Gates

`tests/test_fine_timing_cpe.py` (12 tests):

- **Fine timing, synthetic** — a whole-sample `np.roll` of the RF33 frame
  fixture by 11 samples is recovered exactly (`offset == 11`).
- **Fine timing, real air** — on the unshifted fixture the nominal `t0` is
  already optimal (`offset == 0`) and the pilot coherence is > 0.9.
- **CPE, synthetic** — a per-symbol phase of 0 / +0.35 / −0.6 rad on ideal
  QPSK is removed to < 1e-6 mean nearest-point error.
- **CPE, control** — a wrong alphabet does not improve the fit, and a `region`
  mask keeps corrupt cells out of the estimate.
- **Real-air integration** — the RF33 PLP-16 block decodes byte-identically
  with and without the stages, so enabling them cannot regress a decodable PLP.

## Where the LDM failure actually is

On the (now-removed) banked RF30 capture the oracle's *own* demodulator also
failed the core layer (0/4 FEC blocks, ~18000/38880 unsatisfied), and the
oracle's front end could not even acquire L1 on the raw capture at the same
signal level.  Our preamble pilot coherence there was 0.825, against ≥ 0.96 on
fresh RF33 with the same radio and feed.  The RF30 capture was link-limited,
not demod-limited: the two new stages did not change its unsatisfied count
because they cannot manufacture ~12 dB of missing pilot SNR.  Those RF30
captures have been removed so they are not mistaken for a usable fixture.

Fresh RF33 captures decode at coherence 0.96–0.98, and the stages are gated
there.  RF30's PLP-1 (64QAM-NUC 6/15) remains the in-scope LDM target, pending a
capture at adequate margin.

Reference: ATSC A/322:2024-04, Sections 7.2.5, 7.2.6.5, 8.1.2, 8.1.3.1, 8.1.5.
