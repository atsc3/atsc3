---
created: 2026-08-30
updated: 2026-08-30
sources: [user-provided]
tags: [rtl-sdr, scanning, signal-detection, tutorial]
---

# RTL-SDR Signal Scanning

> **Note:** RTL-SDR is **not supported** for ATSC 3.0 decoding — its 2.4 MHz
> bandwidth is narrower than the 6 MHz channel.  It can only be used for
> spectrum **power surveys** (finding candidate frequencies), which is what this
> historical page describes.  Decoding requires a device wider than 6 MHz
> (SDRplay).

## Quick Start: Scan for ATSC 3.0 Signals

### Method 1: rtl_power (Frequency Sweep)

Scans a frequency range and logs power levels to find active signals.

```bash
# Scan US VHF/UHF TV band (54-806 MHz) in 1 MHz steps
rtl_power -f 54M:806M:1M -i 1s -1 tv_scan.csv

# Scan specific ATSC 3.0 candidate frequencies (faster)
rtl_power -f 470M:698M:1M -i 1s -1 uhf_scan.csv

# Scan with gain optimization
rtl_power -f 470M:698M:1M -g 49.6 -i 2s -1 gain_test.csv
```

**Parameters:**
- `-f` Start:Stop:Step (e.g., 470M:698M:1M)
- `-i` Integration time (1-2s per frequency)
- `-g` Tuner gain (0-49.6 dB, higher = more sensitive but more noise)
- `-1` Single-shot mode (scan once, exit)

**View results:**
```bash
# Simple view
cat tv_scan.csv

# Find strongest signals
awk -F, '{print $1, $3}' tv_scan.csv | sort -k2 -rn | head -20

# Plot (if gnuplot installed)
rtl_power.py -i tv_scan.csv -o scan.png
```

### Method 2: rtl_fm (Listen/Tune)

Tune to specific frequencies to inspect signals.

```bash
# Tune to 617 MHz, output to file
rtl_fm -f 617000000 -s 2048000 -g 49.6 signal_617mhz.raw

# Monitor in real-time (with sox for audio)
rtl_fm -f 617000000 -s 2048000 -g 49.6 | play -t raw -r 2048k -e s -b 16 -c 1 -V1 -
```

### Method 3: Full Spectrum Capture (IQ)

Capture raw IQ samples for offline analysis.

```bash
# Capture 2.4 MHz bandwidth at 617 MHz
rtl_sdr -f 617000000 -s 2400000 -g 49.6 capture_617mhz.iq

# Capture with specific duration (10 seconds)
timeout 10 rtl_sdr -f 617000000 -s 2400000 -g 49.6 capture_10s.iq
```

## ATSC 3.0 Frequency Ranges

### US TV Bands

| Band | Frequencies | Notes |
|------|-------------|-------|
| VHF-Lo | 54-88 MHz | Channels 2-6 (rare for ATSC 3.0) |
| VHF-Hi | 174-216 MHz | Channels 7-13 |
| UHF | 470-698 MHz | Channels 14-51 (most common) |

### Common ATSC 3.0 Frequencies (US)

Major markets with ATSC 3.0 (NextGen TV) deployments:
- Check [rabbitears.info](https://rabbitears.info) for local stations
- Search for stations with "ATSC 3.0" or "NextGen TV" label

## Device Selection

### List Available Devices

```bash
rtl_test -t
```

Expected output:
```
Found 2 device(s):
  0:  Nooelec, NESDR SMArt v5, SN: 84077375
  1:  Nooelec, NESDR SMArt v5, SN: 84077376
```

### Select Device Index

Use `-d` flag to specify device index:

```bash
# Use device at index 1
rtl_power -d 1 -f 470M:698M:1M -g 49.6 -i 2s -1 uhf_scan.csv

# Use device by serial number (more reliable)
rtl_power -d 84077375 -f 470M:698M:1M -g 49.6 -i 2s -1 uhf_scan.csv

# Use device by partial serial match
rtl_power -d 84077375 -f 470M:698M:1M -g 49.6 -i 2s -1 uhf_scan.csv
```

**All rtl-sdr tools support `-d`:**
- `rtl_test -d 1`
- `rtl_power -d 1 ...`
- `rtl_fm -d 1 ...`
- `rtl_sdr -d 1 ...`

## Step-by-Step Scan Procedure

### 1. Verify Device

```bash
rtl_test -t
```

Expected output:
```
Found 1 device(s):
  0:  Nooelec, NESDR SMArt v5, SN: 84077375
Using device 0: Realtek RTL2838UHIDIR SN: 84077375
Supported gain values (29): 0.0 0.9 1.4 ... 49.6
Tuner available
```

### 2. Quick Band Scan

```bash
# 30-minute UHF scan (470-698 MHz, 1 MHz steps, 2s each)
rtl_power -f 470M:698M:1M -g 49.6 -i 2s -1 uhf_quick.csv
```

### 3. Identify Peaks

```bash
# Extract frequency and power, sort by strength
awk -F, '{for(i=3;i<=NF;i++) if($i>max){max=$i;freq=$1}} END{print freq, max}' uhf_quick.csv

# Or find top 10 strongest frequencies
grep -v "^#" uhf_quick.csv | awk -F, '{for(i=3;i<=NF;i++) print $1+(i-3)*0.001, $i}' | sort -k2 -rn | head -10
```

### 4. Inspect Candidate Frequencies

```bash
# Capture 10 seconds at strongest frequency
rtl_sdr -f <freq> -s 2400000 -g 49.6 candidate.iq

# Process with your Python library
python -m atsc3_receiver --file candidate.iq --plot constellation
```

## Tips

**Gain Settings:**
- Start with `-g 49.6` (maximum)
- If overload/distortion: reduce to 40-45 dB
- Indoor antenna: use max gain
- Outdoor antenna: may need less gain

**Integration Time:**
- `-i 1s`: Fast scan, less accurate
- `-i 2s`: Good balance
- `-i 5s+`: More accurate, much slower

**Antenna:**
- Use included dipole, extend fully
- Position near window, higher is better
- Try different orientations (vertical/horizontal)

**USB:**
- Use USB 2.0+ port
- Avoid USB 3.0 ports (can cause interference)
- Use short, shielded USB cable if possible

## Expected Results

**RTL-SDR limitations:**
- 2.4 MHz max sample rate
- ATSC 3.0 uses 6-8 MHz channels
- You'll see the center portion of channels only
- Still sufficient to detect signal presence

**What to look for:**
- Flat-topped "plateau" shapes (digital signals)
- ~6-8 MHz wide power concentrations
- Stronger than noise floor by 20+ dB

## See Also

- [[nooelec-nesdr-smart-v5]] - Your device details
- [[atsc3-physical-layer]] - What ATSC 3.0 signals look like
- [[openatsc3-project-plan]] - Project hardware section

## References

- [RTL-SDR Quick Start Guide](https://www.rtl-sdr.com/rtl-sdr-quick-start-guide/)
- [RabbitEars ATSC 3.0 Map](https://rabbitears.info)
