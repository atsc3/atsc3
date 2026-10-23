"""Receiver-side validation tests: every A/360 5.2.2.6 negative gate."""

import datetime as dt

import pytest

from openatsc3_pki import cdt, cms, keys, ocsp, verify, x509
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


def test_tobesigned_must_match_parsed_fields(signed_cdt):
    table = signed_cdt["cdt"]
    forged = cdt.CertificationData(
        certificates=table.certificates[:-1], current_cert=table.current_cert,
        cmssigneddata=table.cmssigneddata, ocsp_responses=table.ocsp_responses,
        ocsp_refresh=table.ocsp_refresh, tobesigned=table.tobesigned)
    v, _ = verify.verify_certification_data(forged, [signed_cdt["root"]],
                                            now=signed_cdt["now"])
    assert not v.ok
    assert "does not match" in v.reason


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
        resp_key, issuing, signed_cdt["issuing_key"])
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


def test_forged_same_subject_ocsp_responder_fails(signed_cdt):
    """A self-signed responder copying the issuer's subject is not the issuer."""
    from cryptography.x509 import ocsp as _ocsp

    root = signed_cdt["root"]
    issuing = signed_cdt["issuing"]
    forged_key = keys.generate(keys.ROOT_CURVE)
    forged = x509.issue_root(
        SubjectInfo(common_name="OpenATSC3 Root CA",
                    organizational_unit="ATSC Trust Authority",
                    organization="OpenATSC3"), forged_key)
    assert forged.subject == root.subject
    assert forged.der() != root.der()
    good = ocsp.respond(issuing, root, forged, forged_key, ocsp.StatusStore(),
                        this_update=signed_cdt["now"],
                        next_update=signed_cdt["now"] + dt.timedelta(hours=20))
    table = signed_cdt["cdt"]
    ocsps = []
    for der in table.ocsp_responses:
        parsed = _ocsp.load_der_ocsp_response(der)
        if any(s.serial_number == issuing.cert.serial_number
               for s in parsed.responses):
            ocsps.append(good)
        else:
            ocsps.append(der)
    forged_table = cdt.CertificationData(
        certificates=table.certificates, current_cert=table.current_cert,
        cmssigneddata=table.cmssigneddata, ocsp_responses=tuple(ocsps),
        ocsp_refresh=table.ocsp_refresh, tobesigned=table.tobesigned)
    v, _ = verify.verify_certification_data(forged_table, [root],
                                            now=signed_cdt["now"])
    assert not v.ok
    assert "responder" in v.reason


def test_find_chain_rejects_non_ca_issuer(tmp_path):
    """A CA=false signaling leaf cannot be used as an intermediate."""
    from openatsc3_pki import ca

    a = ca.CertificateAuthority(str(tmp_path / "ca"))
    a.init_root()
    a.issue_tenant_ca("acme")
    a.issue_broadcaster("ACME-TV", (540,), tenant="acme")
    root = a.load_root()
    tenant_ca = a.load_tenant("acme")
    leaf = a.load_broadcaster("ACME-TV", tenant="acme")
    leaf_key = a.load_broadcaster_key("ACME-TV", tenant="acme")
    forged = x509.issue_ca(
        SubjectInfo(common_name="Forged CA", organizational_unit="x",
                    organization="x"), keys.generate(keys.CA_CURVE),
        leaf, leaf_key)
    assert verify._find_chain(forged, [root], [tenant_ca, leaf, forged]) is None


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


def _subject(common_name, organization):
    return SubjectInfo(common_name=common_name,
                       organizational_unit="ATSC Broadcast Signaling Signer",
                       organization=organization)


def _ca_subject():
    return SubjectInfo(common_name="Acme Intermediate CA",
                       organizational_unit="ATSC Trust Authority",
                       organization="OpenATSC3")


def test_find_chain_bridges_overlap_with_cross_signed_root(tmp_path):
    """A cross-signed new root chains when only the old anchor, or both, are trusted."""
    from openatsc3_pki import ca, x509

    old = ca.CertificateAuthority(str(tmp_path / "old"))
    old.init_root()
    new = ca.CertificateAuthority(str(tmp_path / "new"))
    new.init_root(common_name="OpenATSC3 Root CA (new)")
    cross = x509.cross_sign_root(new.load_root(), old.load_root(),
                                 old.load_root_key())
    leaf = x509.issue_ca(_ca_subject(), keys.generate(keys.CA_CURVE),
                         new.load_root(), new.load_root_key())

    bridged = verify._find_chain(leaf, [old.load_root()], [cross])
    assert bridged is not None
    assert bridged.root.der() == old.load_root().der()

    overlap = verify._find_chain(leaf, [old.load_root(), new.load_root()], [cross])
    assert overlap is not None
    assert overlap.root.der() in {old.load_root().der(), new.load_root().der()}


