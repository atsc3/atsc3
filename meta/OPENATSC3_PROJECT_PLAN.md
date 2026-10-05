# OpenATSC3 Project Plan
## Python Library → Software Receiver → Certificate Authority Platform

**Goal:** Build open-source ATSC 3.0 ecosystem  
**Timeline:** 12-16 weeks to working receiver with CA validation  
**Hardware:** HackRF One (~$320). RTL-SDR is **not supported** (2.4 MHz is narrower than the 6 MHz ATSC 3.0 channel).  
**Language:** Python (library) → Rust (receiver, optional)   

---

## Phase Overview

```
Phase 1 (Weeks 1-8):  ATSC3 Python Library
                      ├─ Physical layer demodulation
                      ├─ AC-4 audio decoder
                      ├─ Frame parsing
                      └─ C bindings for performance

Phase 2 (Weeks 9-12): Software Receiver
                      ├─ Integration layer
                      ├─ Real-time processing
                      ├─ Certificate validation
                      └─ Proof-of-concept demo

Phase 3 (Week 13+):   Certificate Authority Platform
                      ├─ Multi-CA support
                      ├─ Broadcaster enrollment
                      ├─ Device provisioning
                      └─ Commercial deployment
```

---

## Phase 1: ATSC3 Python Library (Weeks 1-8)

### Architecture

```
atsc3_receiver/
├── __init__.py
├── ofdm.py                 # OFDM demod (Python + C binding)
├── pilot.py                # Pilot extraction + equalization
├── constellation.py        # QAM mapping
├── ldpc.py                 # LDPC decoding (pyldpc wrapper)
├── framing.py              # Frame sync + LLS parsing
├── ac4_decoder.py          # AC-4 audio (C binding or pure Python)
├── bootstrap.py            # Bootstrap sequence parsing
├── signaling.py            # Signaling table parsing
├── transport.py            # MMTP/ROUTE demux
└── certificate.py          # YOUR CA integration

tests/
├── test_ofdm.py
├── test_ldpc.py
├── test_framing.py
├── test_ac4.py
└── fixtures/               # Real broadcast recordings

docs/
├── ARCHITECTURE.md
├── ALGORITHM_REFERENCE.md
└── API.md
```

### Dependencies

```python
# Core signal processing (C bindings)
numpy>=1.24              # Array ops
scipy>=1.10              # Filters, FFT, correlations
scikit-dsp>=0.5         # DSP utilities (optional)

# LDPC decoding (C backend)
pyldpc>=0.2              # Pure Python or libldpc wrapper

# Audio decoding
# AC-4: No library exists
#   Option A: Implement in Python (weeks 3-4)
#   Option B: Use C extension binding

# RF/SDR
hackrf>=0.1 OR osmosdr   # HackRF support (if available)
# Note: RTL-SDR is NOT supported - its 2.4 MHz bandwidth is narrower than the
# 6 MHz ATSC 3.0 channel.

# Utilities
matplotlib>=3.5          # Visualization/debugging
pytest>=7.0              # Testing
pyyaml>=6.0             # Config files

# Your code
openatsc3_cert>=1.0     # YOUR certificate validation library
```

### Week-by-Week Breakdown

**Week 1-2: Physical Layer Foundation**
```
□ OFDM demodulation from raw IQ samples
  ├─ Time synchronization (CP correlation)
  ├─ Frequency synchronization (coarse/fine)
  ├─ Cyclic prefix removal
  └─ FFT → subcarrier symbols
□ Output: Constellation points (8K complex values per symbol)
□ Test: Real broadcast reception, plot constellations
□ Library: atsc3_receiver.ofdm.OFDMDemodulator
```

**Week 3-4: Channel Estimation & Equalization**
```
□ Pilot carrier extraction (scattered pilot pattern from A/322)
□ Channel response estimation (frequency domain)
□ Subcarrier equalization
□ Phase tracking across symbols
□ Output: Equalized symbols ready for QAM mapping
□ Test: Compare constellation before/after equalization (SNR improvement)
□ Library: atsc3_receiver.pilot.PilotExtractor, ChannelEstimator
```

**Week 5-6: Demodulation to Bits**
```
□ QAM constellation mapping (QPSK, 16-QAM, 64-QAM, 256-QAM)
□ Soft-decision decoding (LLR generation)
□ Symbol to bit conversion
□ Handling multiple PLP streams
□ Output: Bit stream with reliability metrics
□ Test: BER measurement against SNR
□ Library: atsc3_receiver.constellation.QAMDecoder
```

