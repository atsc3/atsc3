"""Receiver-side validation tests: every A/360 5.2.2.6 negative gate."""

import datetime as dt

import pytest

from openatsc3_pki import cdt, cms, keys, ocsp, verify
from openatsc3_pki.x509 import (
    SubjectInfo,
    issue_ocsp_responder,
    issue_signaling_signer,
)


def _verify(case, roots=None, now=None):
    parsed = cdt.parse_certification_data(case["cdt"].gzip_bytes())
    return verify.verify_certification_data(
        parsed, roots if roots is not None else [case["root"]],
        now=now or case["now"])


def test_happy_path(signed_cdt, signed_smt):
    v, certified = _verify(signed_cdt)
    assert v.ok, v.reason
    m = verify.verify_signed_message(signed_smt.cms, signed_smt.signed_extent,
                                     certified, [540], now=signed_cdt["now"])
    assert m.ok, m.reason


def test_wrong_root_fails(signed_cdt, tmp_path):
    from openatsc3_pki import ca
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root()
    v, _ = _verify(signed_cdt, roots=[other.load_root()])
    assert not v.ok
    assert "chain to a trusted root" in v.reason


def test_expired_signer_fails(signed_cdt):
    far = signed_cdt["now"] + dt.timedelta(days=400)
    v, _ = _verify(signed_cdt, now=far)
    assert not v.ok
    assert "expired" in v.reason or "not valid" in v.reason


def test_tampered_cdt_signature_fails(signed_cdt):
    table = signed_cdt["cdt"]
    tampered = cdt.CertificationData(
        certificates=table.certificates, current_cert=table.current_cert,
        cmssigneddata=table.cmssigneddata, ocsp_responses=table.ocsp_responses,
        ocsp_refresh=table.ocsp_refresh,
        tobesigned=table.tobesigned + b" ")
    parsed = cdt.CertificationData(
        certificates=table.certificates, current_cert=table.current_cert,
        cmssigneddata=table.cmssigneddata, ocsp_responses=table.ocsp_responses,
        ocsp_refresh=table.ocsp_refresh, tobesigned=tampered.tobesigned)
    v, _ = verify.verify_certification_data(parsed, [signed_cdt["root"]],
                                            now=signed_cdt["now"])
    assert not v.ok


def test_cdt_signer_equals_current_fails(signed_cdt):
    """A/360 5.2.2.2 item 3: the CDT key must differ from the signaling key."""
    signer = signed_cdt["signer"]
    signer_key = signed_cdt["signer_key"]
    # Rebuild a CDT whose CMSSignedData is signed by the CurrentCert itself.
    parsed = cdt.parse_certification_data(signed_cdt["cdt"].gzip_bytes())
    tobesigned = parsed.tobesigned_bytes()
    cms_der = cms.sign_detached(tobesigned, signer, signer_key,
                                signing_time=signed_cdt["now"])
    forged = cdt.CertificationData(
        certificates=parsed.certificates, current_cert=parsed.current_cert,
        cmssigneddata=cms_der, ocsp_responses=parsed.ocsp_responses,
        ocsp_refresh=parsed.ocsp_refresh, tobesigned=tobesigned)
    v, _ = verify.verify_certification_data(forged, [signed_cdt["root"]],
                                            now=signed_cdt["now"])
    assert not v.ok
    assert "CurrentCert" in v.reason


