# ATSC 3.0 Receiver - Project Summary

## Status: Physical Layer + First Payload Bit ✓

As of 2026-09-25, the ATSC 3.0 software receiver demodulates real off-air
broadcasts, decodes all L1 signalling, and extracts a real data-PLP payload
(Baseband Packets) with error correction.

## Hardware Setup

- **SDR:** SDRplay RSP1B (10 MS/s, via the `sdrbindings` SoapySDR extension)
- **Antenna:** Dipole
- **Location:** Rockville, MD (Washington DC DMA)
- **Signal:** RF33 / 587 MHz (WHUT ATSC 3.0 mux)
- **Note:** ch30/ch36 are ATSC 1.0 (8VSB); RF33 is the true ATSC 3.0 mux.
  The HackRF was retired (no longer functional); the RSP1B's wider dynamic
  range lifted PLP-0 to 74/74.

## Validated Chain

```
raw IQ -> resample 6.144M -> bootstrap (structure, fine CFO)
       -> resample 6.912M -> Preamble (FFT, channel est, freq de-interleave)
       -> L1-Basic FEC+parse -> L1-Detail FEC+parse -> per-PLP config
       -> subframe cell pool (pilots, frequency interleaver, SBS)
       -> PLP slice -> NUC/QAM demap -> bit de-interleave
       -> HTI twisted block de-interleave -> LDPC -> BCH -> descramble
       -> Baseband Packets
```

Every stage is gated against an independent receiver and/or A/322 worked
examples and test vectors.

## Test Results

| Component | Input | Output | Status |
|-----------|-------|--------|--------|
| Bootstrap | 10 MS/s IQ | structure 27, CFO | ✓ Working |
| Preamble | 8K symbol | 4851 cells | ✓ Working |
| L1-Basic | 484 cells | 200 bits, CRC OK | ✓ Working |
| L1-Detail | 880 cells | per-PLP config, CRC OK | ✓ Working |
| Cell pool | frame | 211472 cells | ✓ Working |
| PLP-16 (QPSK 2/15) | 8100 cells | 1 Baseband Packet | ✓ Working |
| PLP-0 (64QAM-NUC 11/15) | 199800 cells | 74/74 + SLT/SystemTime | ✓ Working (SDRplay) |
| Normal-frame FEC (64800) | A/322 Annex A.1/B.1 | all 12 rates, 4 MODCODs | ✓ Working |
| Subframe 1 (16K/SP4_4) | L1-D geometry | cell pool 956179, FI reset | ✓ Tables match reference |
| PLP-1 (256QAM-NUC 11/15, 64800) | 947700 cells | 117/117 + 358 UDP datagrams | ✓ Working (SDRplay + dense CH) |
| PLP-1 fixture gate (2026-10-05) | `tests/data/rf33_sf1_y.npy` | pool 956179, **117/117** | ✓ Decode-gated (23.0 dB fresh record) |

**RF33 is the lighthouse and it decodes.**  PLP-0 (64QAM-NUC 11/15) converges
53-60 of 74 FEC blocks off air and yields the A/331 **SLT** (`bsid="540"`,
majors WHUT/WJLA/WTTG/WRC/WUSA) and SystemTime.  PLP-16 (QPSK 2/15) is
byte-identical to the independent receiver.  **This retracts the earlier
"padding-only, 64QAM unachievable" verdict**: the blocker was a demapper
**scale** bug — the A/322 NUC alphabets are unit-power but the equaliser output
is not, and the max-log metric is not scale-free.  Fixed in
`DataPlpChain.decode_cells` (normalise each FEC block to unit mean power);
raw 0/74 → normalised 53-60/74.

