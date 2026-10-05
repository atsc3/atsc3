---
created: 2026-08-30
updated: 2026-08-30
sources: [out/scan.csv]
tags: [scan-results, signal-analysis, uhf-tv]
---

# UHF Scan Results - 2026-08-30

## Summary

Scanned 470-698 MHz UHF band with RTL-SDR. Found **5 strong TV signal clusters** consistent with ATSC broadcasts.

## Strongest Signals

| Frequency Range | Peak Power | Likely Channel | Notes |
|-----------------|------------|----------------|-------|
| 572-578 MHz | 22.05 dB | 30-31 | **Strongest signal** |
| 622-642 MHz | 21.78 dB | 39-41 | Wide cluster (20 MHz) |
| 602-610 MHz | 21.77 dB | 36-37 | Strong, clean |
| 584-592 MHz | 21.58 dB | 33-34 | Good candidate |
| 476-481 MHz | 21.28 dB | 14-15 | Lower band edge |

## Signal Characteristics

**Digital TV plateaus observed:**
- Flat-topped power profiles ~6 MHz wide
- Sharp drop-offs at channel boundaries
- 20+ dB above noise floor

**Noise floor:** -5 to +5 dB in empty spectrum

**Notable clusters:**

### 476-481 MHz (Channel 14-15)
```
476 MHz: 21.28 dB ← peak
477 MHz: 21.15 dB
478 MHz: 21.28 dB
479 MHz: 21.23 dB
480 MHz: 21.20 dB
481 MHz: 20.81 dB
482 MHz: -1.62 dB ← sharp drop
```
Classic 6 MHz digital TV profile.

### 572-578 MHz (Channel 30-31) - STRONGEST
```
572 MHz: 22.05 dB ← highest peak
573 MHz: 21.19 dB
574 MHz: 21.44 dB
575 MHz: 21.51 dB
576 MHz: 21.37 dB
577 MHz: 21.42 dB
578 MHz: 2.15 dB ← sharp drop
```

### 584-592 MHz (Channel 33-34)
```
584 MHz: 21.42 dB
585 MHz: 21.52 dB
586 MHz: 21.44 dB
587 MHz: 21.58 dB ← peak
588 MHz: 21.39 dB
589 MHz: 21.47 dB
590 MHz: 21.97 dB ← highest in cluster
591 MHz: 21.35 dB
592 MHz: 21.46 dB
```

### 602-610 MHz (Channel 36-37)
```
602 MHz: 21.77 dB ← peak
603 MHz: 21.55 dB
604 MHz: 21.42 dB
605 MHz: 21.56 dB
606 MHz: 21.36 dB
607 MHz: 21.28 dB
608 MHz: 14.88 dB ← tapering
```

### 622-642 MHz (Channel 39-41) - WIDEST
```
622 MHz: 21.78 dB
623-631 MHz: 21.11-21.53 dB (sustained)
632-642 MHz: 21.40-21.11 dB
643 MHz: 4.68 dB ← sharp drop
```
20 MHz cluster - possibly multiple co-located channels or ATSC 3.0 multiplex.

## Recommended Capture Frequencies

**Priority 1:** 575 MHz (Channel 30)
- Strongest single peak (22.05 dB)
- Clean 6 MHz profile
- Good ATSC 3.0 candidate

**Priority 2:** 587 MHz (Channel 33)
- Strong, stable signal
- Central UHF band (good propagation)

**Priority 3:** 605 MHz (Channel 36)
- Strong signal
- Common ATSC 3.0 frequency in many markets

**Priority 4:** 632 MHz (Channel 40-41)
- Wide cluster suggests multiplex
- May contain multiple services

## Next Steps

1. **Check RabbitEars:** Visit [rabbitears.info](https://rabbitears.info) to identify which local stations are ATSC 3.0

2. **Capture IQ samples:**
   ```bash
   rtl_sdr -d 1 -f 575000000 -s 2400000 -g 49.6 out/capture_575mhz.iq
   ```

3. **Process with Python library** (once implemented):
   ```bash
   python -m atsc3_receiver --file out/capture_575mhz.iq --plot constellation
   ```

## See Also

- [[rtl-sdr-scanning]] - Scanning methodology
- [[nooelec-nesdr-smart-v5]] - Device used
- [[atsc3-physical-layer]] - What ATSC 3.0 signals look like

## References

- out/scan.csv (raw scan data)
- [RabbitEars ATSC 3.0 Database](https://rabbitears.info)
