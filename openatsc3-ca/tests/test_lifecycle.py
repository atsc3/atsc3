"""The Gap-A core: revocation persists and the receiver then rejects signaling.

This is the end-to-end gate the plan promised: issue a signer, build a signed
CertificationData + SignedMultiTable with ``openatsc3_pki``, verify it, revoke
through the ledger, regenerate the OCSP (which now says ``revoked``), and show
the *receiver* verifier (``openatsc3_pki.verify``) rejects it.  No PHY — this is
the sanctioned synthetic A/331-layer gate for this track.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.models import Certificate

pytestmark = pytest.mark.django_db


def _build_cdt(ca, signer, responder, signing_time):
    """Assemble a signed CDT with a distinct CDT signer and fresh OCSP."""
    from openatsc3_pki import cdt, keys, ocsp
    from openatsc3_pki.x509 import SubjectInfo, issue_signaling_signer

    issuing = services.certificate_x509(ca["issuing"])
    issuing_key = _issuing_key(ca)
    signer_x = services.certificate_x509(signer)

    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(
        SubjectInfo(common_name="WHUT-CDT-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization="WHUT"),
        cdt_key, issuing, issuing_key, (540,))
    resp = services.certificate_x509(responder.certificate)
    resp_key = keys.load_private(responder.key_path)
    store = services.status_store()

    def ocsp_for(cert):
        return ocsp.respond(cert, issuing, resp, resp_key, store,
                            this_update=signing_time,
                            next_update=signing_time + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer_x, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[ocsp_for(issuing), ocsp_for(signer_x), ocsp_for(cdt_cert)],
        signing_time=signing_time)
    return table, signer_x


def _issuing_key(ca):
    from openatsc3_pki import keys

    return keys.load_private(ca["issuing"].key_path)


def test_revoked_signer_fails_cdt_verification(ca):
    from openatsc3_pki import verify

    signer = services.issue_broadcaster("WHUT", (540,))
    now = timezone.now().replace(microsecond=0)
    table, signer_x = _build_cdt(ca, signer, ca["responder"], now)
    roots = [services.certificate_x509(ca["root"])]

    verdict, _keys = verify.verify_certification_data(table, roots, now=now)
    assert verdict.ok, verdict.reason

    # Revoke through the ledger: the status store now reports it revoked...
    services.revoke(signer)
    signer.refresh_from_db()
    assert signer.status == "revoked"
    assert services.status_store().is_revoked(int(signer.serial))

    # ...and a CDT rebuilt with the revoked OCSP no longer verifies.
    table2, _ = _build_cdt(ca, signer, ca["responder"], now)
    verdict2, _ = verify.verify_certification_data(table2, roots, now=now)
    assert not verdict2.ok
    assert "good" in verdict2.reason or "revoked" in verdict2.reason


def test_revoke_is_persistent_across_new_status_store(ca):
    signer = services.issue_broadcaster("WHUT", (540,))
    services.revoke(signer, reason="key-compromise")
    signer.refresh_from_db()
    assert signer.status == "revoked"
    assert signer.revoked_at is not None
    # A fresh store (as a new process would build) still knows.
    assert services.status_store().is_revoked(int(signer.serial))


def test_ocsp_status_follows_revocation(ca):
    signer = services.issue_broadcaster("WHUT", (540,))
    good = services.generate_ocsp(signer, responder=ca["responder"])
    assert good.status == "good"

    services.revoke(signer)
    revoked = services.generate_ocsp(signer, responder=ca["responder"])
    assert revoked.status == "revoked"

    latest = signer.ocsp_responses.first()
    assert latest.status == "revoked"
