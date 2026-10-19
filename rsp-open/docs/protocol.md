# RSP1B protocol notes

These notes are the evidence behind `librsp`. They are observations from a
genuine RSP1B (`1df7:3050`, serial `240523A360`, bcdDevice 2.07) and the
mainline Linux `msi2500`/`msi001` register descriptions. They are not copied
from the vendor service.

## USB shape

- Vendor/product: `1df7:3050`, USB 2.0 high speed, self-powered.
- One interface (0), vendor-specific:
  - alt 0: no endpoints (control only)
  - alt 1: one **isochronous** IN endpoint `0x81`, `wMaxPacketSize` encoded as
    `3 x 1024` bytes per microframe
  - alt 2: one isochronous IN endpoint, `1 x 1024`
  - alt 3: one **bulk** IN endpoint, 512 bytes (fallback not used yet)
- Control transfers are `bmRequestType = 0x40` (vendor, host-to-device) with
  `bRequest` in {`0x41`, `0x43`, `0x45`}. For `0x41` the 32-bit payload is
  encoded as `wValue = payload & 0xffff`, `wIndex = payload >> 16`, no data
  stage. The RSP1B front end also uses a second vendor request, `0x49`, with
  `wValue`/`wIndex` written directly.

## Vendor sequence (validated)

The RSP1B's own API does **not** use the RSP1 kernel sequence. Captured with
`usbmon` while `atsc3-capture` streamed 587 MHz / 10 MS/s / 8 MHz / IFGR 40 /
RFGR 4 (`SoapySDRUtil`/`atsc3-capture`):

```
41 8008 00ee     ; RSP1B register 0x00ee (the RSP1 kernel writes 0x0060)
41 0c05 0000
41 0e09 0000     ; MSi001 word 0x00000e
41 0000 0002
41 0102 0048
41 8509 28bb     ; MSi001 word 0x28bb85  (vendor PLL word)
41 9407 000c     ; CS8, 504 samples
41 f609 2003     ; MSi001 word 0x0320f6  (vendor filter/AGC word)
41 8009 05f4     ; MSi001 word 0x05f480  (B45, zero IF, 8 MHz)
49 12df 0040     ; RFGR 4 front-end (RFGR 0 is 0x12ff)
49 13bd 0040
49 0000 0040
49 0100 0040
41 f309 00ff     ; MSi001 word 0x00fff3
41 e209 1855     ; MSi001 word 0x5518e2
41 0d09 0000     ; MSi001 word 0x00000d
41 0e09 0000     ; MSi001 word 0x00000e
41 8109 0102     ; MSi001 gain: LNA on, mixer on, BB 40 dB (IFGR 40)
49 12ff 0040     ; second front-end write (RFGR 4 uses 0x12df for the first)
49 13bd 0040
41 0004 0000
41 0703 01da     ; ADC PLL for 10 MS/s (same as the kernel value)
43               ; start
```

The gain word follows the A/322/API BB gain reduction: IFGR 40 is
`0x010281` (`8109 0102`), IFGR 44 is `0x0102c1` (`c109 0102`), 45 is
`0x0102d1` (`d109 0102`), 48 is `0x010301` (`0109 0103`). The first `0x49`
value is **`0x12df`** for RFGR 4; `0x12ff` is RFGR 0 and `0x13ad` is RFGR 9
(which also turns LNA/mixer off, `0x013281`). The vendor trace for RFGR 4
showed a single `0x12df` update after the RFGR 0 init; the open driver's
full init uses `0x12df` for the first write and `0x12ff` for the second.
The clean, non-clipping gain is **IFGR 44** (RMS 0.361 vendor / 0.373 open);
IFGR 40 is ~4 dB hotter and clips.


The vendor sequence is in `src/rsp1b_tables.c`; `tests/test_tables.c` pins the
exact words. It is used for the validated 587 MHz / 10 MS/s / 8 MHz / IFGR 40 /
RFGR 4 configuration. With it the capture has **0 sequence gaps** (the
kernel-derived sequence had one startup gap) and 0 overruns.

The vendor close is also replayed:

```
45               ; stop
49 12ff 0040
41 1c09 0000     ; MSi001 standby
41 0009 0000
41 ed09 0100
41 4e09 0350
41 0009 0630
41 0e09 0080
41 0003 0913     ; MSi2500 shutdown
41 0405 0000
(set alt 0)
```

This close sequence (plus the `0x00ee` register) is what lets the vendor
service re-open the device after `rsp-capture`; the earlier kernel-derived
sequence wrote `0x00608008` and did not restore the standby state, which left
the vendor API failing `FailedVerification6` until a power cycle. After the
vendor sequence/close, `SoapySDRUtil --probe=driver=sdrplay` succeeds directly.

The `0x49` writes are not semantically decoded; they are replayed exactly for
the validated rung. They are believed to select the RSP1B front-end filter /
attenuator path.

## Fallback MSi2500 ADC/PLL sequence

For configurations outside the validated vendor rung, `librsp` falls back to
the kernel-derived MSi2500/MSi001 words. It uses the RSP1B `0x00ee` register
(not the RSP1 `0x0060`) so the device stays re-openable by the vendor API.


### Kernel MSi2500 words (fallback)