**Week 7: LDPC Decoding (Integration)**
```
□ Load ATSC 3.0 LDPC parity check matrices (from spec_bank)
□ Belief propagation decoding (use pyldpc library)
□ Handle multiple code rates (2/3, 3/4, 4/5, etc.)
□ FEC convergence detection
□ Output: Decoded FEC blocks
□ Test: FEC block convergence rate
□ Library: atsc3_receiver.ldpc.LDPCDecoder (wrapper around pyldpc)
```

**Week 8: Framing & Signaling**
```
□ Bootstrap sequence parsing
□ L1 detail signaling extraction
□ LLS table parsing (Low-Level Signaling tables)
□ PLCF (Physical Layer Configuration) extraction
□ Output: Structured signaling data, service configuration
□ Test: Parse real broadcast signaling
□ Library: atsc3_receiver.framing.FrameParser
```

### C Bindings Strategy

```
Performance-critical paths (use C):
├─ FFT (already scipy.fft uses FFTPACK)
├─ Correlation (scipy.signal.correlate uses BLAS)
├─ LDPC belief propagation (optional, if pyldpc too slow)
└─ QAM soft-decision LLR calculation (optional)

Audio (AC-4):
├─ Option 1: Pure Python (simplest, ~400 lines)
├─ Option 2: ctypes binding to ffmpeg-libac4
└─ Option 3: Rust extension (performance, later)

Initial: Pure Python for AC-4, optimize later if needed
```

### Testing Each Week

```
Week 1-2 OFDM:
  python -m atsc3_receiver --freq 617000000 --plot constellation
  # Should show constellation plot of received symbols
  
Week 3-4 Equalization:
  python -m atsc3_receiver --freq 617000000 --plot snr
  # Should show SNR improvement after equalization
  
Week 5-6 QAM:
  python -m atsc3_receiver --freq 617000000 --plot ber
  # Should show decreasing BER with increasing symbol count
  
Week 7 LDPC:
  python -m atsc3_receiver --freq 617000000 --show-fec-stats
  # Should show FEC convergence rates
  
Week 8 Framing:
  python -m atsc3_receiver --freq 617000000 --dump-signaling
  # Should dump parsed LLS tables, service info
```

---

## Phase 2: Software Receiver (Weeks 9-12)

### What You're Building

```
Real-time ATSC 3.0 receiver on consumer hardware:
├─ Tune to broadcast frequency (HackRF)
├─ Decode physical layer in real-time
├─ Parse transport (MMTP/ROUTE)
├─ Demux media (video, audio, captions)
├─ Decode video (HEVC passthrough to ffmpeg)
├─ Decode audio (AC-4 Python decoder or ffmpeg)
├─ Validate certificates (YOUR CA)
├─ Output: Watchable broadcast

Performance target:
- Real-time on 6-core x86 CPU (HackRF)
- May need GPU for LDPC on RPi
```

### Architecture

```
openatsc3_receiver/
├── receiver.py           # Main loop
├── tuner.py              # SDR abstraction (HackRF)
├── pipeline.py           # Signal processing pipeline
├── demux.py              # MMTP/ROUTE demultiplexing
├── video_sink.py         # Video output (ffplay)
├── audio_sink.py         # Audio output (alsa/pulseaudio)
├── certificate_gate.py   # YOUR CA validation (pass/fail gate)
└── cli.py               # Command-line interface
```

### Week 9-10: Integration Layer

```
□ Build real-time signal processing pipeline
  ├─ HackRF buffer management
  ├─ OFDM demod → pilot → equalization → QAM → LDPC
  ├─ Frame assembly
  └─ Performance profiling

□ Implement MMTP/ROUTE transport parsing
  ├─ Service Component Loop (SCL) parsing
  ├─ Media processing unit (MPU) assembly
  ├─ IP datagram reconstruction

□ Output: Decoded media frames ready for playback
□ Test: Measure real-time factor, identify bottlenecks
```

### Week 11: Video/Audio Integration

```
□ Video: Pass HEVC bitstream to ffmpeg decoder
  ├─ Use ffmpeg-python or subprocess
  ├─ Pipe decoded frames to display
  └─ Handle sync, timing

□ Audio: Decode AC-4 using your Python decoder
  ├─ Parse AC-4 bitstream from transport
  ├─ Decompress to PCM
  ├─ Output to PulseAudio/ALSA

□ Test: Watch/listen to real broadcast
```

