"""Import an existing on-disk CA tree into the ledger (the first data migration).

The legacy tree is located by ``OPENATSC3_CA_IMPORT_TREE`` (for example the
demo ``out/pkitest`` CA).  When the variable is unset or the path is absent the
migration is a **no-op**, so a fresh ``migrate`` never fails.  It is idempotent
(re-running updates existing serials) and reversible (rows whose DER files live
under the import root are removed).

This is deliberately self-contained: it uses the *historical* models and parses
certificates directly, so future model changes cannot break replaying it.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.db import migrations


def _introspect(cert):
    from cryptography import x509 as _x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    def attr(oid):
        vals = cert.subject.get_attributes_for_oid(oid)
        return vals[0].value if vals else ""

    aki = ""
    try:
        ext = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER).value
        aki = ext.key_identifier.hex() if ext.key_identifier else ""
    except _x509.ExtensionNotFound:
        pass
    eku = []
    try:
        ext = cert.extensions.get_extension_for_class(
            _x509.ExtendedKeyUsage).value
        eku = [o.dotted_string for o in ext]
    except _x509.ExtensionNotFound:
        pass
    return {
        "subject_dn": cert.subject.rfc4514_string(),
        "common_name": attr(NameOID.COMMON_NAME),
        "organization": attr(NameOID.ORGANIZATION_NAME),
        "organizational_unit": attr(NameOID.ORGANIZATIONAL_UNIT_NAME),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
        "ski": _x509.SubjectKeyIdentifier.from_public_key(
            cert.public_key()).digest.hex(),
        "aki": aki,
        "eku": eku,
    }


def import_tree(apps, schema_editor):
    base_env = os.environ.get("OPENATSC3_CA_IMPORT_TREE")
    if not base_env:
        return
    base = Path(base_env)
    if not base.is_dir():
        return

    from openatsc3_pki import x509 as pki_x509

    Certificate = apps.get_model("catalog", "Certificate")
    Broadcaster = apps.get_model("catalog", "Broadcaster")

    def upsert(cert, profile, der_file, key_path, broadcaster=None):
        info = _introspect(cert.cert)
        defaults = dict(info)
        # Decode the id-atsc-sdattr-bsid SDA through the library (cryptography
        # exposes it as an opaque extension).
        try:
            defaults["bsids"] = list(cert.bsids())
        except Exception:
            defaults["bsids"] = []
        defaults.update({
            "profile": profile, "der_file": str(der_file),
            "key_path": str(key_path) if key_path and key_path.exists() else "",
            "status": "good", "broadcaster": broadcaster,
        })
        serial = str(cert.cert.serial_number)
        obj, _ = Certificate.objects.update_or_create(
            serial=serial, defaults=defaults)
        return obj

    mapping = [
        (base / "root" / "root.cert.pem", "root", base / "root" / "root.key.pem"),
        (base / "issuing" / "issuing.cert.pem", "issuing-ca",
         base / "issuing" / "issuing.key.pem"),
    ]
    for cert_path, profile, key_path in mapping:
        if cert_path.exists():
            upsert(pki_x509.load_pem(cert_path.read_bytes()), profile,
                   cert_path, key_path)

    bdir = base / "broadcasters"
    if bdir.is_dir():
        for child in sorted(bdir.iterdir()):
            cert_path = child / "signing.cert.pem"
            if not cert_path.exists():
                continue
            cert = pki_x509.load_pem(cert_path.read_bytes())
            try:
                bsid = list(cert.bsids())
            except Exception:
                bsid = []
            broadcaster, _ = Broadcaster.objects.get_or_create(
                name=child.name,
                defaults={"organization": child.name, "bsids": bsid})
            if bsid and list(broadcaster.bsids) != bsid:
                broadcaster.bsids = bsid
                broadcaster.save(update_fields=["bsids"])
            upsert(cert, "signaling-signer", cert_path,
                   child / "signing.key.pem", broadcaster)


def remove_imported(apps, schema_editor):
    base_env = os.environ.get("OPENATSC3_CA_IMPORT_TREE")
    if not base_env:
        return
    base = str(Path(base_env))
    Certificate = apps.get_model("catalog", "Certificate")
    Broadcaster = apps.get_model("catalog", "Broadcaster")
    Certificate.objects.filter(der_file__startswith=base).delete()
    Broadcaster.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_seed_spec_reference")]
    operations = [migrations.RunPython(import_tree, remove_imported)]
