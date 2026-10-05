"""CMS SignedData profile tests (A/360 5.2.2.1)."""

import datetime as dt

from asn1crypto import cms as acms

from openatsc3_pki import ca, cms


def _signer(authority):
    return authority.load_broadcaster("WHUT"), authority.load_broadcaster_key("WHUT")


def test_cms_detached_no_content_no_certs(authority):
    cert, key = _signer(authority)
    blob = cms.sign_detached(b"payload", cert, key)
    ci = acms.ContentInfo.load(blob)
    sd = ci["content"]
    assert sd["encap_content_info"]["content"].native is None
    assert sd["certificates"].native is None
    assert sd["crls"].native is None


def test_cms_signer_identifier_is_ski(authority):
    cert, key = _signer(authority)
    blob = cms.sign_detached(b"payload", cert, key)
    sig = cms.parse(blob)
    assert sig.signer_ski == cert.subject_key_identifier()


def test_cms_has_signing_time_whole_seconds(authority):
    cert, key = _signer(authority)
    when = dt.datetime(2026, 10, 4, 12, 0, 0, 500000, tzinfo=dt.timezone.utc)
    blob = cms.sign_detached(b"payload", cert, key, signing_time=when)
    assert cms.signing_time(blob) == dt.datetime(2026, 10, 4, 12, 0, 0,
                                                 tzinfo=dt.timezone.utc)


def test_cms_verify_good_and_tampered(authority):
    cert, key = _signer(authority)
    msg = b"a signaling message"
    blob = cms.sign_detached(msg, cert, key)
    assert cms.verify(blob, msg, cert.cert.public_key())
    assert not cms.verify(blob, msg + b"x", cert.cert.public_key())
    assert not cms.verify(blob, b"other", cert.cert.public_key())


def test_cms_wrong_key_fails(authority):
    cert, key = _signer(authority)
    other = ca.CertificateAuthority(authority.base_dir + "/other")
    other.init_root()
    other.issue_issuing()
    other.issue_broadcaster("WJLA", (541,))
    blob = cms.sign_detached(b"msg", cert, key)
    wrong = other.load_broadcaster("WJLA")
    assert not cms.verify(blob, b"msg", wrong.cert.public_key())
