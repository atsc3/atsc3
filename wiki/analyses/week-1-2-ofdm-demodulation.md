---
created: 2026-08-30
updated: 2026-08-30
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [week-1-2, ofdm, physical-layer, task-breakdown]
---

# Week 1-2: OFDM Demodulation Task Breakdown

## Goal

Build OFDM demodulator that converts raw IQ samples → constellation points (8K complex values per symbol).

**Test:** Process WIAV-CD capture (`out/wiav_cd_ch30.iq`) and plot constellation.

---

## Day 1: Project Setup & Signal Analysis

### Morning

**□ Create repository structure**
```bash
mkdir -p atsc3_receiver tests docs out
cd atsc3_receiver
touch __init__.py ofdm.py utils.py cli.py
```

**□ Set up Python environment**
```bash
python -m venv venv
source venv/bin/activate
pip install numpy scipy matplotlib pyrtlsdr pytest
```

**□ Create requirements.txt**
```
numpy>=1.24
scipy>=1.10
matplotlib>=3.5
pyrtlsdr>=0.2.90
pytest>=7.0
```

### Afternoon

**□ Analyze WIAV-CD capture file**
```python
# out/analyze_signal.py
import numpy as np

# Load IQ samples
iq_data = np.fromfile('out/wiav_cd_ch30.iq', dtype=np.float32)
iq_complex = iq_data[::2] + 1j * iq_data[1::2]

print(f"Sample count: {len(iq_complex):,}")
print(f"Sample rate: 2.4 MS/s")
print(f"Duration: {len(iq_complex) / 2_400_000:.2f} seconds")
print(f"Center frequency: 569 MHz")

# Plot spectrum
import matplotlib.pyplot as plt
fft = np.fft.fftshift(np.fft.fft(iq_complex[:16384]))
plt.plot(np.abs(fft))
plt.savefig('out/spectrum_wiav_cd.png')
```

**□ Verify ATSC 3.0 signal characteristics**
- Center peak at 569 MHz
- ~6 MHz channel bandwidth (you captured ~2.4 MHz center portion)
- OFDM subcarrier structure visible in spectrum

### Deliverable

- Repository initialized
- IQ file loaded and analyzed
- Spectrum plot generated

---

## Day 2-3: Time Synchronization (Cyclic Prefix Correlation)

### Theory

**ATSC 3.0 OFDM parameters:**
- FFT size: 8192 subcarriers (8K mode)
- Cyclic prefix (CP): Last portion of symbol copied to front
- CP length: Varies (1/16, 1/32, 1/64 of FFT size)
- Sample rate: ~6.912 MS/s (for 8K mode)

**Your RTL-SDR capture:**
- Sample rate: 2.4 MS/s (undersampled)
- You captured center ~2.4 MHz of 6 MHz channel
- Need to resample or work with partial channel

### Implementation

**□ Add CP correlation to `ofdm.py`:**
```python
import numpy as np
from scipy.signal import correlate

class OFDMDemodulator:
    def __init__(self, fft_size=8192, cp_ratio=1/32):
        self.fft_size = fft_size
        self.cp_length = int(fft_size * cp_ratio)
        self.symbol_size = fft_size + self.cp_length
    
    def find_symbol_boundaries(self, iq_samples):
        """
        Find OFDM symbol start positions using CP correlation.
        
        CP is a copy of the last N samples prepended to each symbol.
        Correlating the signal with a delayed version reveals peaks
        at symbol boundaries.
        """
        # Correlate signal with delayed version (delay = FFT size)
        correlation = np.correlate(
            iq_samples,
            np.roll(iq_samples, self.fft_size),
            mode='same'
        )
        
        # Find peaks (symbol start positions)
        from scipy.signal import find_peaks
        peaks, properties = find_peaks(
            np.abs(correlation),
            distance=self.symbol_size * 0.8,  # Avoid duplicate detections
            height=np.mean(np.abs(correlation)) * 2  # Above noise floor
        )
        
        return peaks
```

**□ Test with known signal:**
```python
# Generate test OFDM symbol with CP
test_symbol = np.random.randn(8192) + 1j * np.random.randn(8192)
cp = test_symbol[-256:]  # CP length = 256 (1/32 of 8192)
test_with_cp = np.concatenate([cp, test_symbol])

# Verify correlation detects the CP
demod = OFDMDemodulator()
peaks = demod.find_symbol_boundaries(test_with_cp)
print(f"Detected symbol at sample: {peaks[0]}")  # Should be ~256
```

