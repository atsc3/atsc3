# Exact LDPC Codec & Group Interleaver

## Date
2026-09-25

## Overview
Wired the previously-orphaned exact A/322 LDPC codec and the official group
interleaver into the library, fixed a broken decoder, and added a full decode
CLI (`atsc3-decode`).

## What was already on disk (unwired)
- `atsc3lib/data/ldpc_tables_N16200.json` - official A/322 Annex A parity
  tables for N=16200 (all 12 rates).
- `atsc3lib/ldpc_exact.py` - encoder + decoder, but **not imported anywhere**
  and the decoder was broken (see below).
- `atsc3lib/data/group_interleaver_B2.json` - group interleaver tables, also
  unused. Keying: top level = modulation (1=QPSK, 2=16QAM, 3=64QAM,
  4=256QAM), second level = code rate (2..13). Verified against the reference
  implementation `drmpeg/gr-atsc3` (`interleaver_bb_impl.cc`).

## Fixes

### 1. `ldpc_exact.py` decoder was non-functional
The min-sum check-node update was malformed and failed even on clean
codewords for most rates. Rewrote `decode()` as a correct normalized min-sum
(normalized min-sum with `alpha=0.75`), converting between the library LLR
convention (`LLR>0 => bit 1`) and the conventional min-sum domain.

Test result on synthetic codewords (rate 4/6/10, 3% raw BER):
`converged=True`, recovered info identical to original.

### 2. Group interleaver implemented
New `atsc3lib/group_interleaver.py` implements the full A/322 Section 6.3
chain for N=16200:
1. Parity interleaver (Type B rates only, `Qldpc` accumulator).
2. Group interleaver using the official 45-entry permutation tables.
3. Block interleaver (Type A with `nr2` tail; Type B with `npart2` tail).

Block type per (rate, modulation) cross-checked against `gr-atsc3`.
`interleave()`/`deinterleave()`/`deinterleave_llrs()` are exact inverses.

## New CLI
```
atsc3-decode --file capture.iq --modulation QPSK --rate 6 [--no-descramble]
```
Runs OFDM → equalize → soft QAM → group deinterleave → exact LDPC →
PRBS descramble, per codeword, and reports convergence.

## Tests
- `tests/test_ldpc_exact.py` - params, encoder syndrome, clean + noisy decode.
- `tests/test_group_interleaver.py` - table integrity, roundtrip, end-to-end
  interleaved decode.
- Full suite: **218 passed** (was 145).

## Remaining Work
1. **Determine actual MODCOD from L1 signaling** - the broadcast config is
   still guessed; `atsc3-decode` must consume `L1SignalingParser` output.
2. **Run on real WIAV-CD capture** - synthetic chain works; real convergence
   still blocked on (1) and on symbol-detection speed.
3. **Symbol detection optimization** - current O(n²) makes full-capture runs
   take minutes (see [[optimization-symbol-detection]]).
4. **L1 FEC decode** - L1 signaling itself uses LDPC; bootstrap parser is a stub.

## Status
✓ Exact LDPC decoder fixed and validated
✓ Group interleaver implemented and validated against A/322 tables
✓ Full decode CLI added
✓ 218/218 tests pass
⚠ Real-broadcast convergence still needs the true MODCOD from L1

## See Also
- [[ldpc-integration-complete]]
- [[bit-deinterleaver-descrambler]]
- [[integration-test]]
- [[l1-signaling-implementation]]
