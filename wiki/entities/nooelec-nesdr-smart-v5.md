---
created: 2026-08-30
updated: 2026-08-30
sources: [user-provided]
tags: [hardware, sdr, rtl-sdr, rf]
---

# Nooelec NESDR SMArt v5

## User Device

**Serial Number:** 84077375

**Location:** Input 1 (USB port 1)

## Overview

The Nooelec NESDR SMArt v5 is an RTL-SDR dongle based on the Realtek RTL2832U chipset with an improved R820T2 tuner. It's a budget-friendly SDR option suitable for initial ATSC 3.0 testing, though with limitations compared to HackRF One.

## Specifications

| Parameter | Value |
|-----------|-------|
| Chipset | Realtek RTL2832U + R820T2 |
| Frequency Range | ~24 MHz - 1.7 GHz (varies by model) |
| Bandwidth | 2.4 MHz (sustained) |
| Resolution | 8-bit ADC |
| Interface | USB 2.0 |
| Transmit | ✗ No (receive only) |
| Price | ~$35-45 |

## ATSC 3.0 Suitability

**Not supported.**  Its 2.4 MHz bandwidth is narrower than the 6 MHz ATSC 3.0
channel, so it cannot capture a whole channel and cannot decode ATSC 3.0.

**Pros:**
- ✓ Very affordable ($35-45)
- ✓ Plug-and-play USB
- ✓ Good for spectrum surveys (power scanning only)
- ✓ Wide community support

**Cons:**
- ✗ 2.4 MHz bandwidth narrower than the 6 MHz ATSC 3.0 channel — cannot decode
- ✗ Receive only (no transmit for testing)
- ✗ Lower dynamic range than HackRF

**Verdict:** Not usable for ATSC 3.0.  Use a device wider than 6 MHz
(SDRplay).

## Historical use

This dongle was used for the early band **surveys** (power scans), which is all
it is capable of.  Its narrow capture cannot drive the decoder; only HackRF
captures have ever decoded.  The survey pages below are kept as history.

## See Also

- [[hackrf-one]] - Recommended upgrade path
- [[atsc3-physical-layer]] - Signal processing requirements
- [[openatsc3-project-plan]] - Hardware section

## References

- OPENATSC3_PROJECT_PLAN.md
- [Nooelec Product Page](https://www.nooelec.com/store/nesdr-smart-v5.html)
- [RTL-SDR Blog](https://www.rtl-sdr.com/)
