---
created: 2026-08-30
updated: 2026-08-30
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [hardware, sdr, rf, hackrf]
---

# HackRF One

## Overview

HackRF One is a software-defined radio (SDR) platform recommended for the OpenATSC3 project. It provides sufficient bandwidth and frequency coverage for ATSC 3.0 broadcast reception.

## Specifications

| Parameter | Value |
|-----------|-------|
| Frequency Range | 1 MHz - 6 GHz |
| Bandwidth | 20 MHz (maximum) |
| Sample Rate | Up to 20 MS/s |
| Resolution | 8-bit ADC |
| Interface | USB 2.0 |
| Transmit Capable | Yes |
| Price | ~$320 |

## Why HackRF for ATSC 3.0

**Pros:**
- ✓ Full frequency coverage (ATSC 3.0 uses VHF/UHF bands)
- ✓ 20 MHz bandwidth sufficient for the 6 MHz ATSC 3.0 channel
- ✓ GNU Radio integration
- ✓ Open-source hardware (aligns with project values)
- ✓ Transmit capable (bonus for testing)

**Cons:**
- ✗ $320 cost
- ✗ Higher noise floor than premium SDRs (USRP)

## Comparison: HackRF vs RTL-SDR

| Feature | HackRF One | RTL-SDR |
|---------|-----------|---------|
| Price | $320 | $35 |
| Bandwidth | 20 MHz | 2.4 MHz |
| Frequency Range | 1 MHz - 6 GHz | 24 MHz - 1.7 GHz |
| ATSC 3.0 Suitable | ✓ Yes | ✗ No — 2.4 MHz < 6 MHz channel |
| Transmit | ✓ Yes | ✗ No |
| Open Source | ✓ Yes | ⚠ Partial |

**Verdict:** HackRF is the supported device. RTL-SDR cannot capture a whole
6 MHz ATSC 3.0 channel, so it **cannot decode ATSC 3.0** and is not supported.

## Shopping List

**Supported path:**
- HackRF One: $320
- Dipole antenna: $40
- USB cable: $10
- USB hub (optional, for reliability): $30
- **Total: ~$400**

## Software Integration

**Python Libraries:**
- `hackrf>=0.1` - Official HackRF control
- `osmosdr` - Alternative backend

**GNU Radio:** Native integration for block diagram processing

**Testing:**
```bash
# Frequency sweep test
hackrf_sweep

# Simple receive test
hackrf_transfer -r test.iq -f 617000000 -s 20000000
```

## See Also

- [[openatsc3-project-plan]] - Hardware requirements section
- [[atsc3-physical-layer]] - Signal processing needs

## References

- OPENATSC3_PROJECT_PLAN.md
- [HackRF One Product Page](https://greatscottgadgets.com/hackrf/)
