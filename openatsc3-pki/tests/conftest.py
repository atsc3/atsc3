"""Shared fixtures: a throwaway CA tree and a signed CertificationData table."""

import datetime as dt
import os

import pytest

# The Django/PostgreSQL CA tests live in tests/ca and need pytest-django plus a
# running PostgreSQL.  They are collected only when OPENATSC3_CA_TEST is set
# (see the ``test-ca`` Makefile target), so the plain crypto suite needs neither.
collect_ignore = []
if not os.environ.get("OPENATSC3_CA_TEST"):
    collect_ignore.append("ca")

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
    store = ocsp.StatusStore()

    def resp(cert, issuer):
        return ocsp.respond(cert, issuer, resp_cert, resp_key, store,
                            this_update=now, next_update=now + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[resp(issuing, issuing), resp(signer, issuing),
                        resp(cdt_cert, issuing)],
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
