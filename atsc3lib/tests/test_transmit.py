"""Synthetic transmitter loopback gate (A/322 TX -> RX).

The transmitter is the inverse of the validated receive chain.  These gates
build a frame at 8K / GI1536 / SP4_2 / QPSK 2/15 (the RF33 PLP-16 shape) from
an A/330 ALP stream wrapped in a Baseband Packet, then require the real
receiver to recover the signalling and the exact bytes back, under AWGN and a
static multipath channel.

Reference: ATSC A/322:2024-04 Sections 5.2, 6, 7, 8; A/330 5.1.
"""

import numpy as np
import pytest

from atsc3lib import baseband, ip, spec, transmit
from atsc3lib.l1_signaling import (
    FEC_BCH_16K, L1Basic, L1Detail, PLPConfig)
from atsc3lib.payload import MOD_NAME, decode_streams
from atsc3lib.receiver import decode_plp_payload, decode_signaling

STRUCTURE = 27                      # 8K / GI1536 / SP4_2, L1-Basic mode 3
FFT, GI = 8192, 1536
BSID = 540
KPAYLOAD_BYTES = 249                # one 16K QPSK 2/15 Baseband Packet


def _l1(plp: PLPConfig) -> tuple:
    lb = L1Basic(
        version=0, mimo_scattered_pilot_encoding=0, lls_flag=1,
        time_info_flag=0, return_channel_flag=0, papr_reduction=0,
        frame_length_mode=1, time_offset=0, num_subframes=0,
        preamble_num_symbols=0, preamble_reduced_carriers=0,
        l1_detail_content_tag=0, l1_detail_size_bytes=64,
        l1_detail_fec_type=2, l1_detail_additional_parity_mode=0,
        l1_detail_total_cells=880, first_sub_mimo=0, first_sub_miso=0,
        first_sub_fft_size=0, first_sub_reduced_carriers=0,
        first_sub_guard_interval=6, first_sub_num_ofdm_symbols=34,
        first_sub_scattered_pilot_pattern=2, first_sub_scattered_pilot_boost=1,
        first_sub_sbs_first=1, first_sub_sbs_last=1, crc_ok=True,
        raw={'L1B_time_offset': 0, 'L1B_additional_samples': 0,
             'L1B_reserved': (1 << 48) - 1})
    sf0 = {'index': 0, 'frequency_interleaver': 1, 'sbs_null_cells': 127,
           'num_plp': 0, 'plps': [plp]}
    ld = L1Detail(version=0, num_rf=0, time_sec=None, bsid=BSID,
                  subframes=[sf0], reserved_len=0, reserved_all_ones=True,
                  crc_ok=True, raw={})
    return lb, ld


def _plp(size: int) -> PLPConfig:
    return PLPConfig(
        plp_id=16, lls_flag=0, layer=0, start=0, size=size,
        scrambler_type=0, fec_type=FEC_BCH_16K, modulation=0, code_rate=0,
        ti_mode=2, ti_fec_block_start=None, hti_inter_subframe=0,
        hti_num_ti_blocks=0, hti_num_fec_blocks=0, hti_cell_interleaver=0,
        cti_depth=None, cti_start_row=None, ti_extended_interleaving=0,
        ldm_injection_level=None)


def _bbp_with_udp(payload_len: int = 217, seed: int = 0) -> bytes:
    """A Baseband Packet whose ALP stream is one IPv4/UDP datagram."""
    rng = np.random.default_rng(seed)
    body = rng.integers(0, 256, payload_len, dtype=np.uint8).tobytes()
    udp = ip.build_ipv4_udp(b"\xac\x12\x81\x14", b"\xe0\x00\x23\x2e",
                            1234, 4937, body)
    alp = transmit.build_alp_stream([udp])[0]
    return baseband.build_baseband_packet(alp, pointer=0)


def _awgn(iq: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    power = float(np.mean(np.abs(iq) ** 2))
    sigma = np.sqrt(power / (2 * 10 ** (snr_db / 10)))
    noise = sigma * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))
    return (iq + noise).astype(np.complex64)


def _multipath(iq: np.ndarray, seed: int = 0) -> np.ndarray:
    """A mild static two-tap channel (the dense estimator's regime)."""
    return (iq + 0.35 * np.roll(iq, 7) * np.exp(0.4j)).astype(np.complex64)


class TestBuildFrame:
    def test_l1_round_trip(self):
        plp = _plp(8100)
        lb, ld = _l1(plp)
        frame = transmit.build_frame(lb, ld, _bbp_with_udp(), plp=plp,
                                     structure=STRUCTURE)
        res = decode_signaling(frame.iq.astype(np.complex64),
                               spec.MAIN_RATE_HZ)
        assert res.error is None
        assert res.l1_basic_ok and res.l1_detail_ok
        assert res.l1_detail.bsid == BSID
        assert res.preamble_structure == STRUCTURE
        p = res.plps[0][1]
        assert (p.plp_id, p.size, p.modulation, p.code_rate, p.fec_type) == \
               (16, 8100, 0, 0, FEC_BCH_16K)

    def test_frame_length_matches_geometry(self):
        plp = _plp(8100)
        lb, ld = _l1(plp)
        frame = transmit.build_frame(lb, ld, _bbp_with_udp(), plp=plp)
        boot = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                         * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
        n_preamble = lb.preamble_num_symbols + 1
        assert len(frame.iq) == boot + (FFT + GI) * (
            n_preamble + frame.n_data_symbols)
    def test_payload_round_trip(self):
        plp = _plp(8100)
        lb, ld = _l1(plp)
        bbp = _bbp_with_udp()
        frame = transmit.build_frame(lb, ld, bbp, plp=plp,
                                     structure=STRUCTURE)
        res = decode_signaling(frame.iq.astype(np.complex64),
                               spec.MAIN_RATE_HZ)
        res, payload = decode_plp_payload(
            frame.iq.astype(np.complex64), spec.MAIN_RATE_HZ, plp_id=16,
            result=res)
        assert payload is not None
        assert payload.n_converged == 1
        assert payload.baseband_packets == [bbp]
        streams = decode_streams(payload)
        assert len(streams.datagrams) == 1
        assert streams.datagrams[0].dst_ip == b"\xe0\x00\x23\x2e"

    @pytest.mark.parametrize('snr_db', [30.0, 22.0, 18.0])
    def test_awgn_decode(self, snr_db):
        plp = _plp(8100)
        lb, ld = _l1(plp)
        bbp = _bbp_with_udp()
        frame = transmit.build_frame(lb, ld, bbp, plp=plp)
        rx = _awgn(frame.iq, snr_db)
        res = decode_signaling(rx, spec.MAIN_RATE_HZ)
        assert res.l1_detail_ok
        _, payload = decode_plp_payload(rx, spec.MAIN_RATE_HZ, plp_id=16,
                                        result=res)
        assert payload is not None and payload.baseband_packets == [bbp]

    def test_static_multipath_decode(self):
        plp = _plp(8100)
        lb, ld = _l1(plp)
        bbp = _bbp_with_udp()
        frame = transmit.build_frame(lb, ld, bbp, plp=plp)
        res = decode_signaling(_multipath(frame.iq), spec.MAIN_RATE_HZ)
        assert res.l1_detail_ok
        _, payload = decode_plp_payload(
            _multipath(frame.iq), spec.MAIN_RATE_HZ, plp_id=16, result=res)
        assert payload is not None and payload.baseband_packets == [bbp]
