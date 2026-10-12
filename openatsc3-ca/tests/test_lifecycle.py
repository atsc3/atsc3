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


def _build_cdt(ca, signer, responder, signing_time, *, ca_responder=None):
    """Assemble a signed CDT with a distinct CDT signer and fresh OCSP.

    The issuing CA's own OCSP is stapled from the root-authorized responder;
    its leaves' OCSP from the issuing CA's responder (A/360 5.2.2.6 item 5).
    ``ca_responder`` overrides the issuer's responder (to forge a response).
    """
    from openatsc3_pki import cdt, keys, ocsp
    from openatsc3_pki.x509 import SubjectInfo, issue_signaling_signer

    root = services.certificate_x509(ca["root"])
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
    ca_resp = ca_responder or ca["root_responder"]
    ca_resp_cert = services.certificate_x509(ca_resp.certificate)
    ca_resp_key = keys.load_private(ca_resp.key_path)
    store = services.status_store()

    def ocsp_for(cert, issuer, r, rk):
        return ocsp.respond(cert, issuer, r, rk, store,
                            this_update=signing_time,
                            next_update=signing_time + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer_x, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[ocsp_for(issuing, root, ca_resp_cert, ca_resp_key),
                        ocsp_for(signer_x, issuing, resp, resp_key),
                        ocsp_for(cdt_cert, issuing, resp, resp_key)],
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


def _build_tenant_cdt(tenant, signer, responder, signing_time, *,
                      ca_responder=None):
    """Assemble a signed CDT for a tenant, with stapled OCSP per certificate.

    The chain carries the tenant intermediate CA (the root stays a trust
    anchor, out of band).  The tenant CA's own OCSP is stapled from the
    root-authorized responder; its leaves' from the tenant's responder
    (A/360 5.2.2.6 item 5).  ``ca_responder`` overrides the tenant CA's
    responder (to forge a self-authorized response).
    """
    from openatsc3_pki import cdt, keys, ocsp
    from openatsc3_pki.x509 import SubjectInfo, issue_signaling_signer

    ca_row = services.tenant_ca(tenant)
    issuing = services.certificate_x509(ca_row)
    issuing_key = keys.load_private(ca_row.key_path)
    signer_x = services.certificate_x509(signer)

    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(
        SubjectInfo(common_name=f"{tenant.slug}-CDT-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization=tenant.name),
        cdt_key, issuing, issuing_key, (540,))
    resp = services.certificate_x509(responder.certificate)
    resp_key = keys.load_private(responder.key_path)
    root_resp = services.root_responder()
    root_x509 = services.certificate_x509(root_resp.certificate.parent)
    ca_resp = ca_responder or root_resp
    ca_resp_cert = services.certificate_x509(ca_resp.certificate)
    ca_resp_key = keys.load_private(ca_resp.key_path)
    store = services.status_store()

    def ocsp_for(cert, issuer, r, rk):
        return ocsp.respond(cert, issuer, r, rk, store,
                            this_update=signing_time,
                            next_update=signing_time + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[issuing.der()], signing_cert=signer_x, cdt_signer_cert=cdt_cert,
        cdt_signer_key=cdt_key,
        ocsp_responses=[ocsp_for(issuing, root_x509, ca_resp_cert, ca_resp_key),
                        ocsp_for(signer_x, issuing, resp, resp_key),
                        ocsp_for(cdt_cert, issuing, resp, resp_key)],
        signing_time=signing_time)
    return table, signer_x


def test_issue_tenant_ca_records_tenant_and_parent(ca):
    from openatsc3_ca.catalog.models import Tenant

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    tca = services.issue_tenant_ca(tenant)
    assert tca.tenant == tenant
    assert tca.parent == ca["root"]
    assert tca.key_path.endswith("tenants/acme/issuing.key.pem")
    assert tca.kms_key_id == ""


def test_revoked_tenant_ca_fails_cdt_verification(ca):
    from openatsc3_pki import verify
    from openatsc3_ca.catalog.models import Tenant

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    services.issue_tenant_ca(tenant)
    signer = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    responder = services.issue_ocsp_responder(tenant=tenant)
    now = timezone.now().replace(microsecond=0)
    roots = [services.certificate_x509(ca["root"])]

    table, _ = _build_tenant_cdt(tenant, signer, responder, now)
    verdict, _ = verify.verify_certification_data(table, roots, now=now)
    assert verdict.ok, verdict.reason

    # Revoking the tenant intermediate disables the whole tenant: its leaf's
    # OCSP now reports the intermediate as revoked and the CDT will not verify.
    services.revoke(services.tenant_ca(tenant))
    table2, _ = _build_tenant_cdt(tenant, signer, responder, now)
    verdict2, _ = verify.verify_certification_data(table2, roots, now=now)
    assert not verdict2.ok


def test_revoking_one_tenant_leaves_another_valid(ca):
    from openatsc3_pki import verify
    from openatsc3_ca.catalog.models import Tenant

    acme = Tenant.objects.create(name="Acme", slug="acme")
    beta = Tenant.objects.create(name="Beta", slug="beta")
    for tenant in (acme, beta):
        services.issue_tenant_ca(tenant)
    signer_a = services.issue_broadcaster("ACME-TV", (540,), tenant=acme)
    signer_b = services.issue_broadcaster("BETA-TV", (541,), tenant=beta)
    resp_a = services.issue_ocsp_responder(tenant=acme)
    resp_b = services.issue_ocsp_responder(tenant=beta)
    now = timezone.now().replace(microsecond=0)
    roots = [services.certificate_x509(ca["root"])]

    services.revoke(services.tenant_ca(acme))

    table_a, _ = _build_tenant_cdt(acme, signer_a, resp_a, now)
    verdict_a, _ = verify.verify_certification_data(table_a, roots, now=now)
    assert not verdict_a.ok

    table_b, _ = _build_tenant_cdt(beta, signer_b, resp_b, now)
    verdict_b, _ = verify.verify_certification_data(table_b, roots, now=now)
    assert verdict_b.ok, verdict_b.reason


def test_self_forged_tenant_ca_ocsp_is_rejected(ca):
    """A tenant must not be able to self-authorize a 'good' for its own CA.

    The tenant CA's OCSP has to be signed by a root-authorized responder; a
    response stapled with the tenant's own responder must not authenticate, so
    revoking a compromised tenant CA stays enforceable (A/360 5.2.2.6 item 5).
    """
    from openatsc3_pki import verify
    from openatsc3_ca.catalog.models import Tenant

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    services.issue_tenant_ca(tenant)
    signer = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    responder = services.issue_ocsp_responder(tenant=tenant)
    now = timezone.now().replace(microsecond=0)
    roots = [services.certificate_x509(ca["root"])]

    table, _ = _build_tenant_cdt(tenant, signer, responder, now,
                                 ca_responder=responder)
    verdict, _ = verify.verify_certification_data(table, roots, now=now)
    assert not verdict.ok
    assert "issuer" in verdict.reason or "responder" in verdict.reason
