"""Seed spec-derived reference data so operator state survives model evolution.

Every value is a normative A/360 / A/331 constant, re-derived from the crypto
library at migration time (never hand-typed) so a spec-version bump is a
one-line import change.
"""

from django.db import migrations


def seed(apps, schema_editor):
    SpecReference = apps.get_model("catalog", "SpecReference")
    from openatsc3_pki import oids

    rows = [
        ("id-atsc", oids.ATSC_PEN, "A/360 Annex A",
         "ATSC IANA Private Enterprise Number arc"),
        ("id-atsc-kp-signalingSigning", oids.ID_ATSC_KP_SIGNALING_SIGNING,
         "A/360 Table A.1", "broadcast signaling signing key purpose"),
        ("id-atsc-kp-author", oids.ID_ATSC_KP_AUTHOR, "A/360 Table A.1",
         "application author key purpose"),
        ("id-atsc-kp-distributor", oids.ID_ATSC_KP_DISTRIBUTOR,
         "A/360 Table A.1", "application distributor key purpose"),
        ("id-atsc-sdattr-bsid", oids.ID_ATSC_SDATTR_BSID, "A/360 Table A.1",
         "Subject Directory Attribute holding the bsid SET OF INTEGER"),
        ("id-kp-OCSPSigning", oids.ID_KP_OCSP_SIGNING, "A/360 Table A.2",
         "OCSP signing key purpose (RFC 6960)"),
        ("lls-certification-data", str(oids.CERTIFICATION_DATA_LLS_TABLE_ID),
         "A/360 5.2.2.2", "LLS table id of CertificationData (0x06)"),
        ("lls-signed-multitable", str(oids.SIGNED_MULTI_TABLE_LLS_TABLE_ID),
         "A/331 6.7", "LLS table id of the SignedMultiTable (0x07)"),
        ("ocsp-max-age-days", str(oids.OCSP_MAX_AGE_DAYS), "A/360 5.2.2.6",
         "OCSP staleness bound from producedAt"),
        ("ocsp-skew-hours", str(oids.OCSP_SKEW_HOURS), "A/360 5.2.2.6",
         "clock-skew allowance on producedAt"),
    ]
    for key, value, ref, note in rows:
        SpecReference.objects.update_or_create(
            key=key, defaults={"value": str(value), "ref": ref, "note": note})


def unseed(apps, schema_editor):
    SpecReference = apps.get_model("catalog", "SpecReference")
    SpecReference.objects.filter(key__in=[
        "id-atsc", "id-atsc-kp-signalingSigning", "id-atsc-kp-author",
        "id-atsc-kp-distributor", "id-atsc-sdattr-bsid", "id-kp-OCSPSigning",
        "lls-certification-data", "lls-signed-multitable",
        "ocsp-max-age-days", "ocsp-skew-hours",
    ]).delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
