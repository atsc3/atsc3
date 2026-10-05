# Optimization TODO: Symbol Detection

## Current Performance

- **12M samples:** ~120 seconds (2 minutes)
- **Bottleneck:** O(n × cp_length) correlation loop
- **Acceptable for:** Offline analysis, development

## Why It's Slow

The cyclic prefix correlation computes:
```python
for i in range(n):
    correlation[i] = sum(cp[i:i+256] * conj(signal[i+8192:i+8192+256]))
```

This is **O(n × 256)** = 3.2 billion operations for 12M samples.

## Proposed Optimizations

### 1. Numba JIT Compilation (Recommended)

Add numba dependency:
```python
from numba import jit, prange

@jit(nopython=True, parallel=True)
def find_symbol_boundaries(iq_samples, fft_size, cp_length):
    n = len(iq_samples)
    corr_len = n - fft_size - cp_length
    correlations = np.zeros(corr_len)
    
    for i in prange(corr_len):  # Parallel loop
        cp_region = iq_samples[i:i + cp_length]
        original_region = iq_samples[i + fft_size:i + fft_size + cp_length]
        correlations[i] = np.abs(np.sum(cp_region * np.conj(original_region)))
    
    return correlations
```

**Expected speedup:** 50-100x (2-4 seconds)

**Tradeoff:** Adds numba dependency, longer initial compile time

---

### 2. FFT-Based Correlation

Use the convolution theorem:
```python
# Correlation in frequency domain
fft_signal = np.fft.fft(iq_samples)
fft_delayed = np.fft.fft(np.roll(iq_samples, -fft_size))
correlation = np.fft.ifft(fft_signal * np.conj(fft_delayed))
```

**Expected speedup:** 10-20x (6-12 seconds)

**Issue:** Tried this but peak detection was incorrect (finding FFT offset, not symbol boundaries)

**Tradeoff:** More complex, needs careful implementation

---

### 3. GPU Acceleration (CuPy)

Replace numpy with CuPy:
```python
import cupy as cp

iq_gpu = cp.asarray(iq_samples)
# Same algorithm runs on GPU
```

**Expected speedup:** 100-200x (0.6-1.2 seconds)

**Tradeoff:** Requires NVIDIA GPU, adds CuPy dependency (2GB install)

---

### 4. Subsample + Refine

1. Compute correlation at every 10th sample (coarse)
2. Refine peaks at full resolution

**Expected speedup:** 5-10x (12-24 seconds)

**Tradeoff:** May miss some symbols, less accurate

---

### 5. Block Processing with Stride Tricks

Use `numpy.lib.stride_tricks.sliding_window_view` to create windows without copying:
```python
from numpy.lib.stride_tricks import sliding_window_view

cp_windows = sliding_window_view(iq_samples, cp_length)
orig_windows = sliding_window_view(iq_samples[fft_size:], cp_length)
correlations = np.abs(np.sum(cp_windows * np.conj(orig_windows), axis=1))
```

**Issue:** Memory explosion - creates 12M × 256 complex64 array (24GB)

**Tradeoff:** Too memory-intensive for large files

---

## Recommendation

**Short-term:** Accept 2-minute processing time for offline work

**Medium-term:** Add numba as optional dependency
```bash
pip install atsc3lib[speed]  # Installs numba
```

**Long-term:** Consider GPU acceleration for real-time applications

---

## Priority

| Optimization | Effort | Speedup | Priority |
|--------------|--------|---------|----------|
| Accept current | None | 1x | ✓ Done |
| Numba JIT | Low | 50-100x | **High** |
| FFT-based | Medium | 10-20x | Medium |
| GPU (CuPy) | Low | 100-200x | Low (niche) |
| Subsample | Low | 5-10x | Low |

---

## Implementation Plan

When ready to optimize:

1. Add numba to `pyproject.toml` as optional dependency
2. Create `atsc3lib/ofdm_fast.py` with JIT-compiled version
3. Auto-detect numba availability, fall back to pure numpy
4. Document 50-100x speedup in release notes

**Estimated effort:** 2-3 hours  
**Impact:** Makes interactive development feasible
