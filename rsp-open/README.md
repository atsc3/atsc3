# rsp-open

An independent, open-source userspace driver for the **SDRplay RSP1B**
(Mirics MSi2500 USB bridge + MSi001 tuner), built on libusb.

This is **not** a wrapper around the SDRplay API. It does not load
`libsdrplay_api.so`, does not require `sdrplay.service`, and does not use the
vendor binary. It speaks the MSi2500 control/stream protocol directly and
programs the MSi001 tuner over the bridge's SPI register.

Status: **first on-air rung working**, now replaying the vendor API's own
control sequence for the RSP1B. At 587 MHz (RF33, WHUT ATSC 3.0 mux),
10 MS/s, 8 MHz bandwidth, **IFGR 44 / RFGR 4** (the clean, non-clipping
gain), an 8 s capture has **no mid-stream sequence gaps, 0 overruns**, and decodes
**PLP-0 74/74 FEC blocks** (64QAM-NUC 11/15) and **PLP-16 1/1**
(QPSK 2/15) through the existing `atsc3lib` receiver. PLP-1 (256QAM-NUC
11/15) is currently 0/117 on the open capture; the vendor front end still
closes it. That is the main open performance item: the raw open IQ has an
image/delay imbalance (circularity 0.0497 vs. vendor 0.00031) after the
vendor's `0x12df` RFGR-4 front-end write and the clean IFGR 44 gain.

The vendor API and this driver now coexist: after `rsp-capture` closes
(including the captured MSi001 standby / MSi2500 shutdown sequence and the
`0x00ee` RSP1B register), `SoapySDRUtil --probe=driver=sdrplay` succeeds
without a power cycle.

## Build

```bash
make
make test
sudo make install-soapy   # installs the SoapySDR module driver=rsp-open
```

Requires `libusb-1.0` development headers and `pkg-config`.

## Capture

Stop the vendor service first. It and this driver both own the USB device:

```bash
sudo systemctl stop sdrplay.service
./rsp-capture -f 587 -r 10e6 -b 8 --ifgr 44 --rfgr 4 -d 8 -o out/rf33.iq
```

The driver primes for 0.5 s after `START`, estimates and removes the DC
offset, then streams interleaved int16 IQ (CS16). `--cs8` writes int8
instead.

Decode with the receiver:

```bash
atsc3-decode out/rf33.iq --rate 10e6 --fmt cs16 --plp 0
```

The CLI's default bootstrap search covers a bounded window. A capture that
starts mid-frame may need the full-search path; in Python:

```python
from atsc3lib.receiver import decode_signaling
res = decode_signaling(iq, 10e6, full_search=True)
```

## Use from atsc3lib

`make install-soapy` puts `libRspOpenSupport.so` in the system SoapySDR module
directory. Stop the vendor service, then select the driver:

```bash
sudo systemctl stop sdrplay.service
atsc3-capture -f 587 -d 8 --driver rsp-open -o out/rf33.iq
atsc3-live --freq 587e6 --plp 0 --driver rsp-open
```

`ATSC3_SDR_DRIVER=rsp-open` selects it for both commands. `sdrbindings` sees
it directly:

```python
import sdrbindings
print(sdrbindings.enumerate("rsp-open"))
```

`SoapySDRUtil --find="driver=rsp-open"` and `--probe` also work.

## What is implemented

- libusb enumeration/claim of `1df7:3050`, isochronous interface 0 alt 1
  (EP `0x81`, `3 x 1024` bytes per microframe), alt 0 on close.
- The **vendor API's own control sequence** for the validated RSP1B rung
  (587 MHz / 10 MS/s / 8 MHz / IFGR 44 / RFGR 4): the `0x00ee` RSP1B
  register (not the RSP1's `0x0060`), the MSi001 PLL/gain words, the `0x12df`
  RFGR-4 front-end write, and the `0x49` front-end writes. The gain word is
  patched for any IFGR 20–59 (40 = `0x010281`, 44 = `0x0102c1`,
  45 = `0x0102d1`, 48 = `0x010301`). This is in `src/rsp1b_tables.c` and
  gated by `tests/test_tables.c`.
- MSi2500 ADC/PLL and CS8 stream format (10 MS/s); MSi001 band/PLL/
  bandwidth/gain programming for other configurations (fallback path).
- 16-byte-per-1024-byte packet parsing (504 int8 IQ pairs per transaction),
  sequence-gap accounting, idle-microframe handling.
- A bounded lock-free SPSC ring (4M IQ samples) with an overrun counter.
- A 0.5 s prime that discards the device startup and measures the DC offset;
  no unbounded buffering, no unbounded search.
- A vendor-compatible close: stream stop, MSi001 standby, MSi2500 shutdown,
  and alt 0. This leaves the device in a state where the vendor service can
  re-open it; `SoapySDRUtil --probe=driver=sdrplay` succeeds after a capture.

## Limits / open items

- **RSP1B only.** The exact vendor sequence is air-proven at 587 MHz,
  10 MS/s, 8 MHz, IFGR 44, RFGR 4. Other frequencies/rates use the computed
  fallback and are not air-proven; other RFGR values need their own captured
  front-end (`0x49`) sequence.

## Protocol and evidence

See [docs/protocol.md](docs/protocol.md) for the captured control words,
packet layout, startup behaviour and the on-air decode results.

## Legal

This is an independent interoperability implementation. It was written from
public USB descriptors, the public SDRplay API specification, the mainline
Linux `msi2500`/`msi001` register descriptions, and USB traffic observed from
the device. It contains no vendor source, headers, firmware or binaries.
"SDRplay" and "RSP1B" are trademarks of SDRplay Limited; this project is
unofficial and not endorsed by SDRplay.

License: Apache-2.0 (see [LICENSE](LICENSE)).
