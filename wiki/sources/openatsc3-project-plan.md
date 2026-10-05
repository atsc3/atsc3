---
created: 2026-08-30
updated: 2026-08-30
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [project-plan, ats3, python, rust, certificate-authority]
---

# OpenATSC3 Project Plan Summary

## Overview

12-16 week project to build an open-source ATSC 3.0 ecosystem consisting of:
1. Python library for ATSC 3.0 signal processing
2. Software receiver with certificate validation
3. Certificate Authority platform for commercial deployment

## Three-Phase Architecture

### Phase 1 (Weeks 1-8): ATSC3 Python Library

Core signal processing pipeline:
- **OFDM demodulation** - Time/frequency sync, cyclic prefix removal, FFT
- **Channel estimation** - Pilot extraction, equalization, phase tracking
- **QAM demodulation** - Constellation mapping, soft-decision LLR generation
- **LDPC decoding** - Belief propagation using pyldpc
- **Framing** - Bootstrap parsing, LLS signaling extraction
- **AC-4 audio decoder** - Pure Python implementation

Library structure: `atsc3_receiver/` with modules for each processing stage.

### Phase 2 (Weeks 9-12): Software Receiver

Real-time integration:
- HackRF hardware support
- Signal processing pipeline (OFDM → pilot → QAM → LDPC → framing)
- MMTP/ROUTE transport demultiplexing
- Video output (HEVC via ffmpeg)
- Audio output (AC-4 Python decoder)
- **Certificate validation gate** - Blocks invalid/untrusted certificates

Performance target: Real-time on 6-core x86 CPU with HackRF One.

### Phase 3 (Week 13+): Certificate Authority Platform

Commercial infrastructure:
- REST API for certificate management
- Broadcaster enrollment workflow
- Device provisioning
- OCSP responder (99.9% SLA)
- Web dashboard
- Multi-tier pricing (Free, Professional $299-599/mo, Enterprise)

## Hardware Requirements

**Recommended:** HackRF One (~$320)
- 1 MHz - 6 GHz coverage
- 20 MHz bandwidth (sufficient for ATSC 3.0)
- Open-source hardware

**RTL-SDR:** not supported — 2.4 MHz bandwidth is narrower than the 6 MHz ATSC 3.0 channel.

## Revenue Model

- **Free tier:** Up to 5 device certs/month
- **Professional:** $299-599/month (unlimited device certs, 1-2 broadcast signers)
- **Enterprise:** Custom (dedicated infrastructure, white-label)

Target: $50K-200K first-year revenue with 10+ broadcaster customers.

## Success Criteria

**Phase 1:** Decode real ATSC 3.0 broadcast, extract signaling, play audio

**Phase 2:** Watch live TV with certificate validation, 1.0x real-time factor

**Phase 3:** First paid customer, production CA infrastructure

## Key Dependencies

- numpy, scipy (signal processing)
- pyldpc (LDPC decoding)
- hackrf (SDR control; RTL-SDR not supported — too narrow)
- ffmpeg (video decoding)
- openatsc3_cert (certificate validation library)

## Timeline

```
Weeks 1-2:   OFDM demodulation
Weeks 3-4:   Channel estimation + equalization
Weeks 5-6:   QAM demodulation
Week 7:      LDPC decoding
Week 8:      Framing + signaling
─────────────────────────────
Weeks 9-10:  Real-time pipeline integration
Week 11:     Video/audio integration
Week 12:     Certificate validation gate
─────────────────────────────
Weeks 13-16: CA platform + go-to-market
```

## See Also

None yet.

## References

- OPENATSC3_PROJECT_PLAN.md (source document)