### Week 12: Certificate Validation Gate

```
□ Integrate YOUR certificate authority
  ├─ Extract broadcaster certificate from LLS
  ├─ Validate against your root CA
  ├─ Check OCSP status
  ├─ Verify certificate chain

□ Certificate gate:
  ├─ If valid: Allow signal display
  ├─ If invalid: Block with message
  ├─ If revoked: Show warning

□ Test: Broadcast with A3SA cert (should fail)
         Broadcast with your CA cert (should pass)
```

### Performance Targets

```
HackRF (20 MHz bandwidth):
  ├─ OFDM demod:        ✓ Real-time (easy)
  ├─ Equalization:      ✓ Real-time (medium)
  ├─ QAM mapping:       ✓ Real-time (easy)
  ├─ LDPC:              ~ 1-1.5x real-time (with optimization)
  ├─ Transport demux:   ✓ Real-time (easy)
  └─ Overall:          ~ 1.0x real-time (feasible)
```

---

## Phase 3: Certificate Authority Platform (Week 13+)

### What You Have Now

```
✓ Working open-source ATSC 3.0 receiver
✓ Proof that your CA can validate broadcasts
✓ Reference implementation
✓ Real receiver working with your certificates
```

### Platform Development

```
Week 13-14: Service Infrastructure
□ REST API for certificate management
□ Broadcaster enrollment workflow
□ Device provisioning (certificate issuance)
□ OCSP responder (certificate status)
□ Dashboard (broadcaster management)

Week 15-16: Go-to-Market
□ Pricing tiers (free, professional, enterprise)
□ Documentation
□ Deployment guide for broadcasters
□ Support procedures

Ongoing (Month 4+):
□ Add white-label options
□ Multi-region deployment
□ Hardware security module (HSM) integration
□ Audit logging
□ SLA monitoring
```

### Revenue Model

```
Free Tier:
  └─ Up to 5 device certs/month
    ├─ Community support
    └─ Goal: Build user base

Professional Tier: $299-599/month
  ├─ Unlimited device certificates
  ├─ 1-2 broadcast signer certificates
  ├─ OCSP responder (99.9% SLA)
  ├─ Email support
  └─ Target: Small broadcasters

Enterprise Tier: Custom
  ├─ Dedicated infrastructure
  ├─ Multi-region deployment
  ├─ White-label option
  ├─ 24/7 support
  └─ Target: Large broadcasters
```

---

## Hardware Options

### Option A: HackRF One (~$320) ← RECOMMENDED

```
Specs:
✓ 1 MHz - 6 GHz (full coverage)
✓ 20 MHz bandwidth (excellent for ATSC 3.0)
✓ GNU Radio integration
✓ Transmit capable (bonus)
✓ Open-source hardware (aligns with your values)

Cons:
✗ $320 cost
✗ Slightly higher noise floor than USRP

Verdict: Best option for this project
```

### Option B: RTL-SDR — NOT SUPPORTED

```
Specs:
✗ 2.4 MHz sustained sample rate (narrower than the 6 MHz ATSC 3.0 channel)
✗ Cannot capture a whole ATSC 3.0 channel, so bootstrap + OFDM cannot decode

Verdict: Not usable for ATSC 3.0. Use HackRF One (or any SDR wider than 6 MHz).
```

### Shopping List

```
Hardware Path:
□ HackRF One: $320
□ Dipole antenna: $40
□ USB cable: $10
□ USB hub: $30 (optional, for reliability)
Total: $400

Computer:
□ Used x86 Linux laptop: $200-300 (Craigslist)
   OR use machine you already have
```

---

## Deliverables by Phase

### Phase 1 (Weeks 1-8): atsc3 Library

```
Deliverables:
✓ atsc3_receiver package (pip installable)
✓ Physical layer demodulation
✓ AC-4 audio decoder
✓ LLS/signaling parsing
✓ Real broadcast test results
✓ Documentation

GitHub: github.com/openatsc3/atsc3-receiver
License: Apache 2.0
Community: Announce on reddit, HN, etc.
```

### Phase 2 (Weeks 9-12): Software Receiver

```
Deliverables:
✓ openatsc3_receiver (real-time receiver)
✓ Working TV display
✓ Audio playback
✓ Certificate validation gate
✓ Real broadcast demo
✓ Hardware compatibility guide

GitHub: github.com/openatsc3/receiver
Demo: YouTube video of working receiver
```