def test_find_chain_rejects_subject_collision_fake_root():
    """A forged self-signed cert copying the anchor's subject is not trusted."""
    from openatsc3_pki import x509

    subject = SubjectInfo(common_name="OpenATSC3 Root CA",
                          organizational_unit="ATSC Trust Authority",
                          organization="OpenATSC3")
    good = x509.issue_root(subject, keys.generate(keys.ROOT_CURVE))
    atk_key = keys.generate(keys.ROOT_CURVE)
    atk = x509.issue_root(subject, atk_key)
    leaf = x509.issue_ca(_ca_subject(), keys.generate(keys.CA_CURVE), atk, atk_key)

    assert verify._find_chain(leaf, [good], [atk]) is None


def test_cdt_with_next_cert_carries_rollover_window(authority, now):
    """A CertReplacement CDT carries NextCert and verifies (A/360 5.2.2.2)."""
    root = authority.load_root()
    root_key = authority.load_root_key()
    issuing = authority.load_issuing()
    issuing_key = authority.load_issuing_key()
    signer = authority.load_broadcaster("WHUT")
    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(_subject("WHUT-CDT-Signer", "WHUT"),
                                      cdt_key, issuing, issuing_key, (540,))
    next_cert = issue_signaling_signer(_subject("WHUT-Next", "WHUT"),
                                       keys.generate(), issuing, issuing_key, (540,))
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, issuing, issuing_key)
    root_resp_key = keys.generate()
    root_resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 Root OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        root_resp_key, root, root_key)
    store = ocsp.StatusStore()

    def resp(c, issuer, responder, responder_key):
        return ocsp.respond(c, issuer, responder, responder_key, store,
                            this_update=now, next_update=now + dt.timedelta(hours=20))

    next_from = now + dt.timedelta(hours=2)
    current_until = now + dt.timedelta(hours=4)
    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[resp(issuing, root, root_resp_cert, root_resp_key),
                        resp(signer, issuing, resp_cert, resp_key),
                        resp(cdt_cert, issuing, resp_cert, resp_key),
                        resp(next_cert, issuing, resp_cert, resp_key)],
        next_cert=next_cert, next_cert_from=next_from,
        current_cert_until=current_until, signing_time=now)

    parsed = cdt.parse_certification_data(table.gzip_bytes())
    assert next_cert.der() in parsed.certificates
    verdict, certified = verify.verify_certification_data(
        parsed, [authority.load_root()], now=now)
    assert verdict.ok, verdict.reason
    assert certified.next_ski == next_cert.subject_key_identifier()
    assert certified.next_from == next_from
    assert certified.current_until == current_until


def test_expired_ocsp_responder_fails(authority, now):
    """An OCSP response signed by an expired responder is rejected (A/360 5.2.2.6)."""
    issuing = authority.load_issuing()
    leaf = authority.load_broadcaster("WHUT")
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="Short-Lived OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, issuing, authority.load_issuing_key(), days=1)
    der = ocsp.respond(leaf, issuing, resp_cert, resp_key, ocsp.StatusStore(),
                       this_update=now, next_update=now + dt.timedelta(hours=20))
    later = now + dt.timedelta(days=2)
    reason = verify._verify_ocsp(der, leaf, issuing, later, dt.timedelta(hours=20))
    assert reason and "responder" in reason and "expired" in reason


def test_ocsp_cert_id_must_match_the_issuer(authority, now, tmp_path):
    """A response for a different issuer (same serial) is not accepted."""
    from openatsc3_pki import ca

    issuing = authority.load_issuing()
    leaf = authority.load_broadcaster("WHUT")
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OCSP", organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, issuing, authority.load_issuing_key())
    der = ocsp.respond(leaf, issuing, resp_cert, resp_key, ocsp.StatusStore(),
                       this_update=now, next_update=now + dt.timedelta(hours=20))
    other = ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root()
    other.issue_issuing()
    reason = verify._verify_ocsp(der, leaf, other.load_issuing(), now,
                                 dt.timedelta(hours=20))
    assert reason and "issuer" in reason


def test_signing_time_before_certificate_validity_fails(signed_cdt, signed_smt):
    _, certified = _verify(signed_cdt)
    early = (signed_cdt["signer"].cert.not_valid_before_utc
             - dt.timedelta(minutes=5))
    blob = cms.sign_detached(signed_smt.signed_extent, signed_cdt["signer"],
                             signed_cdt["signer_key"], signing_time=early)
    v = verify.verify_signed_message(blob, signed_smt.signed_extent, certified,
                                     [540], now=signed_cdt["now"])
    assert not v.ok
    assert "validity" in v.reason


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
