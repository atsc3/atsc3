# BCH(16200) Outer Code

## Date
2026-09-25

## Why
L1-Basic/L1-Detail and the PLP FEC chain all concatenate LDPC with a BCH outer
code. The previous `BCHDecoder` was a stub (returned the first k bits), which
blocked L1 decoding. This implements the real codec.

## Implementation: `atsc3lib/bch.py`

Exact 12-bit-correctable binary BCH per A/322 Section 6.1.2.1, Table 6.3.

- **Ninner=16200**: GF(2^14), generator `g = g1..g12`, `Mouter=168`. Primitive
  poly `g1 = x^14+x^5+x^3+x+1`. Full code is primitive (16383, 16215).
- **Ninner=64800**: GF(2^16), `Mouter=192` (built for completeness).
- Component polynomials stored as **exponent lists** from Table 6.3 (avoids
  hand-transcription errors), then converted to integers.

### Key spec detail (source of a bug)
A/322 defines the codeword directly as a *shortened* code:
`s(x) = m(x) x^Mouter - p(x)`, length `Nouter = Kpayload + Mouter`.
The earlier attempt incorrectly used the full primitive length for the
syndrome/Chien search. Using `Nouter` directly is correct and much faster.

### API
- `BCHCode(n, t).encode(message_bits)` → `Kpayload + Mouter` bits.
- `BCHCode(n, t).decode(received_bits)` → `(message_bits, num_errors, success)`.
- `BCHDecoder` in `ldpc.py` now wraps this: `decode(bits)` → `(bits, success)`.

### Decoder algorithm
1. Syndromes `S_j = r(alpha^j)`, `j=1..2t`.
2. Berlekamp-Massey → error locator `sigma(x)`.
3. Chien search over the shortened codeword indices.
4. Flip located bits, re-check syndromes (reject if still nonzero).

## Tests: `tests/test_bch.py` (18 passing)
- Parameters (Mouter 168/192, primitive check), parity length, too-long input.
- Clean decode; corrects 1/6/12 errors for Kpayload 200-10000.
- L1-Basic block size (200+168).
- 16-error patterns are detected/rejected (no false success in trials).
- 64800 variant corrects 12 errors.

## Integration
- `BCHDecoder` delegates to `BCHCode`; `ATSC3FECDecoder` now requires BCH
  success for overall success.
- Exported `BCHCode` from `atsc3lib`.

## Status
✓ BCH(16200) encoder/decoder implemented and validated
✓ BCH(64800) supported
✓ Wired into `BCHDecoder` / `ATSC3FECDecoder`
✓ Full suite: 255 passed

## Next
1. L1-Basic decode chain: scramble → BCH → zero-pad to Kldpc=3240 → Type A
   LDPC → group-wise parity perm (Table 6.21) → repetition (Mode 1) →
   puncturing (Table 6.24).
2. Real capture: resample 6.912→6.144 MHz, bootstrap detect, frame sync.
3. L1-Detail.

## See Also
- [[bootstrap]]
- [[exact-ldpc-and-group-interleaver]]
- [[l1-signaling-implementation]]
