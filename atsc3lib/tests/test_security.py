"""A/331-layer gate for own-CA signed signaling (A/360).

No receivable stream carries our signature, so this rung is validated
synthetically at the network layer: build a CertificationData table and a
signed SLT/SignedMultiTable, wrap them as LLS UDP datagrams, run them through
the real ``ip.parse_lls`` -> ``payload.decode_streams`` path, and verify.  The
negative gates (tamper, wrong root, wrong bsid) must fail.

The cryptography lives in ``openatsc3_pki``; this test exercises the
``atsc3lib.security`` adapter against it.
"""

import datetime as dt
import gzip

import pytest

pki = pytest.importorskip("openatsc3_pki")

from atsc3lib import ip as ip_layer
from atsc3lib import payload as payload_mod
from atsc3lib import security
from openatsc3_pki import ca, cdt, keys, ocsp
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_ocsp_responder,
    issue_signaling_signer,
)

BSID = 540
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
def wired(tmp_path):
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

    # Signed LLS: an SLT (0x01) and a SystemTime (0x03), inside a
    # SignedMultiTable (0x07).
    smt = cdt.signed_multitable(
        [cdt.LlsTable(0x01, 1, gzip.compress(SLT_XML)),
         cdt.LlsTable(0x03, 1, b"\x00" * 8)],
        signer, signer_key, signing_time=now)

    def lls_table(table_id: int, version: int, body: bytes) -> bytes:
        return bytes([table_id, 0, 0, version]) + body

    datagrams = [
        _lls_datagram(lls_table(0x06, 1, table.gzip_bytes())),
        _lls_datagram(lls_table(0x07, 1, smt)),
        # A/331 5.9: the standalone (unsigned) SLT is also transmitted, and is
        # what supplies the receiver's parsed bsid set.
        _lls_datagram(lls_table(0x01, 1, gzip.compress(SLT_XML))),
    ]
    return {"ca": a, "now": now, "datagrams": datagrams, "smt": smt,
            "signer": signer, "signer_key": signer_key}


def _lls_datagram(body: bytes):
    return ip_layer.UdpDatagram(
        src_ip=b"\xac\x12\x81\x14", dst_ip=ip_layer.LLS_IP,
        src_port=1234, dst_port=ip_layer.LLS_PORT, payload=body)


def _streams(datagrams):
    lls = [ip_layer.parse_lls(d.payload) for d in datagrams]
    lls = [t for t in lls if t is not None]
    from atsc3lib import slt as slt_mod
    slt = None
    for t in lls:
        if t.table_id == 0x01:
            slt = slt_mod.slt_from_lls(t.data)
    return payload_mod.DecodedStreams(
        packets=[], datagrams=list(datagrams), lls=lls,
        alp_stats=None, ip_stats=None, slt=slt)


def test_signed_lls_reaches_parse_lls(wired):
    streams = _streams(wired["datagrams"])
    ids = {t.table_id for t in streams.lls}
    assert 0x06 in ids and 0x07 in ids
    # the SLT is parsed from its standalone form (A/331 5.9)
    assert streams.slt is not None
    assert streams.slt.bsid == (BSID,)


def test_verify_streams_happy_path(wired):
    streams = _streams(wired["datagrams"])
    report = security.verify_streams(streams, [wired["ca"].load_root()],
                                     now=wired["now"])
    assert report.ok, security.describe(report)
    assert streams.verification is report
    assert report.certified is not None
    assert report.provider == "openatsc3"


def test_verify_streams_wrong_root_fails(wired, tmp_path):
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root()
    streams = _streams(wired["datagrams"])
    report = security.verify_streams(streams, [other.load_root()],
                                     now=wired["now"])
    assert not report.ok
    assert "chain" in report.reason or "root" in report.reason


def test_verify_streams_tampered_smt_fails(wired):
    # Flip a byte inside the signed extent of the SignedMultiTable.
    body = bytearray(wired["smt"])
    body[6] ^= 0xFF
    streams = _streams([wired["datagrams"][0],
                        _lls_datagram(bytes([0x07, 0, 0, 1]) + bytes(body))])
    report = security.verify_streams(streams, [wired["ca"].load_root()],
                                     now=wired["now"])
    assert not report.ok


def test_verify_streams_wrong_bsid_fails(wired):
    # The SLT claims a different bsid than the signer's SDA carries.
    streams = _streams(wired["datagrams"])
    report = security.verify_streams(streams, [wired["ca"].load_root()],
                                     now=wired["now"], slt_bsids=[999])
    assert not report.ok
    assert "bsid" in report.reason


def test_no_cdt_fails(wired):
    streams = _streams([wired["datagrams"][1]])
    report = security.verify_streams(streams, [wired["ca"].load_root()],
                                     now=wired["now"])
    assert not report.ok
    assert "CertificationData" in report.reason
