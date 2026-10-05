# L1-Basic Signaling FEC

## Date
2026-09-25

## Why
L1-Basic carries the frame preamble parameters (FFT/GI/pilot, L1-Detail FEC
type, L1-Detail size). Decoding it is the step between bootstrap acquisition
and L1-Detail (which holds the per-PLP MODCOD).

## Implementation: `atsc3lib/l1_basic.py`

Exact A/322 Section 6.5.2 chain for the 200-bit L1-Basic block:

```
200 info bits
  -> scramble (poly x^16+x^13+x^12+x^11+x^7+x^6+x^3+x+1, seed 0x18F, taps D7..D0)
  -> BCH(16200) encode            -> Nouter = 368 (200 + 168 parity)
  -> zero-pad to Kldpc = 3240     -> shortening pattern pi_s = [4,1,5,2,8,6,0,7,3]
  -> 16K Type A LDPC, rate 3/15   -> 16200-bit codeword
  -> group-wise parity permutation (Table 6.21, groups 9..44)
  -> puncturing (Table 6.24)  and repetition (Mode 1)
```

Key facts confirmed from the spec:
- **All L1-Basic modes use LDPC rate 3/15**, because Kldpc = 3240 = 16200*3/15.
  Modes differ only in constellation (`eta`) and puncturing (and Mode 1
  repetition).
- Puncturing (Steps 1-4): `Npunc_tmp = A*(Kldpc-Nouter)+B`,
  `Nfec_tmp = Nouter + 12960 - Npunc_tmp`, `Nfec = floor(Nfec_tmp/eta)*eta`,
  `Npunc = Npunc_tmp - (Nfec_tmp - Nfec)`.
- Mode 1 repetition `Nrepeat = 3672`.

The resulting transmitted bit counts reproduce A/322 Table 6.17 cell counts
exactly for all 7 modes:

| Mode | eta | Cells (Table 6.17) |
|------|-----|--------------------|
| 1 | 2 | 3820 |
| 2 | 2 | 934 |
| 3 | 2 | 484 |
| 4 | 4 | 259 |
| 5 | 6 | 163 |
| 6 | 8 | 112 |
| 7 | 8 | 69 |

### Decoder notes
- Punctured parity is re-inserted as erasures (LLR 0); known-zero shortened
  info positions get a confident negative LLR.
- The LDPC decoder often reports `converged=False` because punctured parity is
  unknown; **BCH success is the definitive criterion** (`decode` returns
  `bch_ok`).

## Tests: `tests/test_l1_basic.py` (18 passing)
- Randomizer first values (1100 0000 0110 1101...) and self-inverse.
- All 7 modes clean roundtrip; cell counts match Table 6.17.
- Mode 1 repetition present; shortening mask roundtrip; noise tolerance.

## Status
✓ L1-Basic encode/decode implemented for all 7 modes
✓ Transmitted lengths validated against Table 6.17
✓ Full suite: 273 passed

## Remaining for Path B
1. Real capture: resample 6.912 → 6.144 MHz, bootstrap detect, frame sync,
   extract L1 cells, decode L1-Basic.
2. L1-Detail decode (Type B LDPC, segmentation, BCH) → per-PLP MODCOD.
3. Wire bootstrap + L1 into `atsc3-decode`; fix pilot PRBS.

## See Also
- [[bch-outer-code]]
- [[bootstrap]]
- [[exact-ldpc-and-group-interleaver]]
- [[l1-signaling-implementation]]