**PLP-1 (256QAM-NUC 11/15) now decodes 117/117 off air (2026-10-01).**  The
earlier "21.2 dB MER, above the 20 dB synthetic threshold, therefore a chain
defect" reasoning was a **units error**, retracted: nearest-point MER is **not**
Es/N0.  Two changes together closed it: (1) the SDRplay RSP1B front end reads
subframe 1 at **25.8 dB nearest-point MER** (the old HackRF fixture was 20.6 dB);
and (2) the data-PLP channel estimator now merges the ``DY`` scattered-pilot
subsets of ``DY`` consecutive symbols into a ``DX``-spaced grid
(`dense_symbol_channel`, `payload.py`), resolving the frequency-selective
(multipath) residual the single-symbol linear interpolator left behind.  The
measured channel ripple was |H| std 0.19 (norm 0.85-2.64); a synthetic static
channel with that ripple read 23.0 dB MER and decoded 0/39 under the old
estimator, while pure AWGN at 22.9 dB decoded 39/39 — nearest-point MER
under-reports multipath residual for 256QAM.  **The limiter was equaliser
residual (multipath), not raw SNR.**  PLP-1 now yields 358 UDP datagrams.
`decode_subframe_plp` defaults ``dense=True``.

**The subframe-1 fixture was replaced 2026-10-05.**  A fresh SDRplay RSP1B
record (`out/recapture/rf33_rsp1b_05.iq`, 587 MHz, IFGR 40 / RFGR 4, 8 s) reads
subframe 1 at **23.0 dB nearest-point MER and decodes 117/117**.  The prior
`tests/data/rf33_sf1_y.npy` (the 20.6 dB recording) measured **21.5 dB and
decoded 0/117** — ~1.5 dB short of the oracle's 21.7-22.9 dB cliff — but its
tests gated **geometry only**, so the shortfall was silent.
`tests/test_subframe1.py::TestSf1Air::test_plp1_decodes` now asserts 117/117 on
the new fixture, so a too-weak recording fails loudly.

## Scope

**Ground rule: every rung must be validated on air.**  A feature is only
implemented when a receivable stream carries it; a structural or synthetic
round-trip gate does not qualify a rung.  If the link cannot deliver the
feature, record the blocker and move on — do not build an unprovable stage.

An earlier decision declaring RF33's 64QAM payloads "unachievable, deferred for
high-order margin" has been **retracted** — it was a receiver bug, not a link
limit.  Still implemented-and-gated but not air-demonstrated: LDM/CTI (RF30's
core and RF25).  256QAM is **now demonstrated off air** (PLP-1, 117/117).

## Unit Tests

- **637 tests** across `tests/` — **all passing** (as of 2026-10-04)
- Includes LDPC (exact A/322, short and normal frames), BCH, the bit
  interleaver (Annex B, both frame lengths), HTI twisted block (A/327 gold
  vector), NUC tables, the A/330 ALP/IP layer, the 16K pilot/data-cell tables
  (constant-data-carrier identity gate), the CTI index map / LDM power tables,
  the fine-timing and CPE front-end stages, and real-air fixtures for L1, the
  PLP-16 payload and the subframe-1 pool

## Recent Milestones (2026-09-26)

- **Real-air L1** — bootstrap + Preamble + L1-Basic/L1-Detail match an
  independent receiver exactly (BSID 540, PLPs 0/16/1)
- **Data-PLP payload chain** (`payload.py`, `nuc.py`, `twisted_block.py`) —
  generic QPSK/16QAM/64QAM/256QAM, HTI TI mode 2, short frames
- **HTI cell interleaver** (`cell_interleaver.py`, A/322 7.1.5.2) — optional
  per-FEC-block permutation, gated on the spec's printed shift vector
- **Link/network layer** (`baseband.py`, `alp.py`, `ip.py`) — A/322 5.2.2
  Baseband Packet headers, A/330 ALP de-encapsulation (single, segmentation,
  concatenation, signalling), IPv4 reassembly, UDP, A/331 LLS
- **Live-path fixes** — guard-interval signalling value was used as a list
  index (6 -> 2048 instead of 1536); bootstrap fractional-CFO de-rotation was
  missing. Both fixed; live PLP-16 now decodes
- **`atsc3-decode` CLI** — signalling + optional PLP stream decode
- **Normal-frame FEC (Ninner=64800)** — A/322 Annex A.1 parity tables and
  Annex B.1 group-wise permutations machine-extracted via `tools/` (PyMuPDF)
  with closed-arithmetic gates, and cross-checked against an independent public
  transcription (`drmpeg/gr-atsc3`, pinned); decoder + bit interleaver now cover
  both frame lengths, all 12 rates, all 4 NUC modes
