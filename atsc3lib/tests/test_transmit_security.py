"""Certificate hook carried over the real PHY (A/331 + A/360 through A/322).

No receivable stream carries our own signature, so the own-CA track is gated
synthetically.  This gate raises that from the A/331-layer injection in
``test_security.py`` to the **full physical layer**: build the LLS
CertificationData (0x06) and signed tables (0x07) plus a standalone SLT (0x01)
with ``openatsc3_pki``, carry them in a generated ATSC 3.0 frame
(``atsc3lib.transmit``), and require the receiver's real
``decode_signaling`` -> ``decode_plp_streams`` -> ``security.verify_streams``
path to verify the chain.  Negative gates (wrong root, tampered SignedMultiTable)
must fail through the same path.
"""

import datetime as dt
import gzip

import numpy as np
import pytest

pki = pytest.importorskip("openatsc3_pki")

from atsc3lib import baseband, ip, security, spec, transmit
from atsc3lib.ldpc_exact import NINNER_SHORT
from atsc3lib.l1_signaling import FEC_BCH_16K, L1Basic, L1Detail, PLPConfig
from atsc3lib.receiver import decode_plp_streams
from openatsc3_pki import ca, cdt, keys, ocsp
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_ocsp_responder,
    issue_signaling_signer,
)

STRUCTURE = 27
BSID = 540
KPAYLOAD_BYTES = 249
SLT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<SLT xmlns="tag:atsc.org,2016:XMLSchemas/ATSC3/Delivery/SLT/1.0/" '
    f'bsid="{BSID}"><Service serviceId="1" majorChannelNo="32" '
    'minorChannelNo="1" shortServiceName="WHUT">'
    '<BroadcastSvcSignaling slsProtocol="1" '
    'slsDestinationIpAddress="239.255.32.1" slsDestinationUdpPort="8321"/>'
    '</Service></SLT>'
).encode()


@pytest.fixture
def signed_lls(tmp_path):
    """The LLS table payloads (header included) for a valid own-CA chain."""
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root(common_name="OpenATSC3 Root CA")
    a.issue_issuing()
    signer = a.issue_broadcaster("WHUT", (BSID,))
    signer_key = a.load_broadcaster_key("WHUT")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(
        SubjectInfo(common_name="WHUT-CDT-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization="WHUT"),
        cdt_key, a.load_issuing(), a.load_issuing_key(), (BSID,))
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, a.load_issuing(), a.load_issuing_key())
    store = ocsp.StatusStore()

    def resp(cert, issuer):
        return ocsp.respond(cert, issuer, resp_cert, resp_key, store,
                            this_update=now,
                            next_update=now + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[a.load_issuing().der()], signing_cert=signer,
        cdt_signer_cert=cdt_cert, cdt_signer_key=cdt_key,
        ocsp_responses=[resp(a.load_issuing(), a.load_issuing()),
                        resp(signer, a.load_issuing()),
                        resp(cdt_cert, a.load_issuing())],
        signing_time=now)
    smt = cdt.signed_multitable(
        [cdt.LlsTable(0x01, 1, gzip.compress(SLT_XML)),
         cdt.LlsTable(0x03, 1, b"\x00" * 8)],
        signer, signer_key, signing_time=now)

    def lls_table(table_id, version, body):
        return bytes([table_id, 0, 0, version]) + body

    tables = [
        lls_table(0x06, 1, table.gzip_bytes()),
        lls_table(0x07, 1, smt),
        lls_table(0x01, 1, gzip.compress(SLT_XML)),
    ]
    return {"ca": a, "now": now, "tables": tables, "smt": smt}


def _plp(size: int) -> PLPConfig:
    return PLPConfig(
        plp_id=16, lls_flag=1, layer=0, start=0, size=size,
        scrambler_type=0, fec_type=FEC_BCH_16K, modulation=0, code_rate=0,
        ti_mode=2, ti_fec_block_start=None, hti_inter_subframe=0,
        hti_num_ti_blocks=0, hti_num_fec_blocks=0, hti_cell_interleaver=0,
        cti_depth=None, cti_start_row=None, ti_extended_interleaving=0,
        ldm_injection_level=None)


def _l1(n_fec: int) -> tuple:
    size = n_fec * (NINNER_SHORT // 2)
    plp = _plp(size)
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


def _build(tables, tamper_smt=False):
    """Modulate the LLS tables into an IQ frame at 8K QPSK 2/15."""
    from atsc3lib import payload as payload_mod
    plp = _plp(0)
    stream, boundaries = transmit.build_alp_stream(
        [ip.build_lls_udp(t) for t in tables])
    packets = baseband.pack_baseband_stream(stream, boundaries,
                                            KPAYLOAD_BYTES)
    bbp_stream = b"".join(packets)
    n_fec = len(packets)
    lb, ld = _l1(n_fec)
    frame = transmit.build_frame(lb, ld, bbp_stream, plp=ld.subframes[0]['plps'][0],
                                 n_fec=n_fec, structure=STRUCTURE)
    return frame


def _decode(frame, plp_size):
    rx = frame.iq.astype(np.complex64)
    res, streams = decode_plp_streams(rx, spec.MAIN_RATE_HZ, plp_id=16)
    return res, streams


def test_signed_lls_reaches_receiver_over_phy(signed_lls):
    frame = _build(signed_lls["tables"])
    res, streams = _decode(frame, None)
    assert res.l1_detail_ok, res.error
    assert streams is not None
    ids = {t.table_id for t in streams.lls}
    assert 0x06 in ids and 0x07 in ids and 0x01 in ids
    assert streams.slt is not None and streams.slt.bsid == (BSID,)


def test_verify_own_ca_over_phy(signed_lls):
    frame = _build(signed_lls["tables"])
    _, streams = _decode(frame, None)
    report = security.verify_streams(streams, [signed_lls["ca"].load_root()],
                                     now=signed_lls["now"])
    assert report.ok, security.describe(report)
    assert streams.verification is report


def test_wrong_root_over_phy_fails(signed_lls, tmp_path):
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root()
    frame = _build(signed_lls["tables"])
    _, streams = _decode(frame, None)
    report = security.verify_streams(streams, [other.load_root()],
                                     now=signed_lls["now"])
    assert not report.ok


def test_tampered_smt_over_phy_fails(signed_lls):
    tables = list(signed_lls["tables"])
    body = bytearray(tables[1])
    body[8] ^= 0xFF
    tables[1] = bytes(body)
    frame = _build(tables)
    _, streams = _decode(frame, None)
    assert streams is not None
    report = security.verify_streams(streams, [signed_lls["ca"].load_root()],
                                     now=signed_lls["now"])
    assert not report.ok