The kernel driver's `msi2500_set_usb_adc()` writes this sequence; the RSP1B
accepts it, but the first word is the RSP1 register `0x00608008`; `librsp`
uses the RSP1B `0x00ee8008` instead so the vendor service can re-open the
device. Captured with dynamic debug (the first line is the kernel's original
RSP1 value; the driver emits `0x00ee8008`):

```
40 41 08 80 60 00   payload 0x00608008   (driver: 0x00ee8008)
40 41 05 0c 00 00   payload 0x00000c05
40 41 00 00 02 00   payload 0x00020000
40 41 02 01 48 00   payload 0x00480102
40 41 08 80 f3 00   payload 0x00f38008
40 41 07 94 0c 00   payload 0x000c9407   CS8, 504 samples/1024-byte packet
40 41 04 00 00 00   payload 0x00000004
40 41 03 07 da 01   payload 0x01da0703   ADC PLL for 10 MS/s
```

`msi2500_adc_words()` reproduces the last three words for any rate; the test
`tests/test_tables.c` pins the 10 MS/s values.

### Kernel MSi001 words (fallback)

The MSi001 is written through the MSi2500 SPI adapter: the 24-bit MSi001 word
is sent as `0x09 | b0<<8 | b1<<16 | b2<<24` with request `0x41`. The
kernel-derived words for 587 MHz / 8 MHz / LNA off / mixer on / BB 40 dB are:

```
40 41 09 0e 00 00   word 0x00000e
40 41 09 03 00 00   word 0x000003
40 41 09 80 f4 05   word 0x05f480   mode B45, zero IF, 8 MHz
40 41 09 85 01 28   word 0x280185   PLL k_thresh = 24
40 41 09 b2 00 18   word 0x1800b2   PLL k_frac = 11, div_n = 24
40 41 09 81 22 01   word 0x012281   gain (LNA off, mixer on, BB 40 dB)
40 41 09 f6 ff 3f   word 0x3ffff6   kernel filter/AGC word
```

`msi001_tuner_words()` computes these; the test pins the values. The **vendor
sequence uses different PLL/filter/gain words** (`0x28bb85`, `0x0320f6`,
`0x010281`, plus `0x00fff3` and `0x5518e2`) and is preferred for the validated
rung.

The fallback sends the MSi2500 words, then the MSi001 words, once. The vendor
sequence is sent as captured and ends with the ADC PLL; both then submit the
isochronous URBs and send `0x43` (start). Stop is the vendor close sequence
above.


## Streaming packet layout

Every isochronous frame observed is 3072 bytes: three 1024-byte transactions.
Each 1024-byte transaction is:

```
offset 0..3    sample number (little-endian uint32)
offset 4..15   12 bytes of non-sample data
offset 16..1023 1008 bytes = 504 interleaved int8 I/Q pairs
```

Sequence numbers advance by 504 per transaction. At 10 MS/s the device sends
1512 samples in about 6612 of the 8000 microframes per second; the other
microframes are zero-length and are skipped. The effective sample rate is
10 MS/s. The stream is 8-bit at 10 MS/s (the ADC resolution table in the
RSP1A/B data sheet: 8-bit above 9.216 MS/s).

## Startup and DC

With the kernel-derived sequence, the first stream after `START` had ~5k stale
samples and one sample-counter jump (~12k samples). `librsp` discards a 0.5 s
prime and measures the mean I/Q during it. The vendor sequence does not show
that startup jump: the 8 s vendor-sequence capture had **0 sequence gaps** and
0 overruns. The prime is still used for the DC estimate.

Without DC removal, the strong DC term makes the bootstrap detector lock on a
false candidate at sample 0. A one-pole high-pass at 100 Hz was also tried and
cost one of the 74 PLP-0 FEC blocks; the prime-mean is used instead.

The transfer path uses a bounded lock-free SPSC ring (4M IQ samples). The
isochronous callback never blocks on the consumer, which reduced sequence
gaps from ~22/2 s to 0 with the vendor sequence.

## On-air evidence

Final capture: the vendor sequence, 587 MHz, 10 MS/s, 8 MHz, **IFGR 44**
(the clean, non-clipping gain; IFGR 40 is ~4 dB hotter), RFGR 4, 8 s, 306 MB
CS16, RMS 0.373, 0.036% of samples at the 8-bit rails.

- `atsc3lib` bootstrap: structure 27 (8K, GI 1536, L1-Basic mode 3)
- L1-Basic CRC OK; L1-Detail CRC OK; BSID 540
- PLP-0 (64QAM-NUC 11/15): **74/74 FEC blocks**
- PLP-16 (QPSK 2/15): **1/1 FEC block**, 0 ALP/UDP (as signalled)
- PLP-1 (256QAM-NUC 11/15, subframe 1): **0/117** with the open capture;
  the vendor capture remains 117/117. Open item.

The gain/clipping part is solved: the vendor RFGR 4 front-end is `0x12df`
(not `0x12ff`), and the clean level is IFGR 44 (vendor RMS 0.361, open 0.373).
The remaining open deficit is the **IQ image/delay**: the raw open capture has
circularity 0.0497 and lag-1 0.0181, while the vendor API's corrected capture
has 0.00031 and 0.00236. The vendor applies DC/IQ/AGC correction in software;
the open driver currently only removes the prime-mean DC. A blind
widely-linear/2-tap IQ correction is the next fix.