- **Subframe 1 (16K FFT)** — `build_data_symbol_pool` generalises the cell pool
  to any FFT/GI/pilot/cred; subframe geometry resolved from L1-D; the A/322 7.3
  frequency-interleaver counter resets at the subframe boundary (origin 0). The
  per-FFT pilot/data-cell tables (Tables 7.3-7.6, Annex F, D.1.4/D.1.5) are
  fetched from the pinned transcription and gated by the constant-data-carrier
  identity. Live 16K pool closes at exactly 956179 cells
- **L1 signalling Mode-1 repetition** (A/322 6.5.2.7) — the transmitted word is
  `[info][repeated parity][punctured tail]`; both L1-Basic and L1-Detail mode-1
  codecs fixed. Gated on Table 6.17's printed 3820 cells and RF30's signalled
  3611 L1-Detail cells. **RF30's L1-Basic now verifies off air**
- **Multi-symbol Preamble** (A/322 7.2.5) — NP Preamble symbols share the
  bootstrap geometry; L1-Detail spills from the first symbol into the rest and
  is block-de-interleaved with Lc = NP columns (7.2.5.2). The FI symbol counter
  keeps counting frame symbols, so subframe 0's first data symbol has origin NP.
  Gated on RF30 (**L1-Basic + L1-Detail BCH + CRC OK**, BSID 9100) and on the
  signalled/derived 3708-cell identity
- **Fine timing + per-symbol CPE** (`payload.py`) — two front-end stages the
  oracle's core path applies that the RF33-class path never needed. Fine timing
  pins the FFT window by scattered-pilot coherence (A/322 8.1.3.1); the CPE
  fits each OFDM symbol's residual complex gain decision-directed against the
  PLP's own alphabet, using the A/322 7.2.6.5 dummy tail where present. Gated
  synthetically (a known shift and per-symbol phase are recovered) and on real
  air (the RF33 frame fixture decodes identically with and without them)
- **LDM / CTI structural chain** — `cti.py` implements the A/322 7.1.4
  convolutional time interleaver and its 9.3.9.1 signalled identity; `spec.py`
  adds Table 9.24 ``Nrows`` and the Table 9.22/6.15/6.16 LDM power ratios. The
  air gate (only the signalled Nrows solves ``C`` inside a FEC block), the
  commutator-continuity gate and the interleave/de-interleave round trip are
  all covered by `tests/test_cti.py`

## Second track: own PKI / content protection (greenfield)

A **separate product track** has been opened alongside the receiver:
`openatsc3-pki`, a greenfield certificate authority plus its own content
protection scheme.  It **competes with A3SA** and runs **alongside** it as a
parallel trust anchor.  The standards (A/331 §5.9/§6.7, A/360 §5.2.2) define
the certificate formats and validation rules — the LLS SignedMultiTable
(`0x07`, CMS `SignedData`), the mandatory CertificationData (`0x06`, carrying
the chain and **stapled OCSP**), ECDSA P-256/P-384 with ATSC OIDs under
`1.3.6.1.4.1.51552` and EKU `id-atsc-kp-signalingSigning` — but nothing in
them says who must own or operate the CA.  That open field is the whole point
of the track.  **No Widevine, no A3SA**: the content protection uses our own
DRM scheme and UUID (ISO/IEC 23001-7 CENC compatible).  The receiver is
configured to trust our root only (fail-closed).

**Market reality (2026-10-05).**  This is *not* a consumer-broadcast replacement
for A3SA and will not become one: broadcast DRM is a **two-sided market** A3SA
already won (broadcasters sign to a root devices trust, and devices add a root
only when broadcasters demand it), and consumer playback additionally needs a
**trusted sink** that a network gateway cannot provide.  SiliconDust's HDHomeRun
— Widevine-licensed (2022), DTCP2-approved, NextGen- and A3SA-certified — still
has no approved DRM gateway path; encrypted channels fall back to their ATSC 1.0
version.  The hardware/software split matters: the CA *authority* can be
software (this repo), but content-key *use* must be hardware-rooted (Gap D), so
a device is realistically **PHY ASIC + DRM secure element**, with software as
middleware/CA.  The track's real value is a standards-compliant inspectable
reference, own-content / private trust (where we are both signer and sink),
option value, and the SDR receiver as a lab instrument.  Full frame + sources:
[[atsc3-drm-gateway-trust-boundary]].

