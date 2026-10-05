---
created: 2026-09-26
updated: 2026-09-26
sources: [out/rf33retry/rf33_587_g12.iq]
tags: [rf33, lighthouse, slt, demapper, scale, 64qam]
---

# RF33 is the lighthouse: the demapper scale bug, and the first off-air SLT

## Summary

RF33 (587 MHz, BSID 540) is the Washington **lighthouse** multiplex carrying the
major channels.  It decodes off air: PLP-0 (64QAM-NUC 11/15) converges 53-60 of
74 FEC blocks from a clean capture and yields the A/331 **SLT** and SystemTime.
This overturns the earlier "padding-only, 64QAM unachievable" verdict.

## The bug that hid it

The A/322 Annex C NUC alphabets have **unit average power**.  An equalised cell
block does **not** (RF33 subframe-0 pool ≈ 0.87, subframe-1 ≈ 0.60 at this
capture), and the max-log demapper is **not scale-invariant**:
`nuc.demap_llr` estimates its noise from the min distances, which do not scale
with the cell cloud, so a mis-scaled block demaps to garbage LLRs.

Symptom that cracked it: `cpe_correct` (which internally normalises to unit
power) turned PLP-0 from 0/74 to 44/74, yet it barely changed the MER.  The
useful part of CPE was the **normalisation**, not the phase correction.

Fix: `DataPlpChain.decode_cells` normalises each FEC block to unit mean power
before demapping.  Effect on RF33 PLP-0 (64QAM 11/15), no CPE, no fine timing:

    raw pool power 0.87, 0/74  ->  normalised, 53-60/74

Gated by `tests/test_data_plp.py::test_decode_cells_scale_invariant`, a
synthetic 256QAM 11/15 block decoded at scales 0.6/0.87/1.3/2.0 with identical
bits; it fails without the fix.

## The evidence: the SLT

From `out/rf33retry/rf33_587_g12.iq` (10 Msps, gain 12, ~16 s), PLP-0 gives
60 BCH-ok FEC blocks -> 74 ALP packets -> 72 datagrams -> the LLS tables:

    table 1  SLT          726 bytes   gzip, bsid="540"
    table 3  SystemTime   177 bytes
    table 255             251 bytes

The SLT (fixture `tests/data/rf33_slt_lls.bin`) lists:

    serviceId  ch     name     note
    1          32-1   WHUT
    2          7-1    WJLA
    3          5-1    WTTG     protected (drmSystemID)
    4          4-1    WRC      protected
    5          9-1    WUSA     protected
    6          7-10   T2       broadband
    7          7-11   PBTV     broadband
    8          7-20   GAMELOOP broadband
    9          7-21   ROXI
    65024              SG-FE00

Source IP 172.18.129.20, SLS on 239.255.x.x.

## Where this leaves the project

- The old "RF33 PLP-0 64QAM is 2.8 dB short, unachievable" is **false** and is
  retracted.  The payload decodes; what was missing was normalisation.
- The old "PLP-16 padding-only means no services" is also corrected: PLP-16 is
  a filler PLP, but PLP-0 carries the real service data and its LLS.
- **Still open: subframe 1's PLP-1 (256QAM-NUC 11/15)**, 0/117 — but this is a
  **link-margin** result, not a chain defect.  The earlier "21.2 dB MER, above
  the 20 dB synthetic threshold" reading was a **units error** (nearest-point
  MER overstates true SNR by ~2.5-4 dB at 256QAM 11/15).  Every table matches
  the reference, and the oracle's own `m13_sf1` decoder also fails 0/6 on our
  fixture — two independent decoders failing identically.  See
  [[subframe1-plp1]].

## See Also

- [[subframe1-plp1]]
- [[project-plan-status]]
- [[fine-timing-cpe]]