### Phase 3 (Week 13+): CA Platform

```
Deliverables:
✓ REST API
✓ Web dashboard
✓ Broadcaster enrollment
✓ OCSP responder
✓ Pricing tiers
✓ Deployment documentation

GitHub: github.com/openatsc3/certificate-authority
Deploy: openatsc3.com (or your domain)
Sales: Contact broadcasters in your region
```

---

## Claude's Role (By Phase)

### Phase 1: Library Development

```
Week 1-2 OFDM:
├─ Explain time/frequency sync algorithms
├─ Debug numpy operations
├─ Help with signal visualization
└─ Test against real broadcasts

Week 3-4 Equalization:
├─ Channel estimation math
├─ Interpolation algorithms
├─ Troubleshoot convergence issues

Week 5-6 QAM:
├─ Constellation mapping design
├─ Soft-decision decoding
├─ LLR calculation

Week 7 LDPC:
├─ pyldpc integration
├─ Parameter tuning
├─ Performance debugging

Week 8 Framing:
├─ Signaling table parsing
├─ Bit extraction logic
```

### Phase 2: Receiver Integration

```
Week 9-10:
├─ Real-time pipeline architecture
├─ Performance profiling
├─ Bottleneck identification

Week 11:
├─ Video/audio integration
├─ ffmpeg subprocess handling

Week 12:
├─ Certificate validation gate
├─ Error handling
```

### Phase 3: Platform

```
├─ REST API design review
├─ Business model refinement
├─ Deployment architecture
├─ Sales strategy
```

---

## Success Criteria

### Phase 1
```
✓ Can receive real ATSC 3.0 broadcast on HackRF
✓ Decode and visualize constellation
✓ Extract and parse signaling tables
✓ Measure SNR, BER, FEC convergence
✓ Play decoded audio
```

### Phase 2
```
✓ Watch live ATSC 3.0 broadcast on your receiver
✓ Audio/video synchronized
✓ Receiver validates certificate from your CA
✓ Real-time factor ≥ 1.0x on 6-core CPU
✓ Runs for 6+ hours without crashes
```

### Phase 3
```
✓ First paid broadcaster customer
✓ 10+ broadcasters using your CA
✓ $50K+ annual revenue
✓ Production-grade infrastructure
✓ White-label option deployed
```

---

## Timeline Summary

```
Week 1-2:   OFDM demod (library)
Week 3-4:   Pilot + equalization (library)
Week 5-6:   QAM + bits (library)
Week 7:     LDPC (library)
Week 8:     Framing + signaling (library)
───────────────────────────────
Week 9-10:  Real-time pipeline (receiver)
Week 11:    Video/audio (receiver)
Week 12:    Certificate validation (receiver)
───────────────────────────────
Week 13-14: CA platform infrastructure
Week 15-16: Go-to-market, pricing, docs
───────────────────────────────

Total: 4 months to working receiver + CA demo
```

---

## Starting Point (This Week)

```
Day 1:
□ Order HackRF One
□ Verify hardware is detected
□ Test with simple frequency sweep

Day 2-3:
□ Download ATSC 3.0 standards (A/322, A/331)
□ Read physical layer section
□ Create GitHub repo structure

Day 4:
□ Set up Python project
□ Write Week 1-2 OFDM skeleton
□ Start implementing time sync

Then: Report back with first constellation plot from real broadcast
```

---

## Budget

```
Hardware:       $400-500 (HackRF path)
Software:       $0 (all open-source)
Your time:      Weeks 1-12 (full-time equivalent)
Cloud hosting:  $0-100/month (optional, when you launch CA)

Total first-year cost: $400-600 + your time + minimal hosting
Potential first-year revenue: $50K-200K (depending on adoption)
```

---

## Success Looks Like

**Month 1:** "I decoded ATSC 3.0 on HackRF and got audio working"  
**Month 2:** "I have a working Python library others can use"  
**Month 3:** "I built a complete receiver that validates my certificate authority"  
**Month 4+:** "Broadcasters are paying for my open-source CA infrastructure"

---

**You have:**
- Clear technical path (Python library → receiver → platform)
- Market validation (A3SA + SiliconDust exist, CA is needed)
- Competitive advantage (open-source, transparent, cheaper)
- 4-month timeline to working proof-of-concept
- Revenue model that works

**Ready to start Week 1?**
