---
created: 2026-08-30
updated: 2026-08-30
sources: [rtl-power output]
tags: [rtl-sdr, scan-format, data-analysis]
---

# RTL-Power Scan File Format

## File Structure

Each line in `scan.csv` represents one 1 MHz frequency bin measurement.

### CSV Columns

```
Column 1: Date (2026-08-30)
Column 2: Time (16:35:32)
Column 3: Start frequency (Hz) (470000000 = 470 MHz)
Column 4: Stop frequency (Hz) (471000000 = 471 MHz)
Column 5: Step size (Hz) (1000000.00 = 1 MHz bins)
Column 6: Number of samples (1)
Column 7: Average power (dB) ← KEY METRIC
Column 8: RMS power (dB)
```

### Example Line

```csv
2026-08-30, 16:35:32, 470000000, 471000000, 1000000.00, 1, 12.26, 12.26
```

**Translation:**
- Date: 2026-08-30 at 16:35:32
- Scanned 470-471 MHz (1 MHz bin)
- Took 1 sample
- Average power: **12.26 dB** (relative to noise floor)

## Understanding dB Values

**Power is measured in dB (decibels) relative to noise:**

| dB Range | Interpretation |
|----------|----------------|
| < 0 dB | Below noise floor (no signal) |
| 0-10 dB | Weak signal or noise variation |
| 10-20 dB | Moderate signal |
| 20+ dB | **Strong signal** (likely broadcast TV) |
| 30+ dB | Very strong (near transmitter) |

**Your scan results:**
- Noise floor: -5 to +5 dB (empty spectrum)
- TV signals: 20-22 dB (clear digital broadcasts)

## Reading the File

### Method 1: Command Line

```bash
# View first 10 lines
head -10 out/scan.csv

# Find strongest signals (sort by column 7, descending)
sort -t',' -k7 -rn out/scan.csv | head -20

# Find signals above 20 dB
awk -F',' '$7 > 20 {print $3/1000000, $7}' out/scan.csv
# Output: frequency in MHz, power in dB
```

### Method 2: Python

```python
import csv

with open('out/scan.csv', 'r') as f:
    reader = csv.reader(f)
    for row in reader:
        freq_mhz = float(row[2]) / 1e6  # Hz to MHz
        power_db = float(row[6])
        
        if power_db > 20:  # Strong signals only
            print(f"{freq_mhz:.0f} MHz: {power_db:.1f} dB")
```

### Method 3: Plot (if matplotlib installed)

```python
import csv
import matplotlib.pyplot as plt

freqs = []
powers = []

with open('out/scan.csv', 'r') as f:
    reader = csv.reader(f)
    for row in reader:
        freqs.append(float(row[2]) / 1e6)  # MHz
        powers.append(float(row[6]))       # dB

plt.figure(figsize=(12, 4))
plt.plot(freqs, powers, linewidth=0.5)
plt.xlabel('Frequency (MHz)')
plt.ylabel('Power (dB)')
plt.title('UHF Scan Results')
plt.grid(True)
plt.savefig('scan_plot.png')
```

## Identifying TV Signals

**Digital TV characteristics:**

1. **Plateau shape** - Flat top ~6 MHz wide (not a sharp peak)
2. **Sharp boundaries** - Power drops 15+ dB at channel edges
3. **Consistent power** - Within 2-3 dB across the channel

**Example TV channel signature:**
```
476 MHz: 21.28 dB ← channel start
477 MHz: 21.15 dB
478 MHz: 21.28 dB
479 MHz: 21.23 dB
480 MHz: 21.20 dB
481 MHz: 20.81 dB
482 MHz: -1.62 dB ← sharp drop (channel end)
```

This is a **6 MHz ATSC channel** (476-482 MHz, Channel 14).

**Analog TV (old) would show:**
- Single sharp peak at visual carrier frequency
- Gradual roll-off on edges
- ~4.5 MHz wide

## Your Scan Summary

**Scan parameters:**
- Range: 470-698 MHz (UHF TV band)
- Resolution: 1 MHz bins
- Total bins: 228
- Duration: ~1 snapshot (single pass)

**Signal inventory:**

| Power Range | Count | Interpretation |
|-------------|-------|----------------|
| > 20 dB | ~50 bins | **Strong TV broadcasts** |
| 10-20 dB | ~30 bins | Moderate signals/edge of channels |
| 0-10 dB | ~80 bins | Weak signals or noise |
| < 0 dB | ~68 bins | Noise floor (empty spectrum) |

**TV channels detected:**
- Channel 14-15 (476-482 MHz)
- Channel 30-31 (572-578 MHz) ← strongest
- Channel 33-34 (584-592 MHz)
- Channel 36-37 (602-610 MHz)
- Channel 39-41 (622-642 MHz) ← widest cluster

## Converting to MHz

```
Hz          → MHz:    divide by 1,000,000
470000000   → 470 MHz
575000000   → 575 MHz
698000000   → 698 MHz
```

## US TV Channel Map

| Channel | Frequency Range |
|---------|-----------------|
| 14-20   | 470-512 MHz |
| 21-27   | 512-554 MHz |
| 28-34   | 554-596 MHz |
| 35-41   | 596-638 MHz |
| 42-48   | 638-680 MHz |
| 49-51   | 680-698 MHz |

**Note:** Channels 38-51 repurposed for 5G in many areas.

## See Also

- [[rtl-sdr-scanning]] - How to run scans
- [[scan-results-2026-08-30]] - Your scan analysis
- [[nooelec-nesdr-smart-v5]] - Your device

## References

- [rtl_power documentation](https://github.com/keenerd/rtl-sdr-misc/tree/master/rtl_power)
- [US TV Channel Frequencies](https://en.wikipedia.org/wiki/United_States_television_channel_frequencies)
