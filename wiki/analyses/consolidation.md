# Codebase Consolidation

## Date
2026-09-25

## Why
The codebase had accumulated two generations of modules.  The valid, air-proven
chain (bootstrap -> Preamble -> L1-Basic -> L1-Detail -> PLP config) was mixed
with early-week scaffolding that was never part of the working receiver, and the
public CLI drove the scaffolding rather than the proven chain.

## Removed (Gen-1 scaffolding, superseded and not on the validated path)
- `atsc3_ldpc.py` — early quasi-cyclic matrix generator (random shifts)
- `ldpc.py` — old `LDPCDecoder` / `BCHDecoder` / `ATSC3FECDecoder` / `decode_ldpc`
- `bit_interleaver.py` — simplified zero-twist interleaver (superseded by
  `group_interleaver.py` and the L1 block interleaver)
- `qam.py` — uniform-QAM demod + heuristic `ModulationDetector`
- `ofdm.py`, `pilots.py`, `equalizer.py` — early OFDM/pilot/equalizer path
- Corresponding tests: `test_ldpc`, `test_bit_interleaver`, `test_qam`,
  `test_ofdm`, `test_pilots`, `test_equalizer`

## Added
- `receiver.py` — single validated entry point:
  `decode_signaling(iq, fs)` and `decode_capture(path, fs, fmt)`, returning a
  `ReceiverResult` with L1-Basic, L1-Detail and the flat per-PLP list.
- `cli.py` rewritten around it: one `atsc3-decode` command.  Dropped the stale
  `atsc3-demod/equalize/bits` entry points (they ran the removed scaffolding).

## Result
- 20 library modules, 16 test files, all on one coherent chain.
- `atsc3-decode <capture> --rate 10e6 --fmt cs8` decodes real air and prints the
  per-PLP MODCOD table.
- Full suite: **245 passed** in ~18 s (down from 346/~107 s — the removed tests
  were the slow scaffolding ones).

## Note
The project is now under git (`atsc3lib/.git`); the `initial` commit captures
this consolidated state.

## See Also
- [[l1-detail-real-air]]
- [[ldpc-vectorized]]
