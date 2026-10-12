"""Shared fixtures: a throwaway CA tree and a signed CertificationData table."""

import datetime as dt

import pytest

from openatsc3_pki import ca, cdt, keys, ocsp
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_ocsp_responder,
    issue_signaling_signer,
)

BSID = 540


@pytest.fixture
def authority(tmp_path):
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root(common_name="OpenATSC3 Root CA")
    a.issue_issuing()
    a.issue_broadcaster("WHUT", (BSID,))
    return a


@pytest.fixture
def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


@pytest.fixture
def signed_cdt(authority, now):
    """A complete signed CDT: chain + CurrentCert + distinct CDT signer + OCSP."""
    root = authority.load_root()
    root_key = authority.load_root_key()
    issuing = authority.load_issuing()
    issuing_key = authority.load_issuing_key()
    signer = authority.load_broadcaster("WHUT")
    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(
        SubjectInfo(common_name="WHUT-CDT-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization="WHUT"),
        cdt_key, issuing, issuing_key, (BSID,))
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, issuing, issuing_key)
    # The offline root authorizes the responder that signs the issuing CA's own
    # OCSP; the issuing CA authorizes the responder that signs its leaves' OCSP.
    # A CA's OCSP must be signed by its issuer (A/360 5.2.2.6 item 5, RFC 6960).
    root_resp_key = keys.generate()
    root_resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 Root OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        root_resp_key, root, root_key)
    store = ocsp.StatusStore()

    def resp(cert, issuer, responder, responder_key):
        return ocsp.respond(cert, issuer, responder, responder_key, store,
                            this_update=now, next_update=now + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[resp(issuing, root, root_resp_cert, root_resp_key),
                        resp(signer, issuing, resp_cert, resp_key),
                        resp(cdt_cert, issuing, resp_cert, resp_key)],
        signing_time=now)
    return {
        "root": root, "issuing": issuing, "issuing_key": issuing_key,
        "signer": signer, "signer_key": authority.load_broadcaster_key("WHUT"),
        "cdt": table, "now": now, "store": store,
    }


@pytest.fixture
def signed_smt(signed_cdt):
    """A signed LLS SignedMultiTable from the same signer."""
    from openatsc3_pki.cdt import LlsTable, parse_signed_multitable, signed_multitable
    payload = b'<SLT bsid="540"/>'
    body = signed_multitable([LlsTable(table_id=0x01, version=13, payload=payload)],
                             signed_cdt["signer"], signed_cdt["signer_key"],
                             signing_time=signed_cdt["now"])
    return parse_signed_multitable(body)
