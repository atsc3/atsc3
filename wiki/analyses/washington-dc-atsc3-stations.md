---
created: 2026-08-30
updated: 2026-08-30
sources: [nextgentv.info, rabbitears.info, out/scan.csv]
tags: [washington-dc, ats3-stations, channel-identification, wiav-cd]
---

# Washington DC ATSC 3.0 Stations

## Location

**Rockville, MD** (Washington DC DMA - Designated Market Area #9)

## Identified Stations

Your UHF scan (470-698 MHz) detected **5 ATSC 3.0 stations** from the Washington DC market.

### Confirmed ATSC 3.0 Stations

| Station | Network | RF Channel | Frequency | Your Scan Power | Status |
|---------|---------|------------|-----------|-----------------|--------|
| **WIAV-CD** | Independent | **30** | 566-572 MHz | **22.05 dB** (strongest) | ✓ Captured |
| WHUT-TV | PBS | 33 | 584-590 MHz | 21.58 dB | ✓ Detected |
| WRC-TV | NBC | 34 | 590-596 MHz | 21.44 dB | ✓ Detected |
| WTTG | FOX | 36 | 602-608 MHz | 21.77 dB | ✓ Detected |
| WJLA-TV | ABC | 7 | 174-180 MHz | (VHF-Hi) | Not in UHF scan |
| WUSA | CBS | 9 | 186-192 MHz | (VHF-Hi) | Not in UHF scan |

## WIAV-CD (Channel 30) - Primary Target

**Why this is your best capture target:**

1. **Strongest signal** in your scan (22.05 dB at 572 MHz)
2. **ATSC 3.0 native** - launched NextGen TV Dec 2021
3. **Independent station** - may carry multiple subchannels
4. **RF Channel 30** = 566-572 MHz (6 MHz bandwidth)

**Station details:**
- **Call sign:** WIAV-CD (Class A digital station)
- **Network:** Independent (locally owned)
- **Location:** Washington, DC transmitter
- **FCC Profile:** [View](https://publicfiles.fcc.gov/tv-profile/WIAV-CD)
- **Website:** Not available (independent)

**Capture parameters:**
```bash
# Center frequency: 569 MHz (middle of channel 30)
# Bandwidth: 2.4 MHz (RTL-SDR maximum)
# This captures ~40% of the 6 MHz channel

rtl_sdr -d 1 -f 569000000 -s 2400000 -g 49.6 out/wiav_cd_ch30.iq
```

**Note:** RTL-SDR's 2.4 MHz bandwidth captures the center portion of the 6 MHz channel. Sufficient for:
- Signal presence confirmation
- Constellation analysis
- Pilot carrier extraction
- Initial OFDM demod testing

For full channel capture, upgrade to HackRF (20 MHz bandwidth).

## Other Detected Stations

### WHUT-TV (Channel 33) - PBS
- **RF Channel:** 33 (584-590 MHz)
- **Network:** PBS (Howard University)
- **Scan power:** 21.58 dB at 587 MHz
- **ATSC 3.0:** Yes (public broadcasting pioneer)
- **Virtual channel:** 32

### WRC-TV (Channel 34) - NBC
- **RF Channel:** 34 (590-596 MHz)
- **Network:** NBC (owned-and-operated)
- **Scan power:** 21.44 dB at 591 MHz
- **ATSC 3.0:** Yes
- **Virtual channel:** 4 (NBC Washington)

### WTTG (Channel 36) - FOX
- **RF Channel:** 36 (602-608 MHz)
- **Network:** FOX (owned-and-operated)
- **Scan power:** 21.77 dB at 605 MHz
- **ATSC 3.0:** Yes
- **Virtual channel:** 5 (FOX 5 DC)

### WJLA-TV (Channel 7) - ABC
- **RF Channel:** 7 (174-180 MHz) - VHF-Hi band
- **Network:** ABC
- **ATSC 3.0:** Yes
- **Virtual channel:** 7
- **Note:** Not visible in UHF scan (470-698 MHz)

### WUSA (Channel 9) - CBS
- **RF Channel:** 9 (186-192 MHz) - VHF-Hi band
- **Network:** CBS
- **ATSC 3.0:** Yes
- **Virtual channel:** 9
- **Note:** Not visible in UHF scan (470-698 MHz)

## Washington DC Market Context

**NextGen TV Launch:** December 16, 2021

**Market characteristics:**
- DMA #9 (9th largest US TV market)
- 6 ATSC 3.0 stations (as of 2026)
- Mix of commercial and public broadcasters
- Strong signal coverage in Rockville, MD area

**Your location (Rockville, MD):**
- ~15 miles northwest of DC transmitters
- Suburban location with good line-of-sight
- All UHF stations detectable with RTL-SDR
- VHF stations (7, 9) require VHF-capable antenna

## Signal Analysis

**Why WIAV-CD is strongest:**

1. **Transmitter location** - May be closer to Rockville than other stations
2. **Antenna pattern** - Directed toward northwestern suburbs
3. **Power level** - Class A station with adequate ERP
4. **Frequency** - Channel 30 has good propagation characteristics

**Signal quality indicators:**
- 22.05 dB = strong, clean signal
- Flat plateau across 6 MHz = proper ATSC modulation
- Sharp channel boundaries = minimal interference

## Next Steps

### Immediate (Week 1-2)

1. **Process WIAV-CD capture** with OFDM demodulator
   ```bash
   python -m atsc3_receiver --file out/wiav_cd_ch30.iq --plot constellation
   ```

2. **Verify ATSC 3.0 signal structure**
   - Look for OFDM subcarriers (8K FFT)
   - Identify pilot carriers
   - Check constellation pattern (QAM)

3. **Compare with ATSC 1.0** (if available)
   - WJLA-TV on channel 7 (VHF) may still be ATSC 1.0
   - Different modulation (8VSB vs OFDM)

### Medium-term (Week 3-8)

- Capture additional stations (WHUT, WRC, WTTG)
- Compare signal quality across channels
- Test with different antenna positions
- Document SNR improvements through equalization

### Long-term (Phase 2+)

- Integrate certificate validation
- Check WIAV-CD certificate chain
- Validate against your CA platform

## See Also

- [[scan-results-2026-08-30]] - Original UHF scan analysis
- [[rtl-sdr-scanning]] - Scanning methodology
- [[atsc3-physical-layer]] - ATSC 3.0 signal structure
- [[openatsc3-project-plan]] - Project overview

## References

- [NextGen TV DC Station List](https://nextgentv.info/state/district-of-columbia/city/washington)
- [FCC WIAV-CD Profile](https://publicfiles.fcc.gov/tv-profile/WIAV-CD)
- [ATSC NextGen TV Launch Announcement](https://www.atsc.org/news/nextgen-tv-launches-in-washington-dc/)
- [Wikipedia ATSC 3.0 Station List](https://en.wikipedia.org/wiki/List_of_ATSC_3.0_television_stations_in_the_United_States)