This rung is exempt from the on-air ground rule: no receivable stream carries
our own signature or DRM, so it is gated **synthetically at the A/331 layer**
(generated CDT/SMT/signed-SLS through `ip.parse_lls` → `slt_from_lls` →
`mmtp`/`route`/`media`) with negative gates (tamper, expiry, revocation,
wrong-root, missing EKU).  Plan and spec grounding: see
`wiki/analyses/own-ca-and-content-protection.md`.

**Status (2026-10-04):** the greenfield repo `openatsc3-pki/` implements the
root/issuing CA, the A/360 5.3.1.6 signaling-signer profile
(`id-atsc-kp-signalingSigning`, `id-atsc-sdattr-bsid`), OCSP responder +
stapled OCSP, detached CMS `SignedData` (A/360 5.2.2.1), the CertificationData
LLS table (0x06, gzipped XML) and the LLS SignedMultiTable (0x07), plus a
receiver-side verifier enforcing A/360 5.2.2.6, the own-CENC content-protection
scheme (`content.py`: AES-128 CTR, `pssh`/`tenc`/`senc`/`schm`, MPD
`ContentProtection` with our own DRM UUID, A/331 Table 7.32 security
descriptor).  The `id-atsc` arc is `1.3.6.1.4.1.51552`.  44 tests pass,
including every negative gate and the CENC round trip.  Verified against the
published ATSC CDT example as an oracle.
**Receiver integration done:** `atsc3lib/security/` is a **pluggable provider
seam** (`base.SecurityProvider` protocol + registry; `openatsc3.py` the default
provider) so the CA/DRM backend can be swapped for A3SA/Widevine without
touching the receiver; verdicts attach to `DecodedStreams.verification` and
`atsc3-decode` gained `--trust-root` / `--security-provider` /
`--require-signature`; `atsc3lib/tests/test_security.py` and
`test_security_provider.py` gate it.
**Gap register + Gap A closure plan** are recorded in
`wiki/analyses/own-ca-and-content-protection.md`.  Gap A (operation lifecycle:
persistent revocation, real `revoke`, key rollover/renewal) closes with a new
**Django + PostgreSQL CA operator application, `openatsc3-ca`**: PostgreSQL is
the source of truth, keys stay on disk (0600, DB holds path + fingerprint), PEMs
are materialized on `export`, admin-only v1, and the first data migration
imports the existing CA tree.  It depends on the now dependency-light
`openatsc3-pki` crypto library (only `cryptography` + `asn1crypto`).  Gaps B–F
(ROUTE/MMTP SLS signing, receiver content decryptor, HSM/offline root) remain
open.
**Gap A Phase 1 is done (2026-10-04):** the app lives in `openatsc3-ca` as
`openatsc3_ca/` + the `openatsc3_ca.catalog` app (label pinned to `catalog`, so
the migration history is unchanged); PostgreSQL is authoritative, keys stay on
disk, `revoke` now persists and turns the stapled OCSP to `revoked`, and
`export`/`import-tree` move the DB and PEM tree in both directions.
Migrations `0001`–`0003` (incl. the spec seed and the legacy-tree data
migration, idempotent + reversible).  Tests: `make test-ca` = 11 pass
(embedded rootless PostgreSQL 16.2 via `pgserver`, no Docker needed; a
`docker-compose.yml` is provided for deployment), including the end-to-end gate
where a revoked signer makes the receiver's `verify_certification_data` fail;
`make test-pki` (44) stays database-free.  Phase 2 (rollover/renew, `publish`)
and Phase 3 (content keys, DRF, HSM) remain.