### Deliverable

- `OFDMDemodulator.find_symbol_boundaries()` working
- Test passes with synthetic signal
- Ready to test on WIAV-CD capture

---

## Day 4: Frequency Synchronization

### Problem

RTL-SDR has frequency offset errors (crystal tolerance ~20-50 ppm). At 569 MHz:
- 20 ppm error = 11.38 kHz offset
- 50 ppm error = 28.45 kHz offset

This rotates constellation points, making demodulation impossible without correction.

### Implementation

**□ Add frequency sync to `ofdm.py`:**
```python
def estimate_frequency_offset(self, iq_samples, symbol_positions):
    """
    Estimate carrier frequency offset using CP phase rotation.
    
    CP and original are identical except for phase rotation
    caused by frequency offset.
    """
    offsets = []
    
    for pos in symbol_positions[:100]:  # Use first 100 symbols
        cp_start = pos
        cp_end = pos + self.cp_length
        
        original_start = pos + self.cp_length
        original_end = original_start + self.cp_length
        
        # Correlate CP with original
        cp = iq_samples[cp_start:cp_end]
        original = iq_samples[original_start:original_end]
        
        # Phase difference = frequency offset
        phase_diff = np.angle(np.sum(cp * np.conj(original)))
        freq_offset = phase_diff / (2 * np.pi * self.cp_length)
        
        offsets.append(freq_offset)
    
    return np.median(offsets)

def correct_frequency_offset(self, iq_samples, freq_offset):
    """Apply frequency correction."""
    n = np.arange(len(iq_samples))
    correction = np.exp(-1j * 2 * np.pi * freq_offset * n)
    return iq_samples * correction
```

### Deliverable

- Frequency offset estimation working
- Correction applied to IQ samples
- Test shows constellation stabilizes

---

## Day 5: FFT Demodulation

### Implementation

**□ Add FFT demodulation to `ofdm.py`:**
```python
def demodulate_symbols(self, iq_samples, symbol_positions):
    """
    Extract OFDM symbols and apply FFT.
    
    Returns: Complex constellation points (8192 per symbol)
    """
    symbols = []
    
    for pos in symbol_positions:
        # Remove CP, keep FFT portion
        symbol_start = pos + self.cp_length
        symbol_end = symbol_start + self.fft_size
        
        if symbol_end > len(iq_samples):
            break
            
        symbol = iq_samples[symbol_start:symbol_end]
        
        # Apply window (optional, reduces spectral leakage)
        window = np.hanning(len(symbol))
        symbol = symbol * window
        
        # FFT → frequency domain (subcarrier values)
        subcarriers = np.fft.fft(symbol)
        
        # Shift zero frequency to center
        subcarriers = np.fft.fftshift(subcarriers)
        
        symbols.append(subcarriers)
    
    return symbols
```

**□ Add constellation plotting to `utils.py`:**
```python
def plot_constellation(subcarriers, filename='constellation.png'):
    """Plot QAM constellation diagram."""
    import matplotlib.pyplot as plt
    
    # Select active subcarriers (skip edge/pilot carriers)
    active = subcarriers[1000:7000]  # Approximate, refine later
    
    plt.figure(figsize=(8, 8))
    plt.scatter(np.real(active), np.imag(active), s=0.5, alpha=0.5)
    plt.xlabel('In-phase (I)')
    plt.ylabel('Quadrature (Q)')
    plt.title('Constellation Diagram')
    plt.grid(True, alpha=0.3)
    plt.axis('equal')
    plt.savefig(filename, dpi=150)
    print(f"Saved: {filename}")
```

### Deliverable

- FFT demodulation working
- Constellation plot generated
- Can see QAM symbol structure

---

## Day 6-7: Integration & Testing

### Full Pipeline Test