def test_revoked_signer_ocsp_fails(signed_cdt):
    """A revoked signer's OCSP response says revoked -> CDT fails."""
    issuing = signed_cdt["issuing"]
    signer = signed_cdt["signer"]
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="Revoker", organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, issuing, signed_cdt["signer_key"])
    store = ocsp.StatusStore()
    store.mark_revoked(signer.cert.serial_number, signed_cdt["now"])
    revoked = ocsp.respond(signer, issuing, resp_cert, resp_key, store,
                           this_update=signed_cdt["now"],
                           next_update=signed_cdt["now"] + dt.timedelta(hours=20))
    table = signed_cdt["cdt"]
    # swap in the revoked OCSP for the signer's serial
    ocsps = []
    for der in table.ocsp_responses:
        from cryptography.x509 import ocsp as _ocsp
        r = _ocsp.load_der_ocsp_response(der)
        if any(s.serial_number == signer.cert.serial_number for s in r.responses):
            ocsps.append(revoked)
        else:
            ocsps.append(der)
    forged = cdt.CertificationData(
        certificates=table.certificates, current_cert=table.current_cert,
        cmssigneddata=table.cmssigneddata, ocsp_responses=tuple(ocsps),
        ocsp_refresh=table.ocsp_refresh, tobesigned=table.tobesigned)
    v, _ = verify.verify_certification_data(forged, [signed_cdt["root"]],
                                            now=signed_cdt["now"])
    assert not v.ok
    assert "status is not good" in v.reason or "revoked" in v.reason


def test_stale_ocsp_fails(signed_cdt):
    parsed = cdt.parse_certification_data(signed_cdt["cdt"].gzip_bytes())
    later = signed_cdt["now"] + dt.timedelta(days=11)
    v, _ = verify.verify_certification_data(parsed, [signed_cdt["root"]],
                                            now=later)
    assert not v.ok
    assert "OCSP" in v.reason


def test_wrong_bsid_fails(signed_cdt, signed_smt):
    _, certified = _verify(signed_cdt)
    v = verify.verify_signed_message(signed_smt.cms, signed_smt.signed_extent,
                                     certified, [999], now=signed_cdt["now"])
    assert not v.ok
    assert "bsid" in v.reason


def test_tampered_message_fails(signed_cdt, signed_smt):
    _, certified = _verify(signed_cdt)
    v = verify.verify_signed_message(signed_smt.cms,
                                     signed_smt.signed_extent + b"x",
                                     certified, [540], now=signed_cdt["now"])
    assert not v.ok
    assert "signature invalid" in v.reason


def test_future_signing_time_fails(signed_cdt, signed_smt):
    _, certified = _verify(signed_cdt)
    later = signed_cdt["now"] - dt.timedelta(hours=2)
    v = verify.verify_signed_message(signed_smt.cms, signed_smt.signed_extent,
                                     certified, [540], now=later)
    assert not v.ok
    assert "future" in v.reason


def test_backward_signing_time_fails(signed_cdt, signed_smt):
    _, certified = _verify(signed_cdt)
    v = verify.verify_signed_message(
        signed_smt.cms, signed_smt.signed_extent, certified, [540],
        now=signed_cdt["now"],
        previous_signing_time=signed_cdt["now"] + dt.timedelta(hours=1))
    assert not v.ok
    assert "backward" in v.reason


def test_missing_signaling_eku_fails(tmp_path):
    """A signer without id-atsc-kp-signalingSigning is rejected (A/360 5.2.2.6 3a)."""
    from openatsc3_pki import ca
    from openatsc3_pki.verify import CertifiedKeys
    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root()
    a.issue_issuing()
    key = keys.generate()
    # A CA-style leaf: BasicConstraints CA, no signaling EKU, no bsid SDA.
    from openatsc3_pki import x509 as _x509
    plain = _x509.issue_ca(
        SubjectInfo(common_name="No-EKU", organizational_unit="x",
                    organization="WHUT"),
        key, a.load_issuing(), a.load_issuing_key())
    assert not plain.signaling_eku()
    certified = CertifiedKeys(
        cdt=None, current_ski=plain.subject_key_identifier(), next_ski=None,
        current_until=None, next_from=None, certificates=(plain,),
        ocsp_refresh=dt.timedelta(0), verified={})
    msg = b"signaling payload"
    blob = cms.sign_detached(msg, plain, key)
    v = verify.verify_signed_message(blob, msg, certified, [])
    assert not v.ok
    assert "EKU" in v.reason