### High Priority
1. **ROUTE/MMTP -> media** (A/331/A/344) — **implemented and validated
   (2026-10-02)**: a saved 3 s RF33 capture reassembles to fragmented-MP4
   (MMTP MPU + ROUTE/LCT objects) and decodes to 1920x1080 HEVC (WETA's
   *Amanpour & Co.* on WHUT).  The reassembly now lives in `atsc3lib`
   (`mmtp.MmtpFlow`, `route.RouteAssembler`, `media.py`) and its output is
   byte-identical to the independent reference receiver's.  Two library bugs
   closed: `frame_samples` summed only subframe 0 (364,032 samples) instead of
   the whole frame (1,708,032 = 247.1 ms, measured on air), and the MMTP
   version-1 `ver_ext` field was not parsed (the header is 14 bytes, not 12;
   the signaling payload header is 2 bytes, not 4).  **Update 2026-10-04:**
   AC-4 audio now decodes off air (`ac4bindings` + `audio.py`) and `mux.py`
   writes one combined A/V MP4 (HEVC stream-copied + AAC-LC from the AC-4 PCM),
   offline and live; only **live real-time throughput** remains compute-bound.

### Deferred (blocked by the air census, not difficulty)
- **ROHC decompression** (A/330 §6) for compressed-IP streams — RF33's decoded
  PLPs are **all IPv4**: a `packet_type` census found 477/477 ALP packets
  `000` (PLP-0 78, PLP-1 399, PLP-16 0), so no receivable stream signals the
  compressed-IP `010`.  Out of scope until one does.

### Medium Priority
3. **LDM / CTI payloads** — RF30/RF25 carry two LDM layers with convolutional
   time interleaving (A/322 7.1.4) and Ninner = 64800; RF30's PLP-1 (64QAM-NUC
   6/15) is the in-scope payload target. The structural chain (CTI index map,
   LDM power tables, fine timing, CPE) is implemented and gated on RF33; the
   remaining work is RF30-specific link margin
4. **Symbol-detection optimization** — Numba JIT
5. **Video/Audio decode** — **done (2026-10-04)**: HEVC video builds to a
   playable MP4; AC-4 audio decodes (`ac4bindings`); `mux.py` combines both
   into one A/V MP4.  The open item is live-rate throughput, not a codec.

### Low Priority
6. **GUI** — real-time constellation display
7. **Network streaming** — UDP output
8. **Multi-CA support** — certificate validation

## File Structure

```
atsc3lib/
├── atsc3lib/            # Python library (one validated chain)
│   ├── frontend.py      # resample + frame acquisition
│   ├── bootstrap.py     # bootstrap detect + fine CFO
│   ├── preamble.py      # preamble channel est / equalize
│   ├── frequency_interleaver.py, pilot_reference.py
│   ├── l1_basic.py, l1_detail.py, l1_signaling.py
│   ├── crc.py, bch.py, ldpc_exact.py, group_interleaver.py
│   ├── nuc.py, twisted_block.py, cell_interleaver.py, payload.py
│   ├── pilot_tables.py  # per-FFT pilot/data-cell tables (Annex D/F)
│   ├── baseband.py, alp.py, ip.py   # A/322 BBP, A/330 ALP, IPv4/UDP/LLS
│   ├── receiver.py      # decode_signaling / decode_plp_streams
│   └── cli.py           # atsc3-decode
├── tools/               # table fetchers + extractors (PyMuPDF) + survey tools
├── tests/               # 464 tests incl. real-air fixtures
└── pyproject.toml
```

## CLI Tools

```bash
atsc3-capture    # capture from SDR
atsc3-decode <capture.iq> --rate 10e6 --fmt cs8 [--plp 16]
```

## References

- ATSC A/322 Physical Layer Specification
- ATSC A/327 (test vector for the twisted block interleaver)
- Felbs/atsc3 (independent receiver, used as a referee only)
- Unit tests under `atsc3lib/tests/`

---

**Bottom Line:** The receiver decodes L1 signalling and a real data-PLP
payload from live ATSC 3.0 broadcasts, through ALP/IP/LLS.  It supports both
short-frame (16200) and normal-frame (64800) FEC, one- and multi-symbol
Preambles, and both subframes of a 16K frame.  The next rung is MMTP media
(MPU); ROHC is deferred (no compressed-IP `010` on RF33 — 477/477 ALP packets
were IPv4), and the LDM/CTI payload chains are capture-limited.