**□ Create `cli.py`:**
```python
#!/usr/bin/env python3
"""Command-line interface for ATSC 3.0 receiver."""

import argparse
import numpy as np
from ofdm import OFDMDemodulator
from utils import plot_constellation, plot_spectrum

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', required=True, help='IQ file path')
    parser.add_argument('--freq', type=float, default=569.0, 
                        help='Center frequency (MHz)')
    parser.add_argument('--sample-rate', type=float, default=2.4e6,
                        help='Sample rate (Hz)')
    parser.add_argument('--plot', choices=['constellation', 'spectrum', 'both'],
                        default='both')
    args = parser.parse_args()
    
    # Load IQ samples
    print(f"Loading {args.file}...")
    iq_data = np.fromfile(args.file, dtype=np.float32)
    iq_complex = iq_data[::2] + 1j * iq_data[1::2]
    print(f"Loaded {len(iq_complex):,} samples")
    
    # Demodulate
    demod = OFDMDemodulator(fft_size=8192, cp_ratio=1/32)
    
    print("Finding symbol boundaries...")
    symbol_positions = demod.find_symbol_boundaries(iq_complex)
    print(f"Found {len(symbol_positions)} symbols")
    
    print("Estimating frequency offset...")
    freq_offset = demod.estimate_frequency_offset(iq_complex, symbol_positions)
    print(f"Frequency offset: {freq_offset:.2f}")
    
    print("Correcting frequency offset...")
    iq_corrected = demod.correct_frequency_offset(iq_complex, freq_offset)
    
    print("Demodulating symbols...")
    symbols = demod.demodulate_symbols(iq_corrected, symbol_positions)
    
    # Plot
    if args.plot in ['spectrum', 'both']:
        plot_spectrum(iq_complex, filename='out/spectrum.png')
    
    if args.plot in ['constellation', 'both']:
        plot_constellation(symbols[0], filename='out/constellation.png')
    
    print("Done!")

if __name__ == '__main__':
    main()
```

### Success Criteria

```bash
# Run the pipeline
python -m atsc3_receiver.cli --file out/wiav_cd_ch30.iq --plot both

# Expected output:
# - Found 50-200 symbols (depends on capture length)
# - Frequency offset: 0.001-0.03 (typical RTL-SDR drift)
# - Constellation shows QAM structure (not random noise)
```

**What you should see:**

**Good constellation:**
- Clear clusters of points (QAM symbols)
- 4-16 distinct clusters visible (depending on modulation)
- Low scatter around each cluster

**Bad constellation (problems):**
- Random cloud (no synchronization)
- Rotating pattern (frequency offset not corrected)
- Smearing (timing drift)

### Deliverable

- Full pipeline working end-to-end
- Constellation plot from real WIAV-CD broadcast
- Document results (screenshots, observations)

---

## Week 1-2 Output

**By end of Week 2, you should have:**

```
atsc3_receiver/
├── __init__.py
├── ofdm.py              # OFDMDemodulator class
├── utils.py             # Plotting utilities
├── cli.py               # Command-line interface
└── tests/
    ├── test_ofdm.py     # Unit tests
    └── test_sync.py     # Sync algorithm tests

out/
├── spectrum_wiav_cd.png
├── constellation.png
└── week1_report.md      # Your observations
```

**Test command:**
```bash
python -m atsc3_receiver.cli --file out/wiav_cd_ch30.iq --plot constellation
```

**Expected result:** Constellation diagram showing QAM symbols from WIAV-CD broadcast.

---

## Common Issues & Solutions

| Problem | Symptom | Solution |
|---------|---------|----------|
| No symbol detection | 0 symbols found | Lower peak detection threshold; check CP ratio |
| Constellation rotating | Points spin in circle | Frequency offset correction not working |
| Random scatter | No clusters visible | Timing sync failed; verify CP correlation |
| Only noise | Flat spectrum | Wrong frequency; antenna not connected |
| Crash on load | Memory error | File too large; load in chunks |

---

## Next Week (Week 3-4)

After OFDM demodulation works:

- **Pilot extraction** - Extract scattered pilot carriers (A/322 spec)
- **Channel estimation** - Interpolate pilots to estimate channel response
- **Equalization** - Divide subcarriers by channel estimate
- **Test:** Compare constellation SNR before/after equalization

---

## See Also

- [[openatsc3-project-plan]] - Full project timeline
- [[atsc3-physical-layer]] - ATSC 3.0 signal structure reference
- [[washington-dc-atsc3-stations]] - WIAV-CD station details
- [[nooelec-nesdr-smart-v5]] - Your RTL-SDR device

## References

- ATSC A/322 Physical Layer Specification
- [ATSC 3.0 OFDM Parameters](https://www.atsc.org/standard/physical-layer-protocol/)
- OPENATSC3_PROJECT_PLAN.md (source document)
