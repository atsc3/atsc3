---
created: 2026-08-30
updated: 2026-08-30
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [atsc3, physical-layer, ofdm, signal-processing]
---

# ATSC 3.0 Physical Layer

## Overview

ATSC 3.0 uses OFDM (Orthogonal Frequency Division Multiplexing) for physical layer transmission. The signal processing pipeline converts raw IQ samples from an SDR into decoded bits through multiple stages.

## Processing Pipeline

### 1. OFDM Demodulation (Weeks 1-2)

**Input:** Raw IQ samples from an SDR wide enough for the 6 MHz channel (SDRplay; RTL-SDR is not supported)

**Steps:**
- Time synchronization using cyclic prefix correlation
- Frequency synchronization (coarse/fine correction)
- Cyclic prefix removal
- FFT → 8K complex values per symbol (subcarrier symbols)

**Output:** Constellation points

**Test:** Plot constellations from real broadcast reception

### 2. Channel Estimation & Equalization (Weeks 3-4)

**Input:** Subcarrier symbols from OFDM demod

**Steps:**
- Pilot carrier extraction (scattered pilot pattern per A/322 spec)
- Channel response estimation in frequency domain
- Subcarrier equalization (divide by channel estimate)
- Phase tracking across symbols

**Output:** Equalized symbols ready for QAM mapping

**Test:** Compare constellation SNR before/after equalization

### 3. QAM Demodulation (Weeks 5-6)

**Input:** Equalized symbols

**Steps:**
- Constellation mapping (QPSK, 16-QAM, 64-QAM, 256-QAM)
- Soft-decision decoding (LLR generation for each bit)
- Symbol to bit conversion
- Handle multiple PLP (Physical Layer Pipe) streams

**Output:** Bit stream with reliability metrics (LLRs)

**Test:** BER measurement vs SNR

### 4. LDPC Decoding (Week 7)

**Input:** LLRs from QAM demod

**Steps:**
- Load ATSC 3.0 LDPC parity check matrices from spec
- Belief propagation decoding (iterative)
- Handle multiple code rates (2/3, 3/4, 4/5, etc.)
- FEC convergence detection

**Output:** Decoded FEC blocks (error-corrected bit blocks)

**Test:** FEC block convergence rate measurement

### 5. Framing & Signaling (Week 8)

**Input:** Decoded FEC blocks

**Steps:**
- Bootstrap sequence parsing (initial signal acquisition)
- L1 detail signaling extraction
- LLS (Low-Level Signaling) table parsing
- PLCF (Physical Layer Configuration) extraction

**Output:** Structured signaling data, service configuration

**Test:** Parse real broadcast signaling tables

## Key Algorithms

### Time Synchronization

Uses cyclic prefix correlation: CP is a copy of the last part of each OFDM symbol, creating a peak in the autocorrelation at symbol boundaries.

### Frequency Synchronization

Two-stage process:
1. **Coarse:** Estimate from CP correlation phase rotation
2. **Fine:** Use pilot subcarriers for residual correction

### Channel Estimation

Scattered pilots form a grid in time-frequency domain. Interpolate (2D) to estimate channel at all subcarriers.

### Soft-Decision Decoding

Calculate Log-Likelihood Ratios (LLRs) for each bit based on distance to constellation points. More reliable bits have higher magnitude LLRs.

## Performance Considerations

**Pure Python:** Feasible for initial implementation, especially with numpy/scipy (which use optimized BLAS/FFTPACK)

**C bindings may be needed for:**
- LDPC belief propagation (if pyldpc too slow)
- QAM LLR calculation (if performance critical)

**Real-time targets:**
- OFDM, equalization, QAM: Easy in Python
- LDPC: 1-1.5x real-time with optimization
- Overall: ~1.0x real-time on 6-core CPU

## See Also

- [[openatsc3-project-plan]] - Full project plan
- [[atsc3-certificate-validation]] - Certificate validation (Phase 2)

## References

- OPENATSC3_PROJECT_PLAN.md
- ATSC A/322 Physical Layer Specification
- ATSC A/331 Signaling Specification
