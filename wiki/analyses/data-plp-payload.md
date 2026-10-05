# Data-PLP Payload Chain

## Date
2026-09-25

## Why
The validated chain stopped at the per-PLP configuration decoded from
L1-Detail.  The next rung is the data-PLP payload: cells -> bits -> Baseband
Packets.  An independent receiver (Felbs/atsc3, Apache-2.0) was already
decoding RF33's PLP-16 and was used **as a referee only** to verify each stage.

## What was wrong on the live path (both were latent, never previously run)
1. **Guard-interval signalling decoded as a list index.** L1-Basic signals
   the A/322 Table 8.6 *value* (6 = GI6_1536), but the receiver indexed
   `GUARD_INTERVALS[fft][value]`, so value 6 gave 2048.  Fixed with an explicit
   `spec.GI_SAMPLES` map and `spec.guard_interval()`; value 0 and 13..15 are
   rejected, and values illegal for the FFT size are rejected.
2. **No bootstrap fractional-CFO de-rotation.** The independent receiver
   de-rotates the residual CFO estimated from the bootstrap's part-C repeat
   before OFDM demodulation.  Added `bootstrap.fine_cfo()` (assumption-free,
   +/-1500 Hz unambiguous) and applied it in `_bootstrap_to_preamble`.

After both fixes the real capture decodes PLP-16 end to end in the library,
matching the referee's Baseband Packet byte-for-byte.

## Added
- `nuc.py` - A/322 Annex C NUC alphabets (banked numeric artifact) and the
  Section 6.3 max-log demapper; QPSK from Table C.1.1.
- `twisted_block.py` - A/322 7.1.5.4 HTI twisted block interleaver, gated on
  the A/327 Figure 6.5 worked example (exercises the virtual-block skip rule
  the air does not).
- `payload.DataPlpChain` - demap -> bit de-interleave -> LDPC -> BCH ->
  descramble; `decode_data_plp` / `decode_subframe0_plp` add HTI and the
  generic MODCOD dispatch.
- `cell_interleaver.CellInterleaver` (A/322 7.1.5.2) - the optional per-FEC
  block cell permutation, reset each TI block, gated on the spec's own
  printed `P(r)` vector (N_cells 10800, N_d 14).
- Receiver/CLI: `decode_plp_payload`, `--plp`, payload reporting.

## Link/network layer (2026-09-26)
- `baseband.split_baseband_packet` (A/322 5.2.2): MODE/pointer/OFI and the
  optional/extension fields; the real PLP-16 packet is padding-only.
- `alp.parse_alp` (A/330 5.1): single (short/long, SID), segmentation
  reassembly, concatenation, and the link-layer signalling header.  Baseband
  Packet pointers resynchronise the walk and resyncs are counted.
- `ip.IpReassembler` (RFC 791/768) and `ip.parse_lls` (A/331 6.1).
- `payload.decode_streams` / `receiver.decode_plp_streams` run the whole path.

## Gate
The chain decodes reference-encoded cells **bit-exact** for QPSK 2/15, 16QAM
11/15, 64QAM 6/15 and 11/15, 256QAM 3/15 and 11/15, and the HTI NTI=2
round trip (74-block shape) is exact.  A compact oracle fixture
(`plp0_hti_*`) locks the HTI path in the test suite; a second
(`plp0_hti_ci_*`) locks the cell-interleaver path, and decoding it with the
flag off is asserted to fail so the fixture cannot pass vacuously.  The ALP
layer is gated on spec-derived header writers for every payload
configuration, and the Baseband Packet header is gated on the live PLP-16
packet.

## PLP 0 (64QAM-NUC 11/15) is link-limited, not broken
RF33's PLP 0 is the large video PLP: 64QAM-NUC 11/15, NTI 2, 74 FEC blocks.
It decodes 0/74 in **both** this receiver and the independent reference on
every capture we hold.  Measured MER is ~15.9 dB against the ~18.8 dB AWGN
threshold for that MODCOD, so the captures are ~3 dB short.  The chain is
proven on synthetic cells at that MODCOD, so the failure is the channel, not
the implementation.

## Remaining
- ROHC decompression (A/330 §6) for compressed-IP streams.
- ROUTE/MMTP -> media (A/331/A/344), and the 64800-bit (normal frame) codes.
- A capture with ~19 dB+ PLP-0 MER to close PLP 0 on air.

## See Also
- [[consolidation]]
- [[l1-detail-real-air]]
- [[preamble-demod-toolchain]]
