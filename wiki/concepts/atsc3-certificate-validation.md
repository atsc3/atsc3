---
created: 2026-08-30
updated: 2026-08-30
sources: [OPENATSC3_PROJECT_PLAN.md]
tags: [certificate-authority, security, broadcast, pki]
---

# ATSC 3.0 Certificate Validation

## Overview

ATSC 3.0 broadcasts include digital certificates for content authentication and service authorization. A certificate authority (CA) validates broadcaster certificates, enabling receivers to distinguish legitimate broadcasts from unauthorized signals.

## Use Case

**Problem:** A3SA (Advanced Television Systems Committee Security Authority) and SiliconDust exist as CAs, but there's demand for:
- Open-source, transparent CA infrastructure
- Lower-cost alternatives for small broadcasters
- Multi-CA support (not locked to single provider)

**Solution:** Open-source CA platform with:
- Transparent operations
- Competitive pricing ($299-599/month vs enterprise contracts)
- Multi-CA architecture (broadcasters can choose)

## Certificate Validation Gate (Week 12)

### Integration Point

Receiver validates certificates **after** decoding transport layer, **before** displaying content:

```
Signal → OFDM → QAM → LDPC → Framing → Transport → [CERT GATE] → Display
                                              ↓
                                         Extract cert
                                         Validate chain
                                         Check OCSP
```

### Validation Steps

1. **Extract broadcaster certificate** from LLS (Low-Level Signaling) tables
2. **Validate certificate chain** against root CA
3. **Check OCSP status** (certificate not revoked)
4. **Verify certificate validity period** (not expired)

### Gate Behavior

| Certificate Status | Action |
|-------------------|--------|
| Valid (your CA) | ✓ Allow signal display |
| Valid (other CA) | ⚠ Show warning, allow if configured |
| Invalid | ✗ Block with error message |
| Revoked | ✗ Show revocation warning |

## CA Platform Architecture (Phase 3)

### Core Services

**REST API:**
- Certificate issuance/renewal/revocation
- Broadcaster enrollment
- Device provisioning
- OCSP responder endpoint

**Web Dashboard:**
- Broadcaster self-service
- Certificate status monitoring
- Usage analytics

**OCSP Responder:**
- Real-time certificate status checks
- 99.9% SLA target
- Caching for performance

### Revenue Tiers

**Free Tier:**
- Up to 5 device certificates/month
- Community support
- Goal: Build user base

**Professional ($299-599/month):**
- Unlimited device certificates
- 1-2 broadcast signer certificates
- OCSP responder (99.9% SLA)
- Email support
- Target: Small broadcasters

**Enterprise (Custom):**
- Dedicated infrastructure
- Multi-region deployment
- White-label option
- 24/7 support
- Target: Large broadcasters

## Technical Requirements

### Certificate Formats

- X.509 v3 certificates
- RSA or ECDSA keys
- Standard extensions (keyUsage, extendedKeyUsage)
- OCSP signing certificates

### Security

- HSM (Hardware Security Module) for root key storage
- Audit logging for all operations
- Multi-sig for critical operations
- Regular security audits

### Performance

- OCSP response time: <100ms (cached)
- Certificate issuance: <5 minutes automated
- Support 1000+ concurrent broadcasters

## Implementation Timeline

**Week 12:** Certificate gate in receiver (validate against hardcoded CA)

**Weeks 13-14:** CA platform infrastructure
- REST API
- Certificate lifecycle management
- OCSP responder
- Web dashboard

**Weeks 15-16:** Go-to-market
- Documentation
- Deployment guides
- Pricing tiers
- Support procedures

## Success Metrics

- First paid broadcaster customer (Month 4)
- 10+ broadcasters using CA (Month 6)
- $50K+ annual revenue (Year 1)
- 99.9% OCSP uptime

## See Also

- [[openatsc3-project-plan]] - Full project plan
- [[atsc3-physical-layer]] - Signal processing pipeline

## References

- OPENATSC3_PROJECT_PLAN.md
- ATSC A/321 System Discovery & Signaling
- X.509 Certificate Specification (RFC 5280)
- OCSP Specification (RFC 6960)
