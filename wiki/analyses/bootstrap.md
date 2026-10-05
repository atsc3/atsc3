# ATSC 3.0 Bootstrap (Path B: no guessing)

## Date
2026-09-25

## Why
Path A (brute-force MODCOD search) was rejected: it still assumes the front-end
(FFT/GI/pilot/frame timing) is correct. Path B acquires the bootstrap, which
*carries* those parameters, so the receiver stops guessing. This is the correct
forward path.

## Implemented: `atsc3lib/bootstrap.py`

### Generation (transmitter-accurate)
- `generate_bootstrap(structure, major, minor, frame_interval, bandwidth)`
  produces the full 12288-sample waveform (4 symbols x (2048 FFT + 504 B + 520 C)).
- Zadoff-Chu sequence (q=137 major 0, q=197 major 1), length 1499, 275 left nulls.
- PN LFSR per A/322 Table 7.1, seeded by major/minor version (verified against
  the reference `drmpeg/gr-atsc3` `init_pseudo_noise_sequence`).
- Signaling bytes → 11-bit cyclic shift via Gray-code mapping; symbol 0 uses the
  +2π n/2048 pre-rotation, symbols 1-3 use -2π n/2048.
- Symbol 3 inverted (`-1/√1498`).

### Preamble structure decode
- Full 0-159 `preamble_structure` table → (FFT size, guard interval, L1 FEC
  mode, pilot-pattern note). Covers 8K/16K/32K including the pilot-dependent
  32K GI 9/3072 and 10/3648 cases.

### Detection (receiver)
- `detect_bootstrap(iq)` at the native bootstrap sample rate:
  - Slides symbol-0 reference over the buffer for all 16 major/minor
    hypotheses, picks the best correlation.
  - Recovers frame start and major/minor version.
  - Recovers the 3 signaling bytes from relative cyclic shifts of symbols 1-3.
- Returns `BootstrapDetection(start, major, minor, structure, signaling)`.

### Tests: `tests/test_bootstrap.py` (19 passing)
- Shift map injectivity/roundtrip, preamble structure decode, generation length,
  and clean detection for structures 0/6/10/30/90/120/159.

## Key facts extracted from A/322 for the next stage (L1-Basic)
Source: `/tmp/opencode/a322.txt` (extracted from the official PDF).

- L1-Basic input: **200 bits**, same for all modes.
- Scramble → BCH encode → zero pad to `Kldpc=3240`; `Nouter=368` (= 200 + 168
  BCH parity). Zero padding is group-wise `Ninfo_group=9` with shortening
  pattern `πs = [4,1,5,2,8,6,0,7,3]`.
- LDPC: 16K (16200), **Type A**. Tables 6.6: modes 1-4 use `2/15,3/15,4/15,5/15`.
- Parity permutation: parity interleaver is *not* used for L1-Basic; group-wise
  interleave pattern `πp(9..44)` from Table 6.21.
- Repetition: only L1-Basic Mode 1 (`Nrepeat = 2`).
- Puncturing: Table 6.24 (A, B); Mode 1 A=0, B=9360, `Nldpc_parity=12960`.
- L1-Detail: separate, larger job (Type B, BCH, segmentation, 200-6312 bits).

## Consequence / corrected plan
The exact LDPC codec + group interleaver already built apply to the **PLP
payload**, not directly to L1. L1-Basic needs an additional layer:
1. **BCH outer code for Ninner=16200** (shortened, `Mouter=168`). This is a
   *blocker* - the current `BCHDecoder` is a stub. Needed for both L1 and the
   PLP FEC chain in A/322.
2. L1-specific shortening (`Kldpc=3240`), group-wise parity permutation
   (Table 6.21), repetition (Mode 1), and puncturing (Table 6.24).
3. L1-Detail (Type B) is a further separate effort.

## Status
✓ Bootstrap generator + detector implemented and tested
✓ Preamble structure → FFT/GI/L1 FEC mode mapping implemented
✓ Exported from `atsc3lib`
⚠ L1-Basic FEC decode is blocked on the BCH(16200) decoder being written
⚠ Not yet wired into `atsc3-decode` (needs L1 chain + resampling to 6.144 MHz)

## Next Steps (ordered)
1. Implement shortened BCH(16200) encoder/decoder (unblocks L1 and PLP FEC).
2. Implement L1-Basic decode chain using it + existing Type A LDPC.
3. Resample captures from 6.912 MHz to 6.144 MHz; run real bootstrap detect.
4. Then L1-Detail.

## See Also
- [[exact-ldpc-and-group-interleaver]]
- [[l1-signaling-implementation]]
- [[integration-test]]
