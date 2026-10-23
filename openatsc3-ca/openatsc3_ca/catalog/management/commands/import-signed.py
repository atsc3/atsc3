"""Record a certificate signed offline by the root (ceremony step 3)."""

from __future__ import annotations

from pathlib import Path

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Broadcaster, Tenant

_PROFILES = ("root", "tenant-ca", "ocsp-responder", "signaling-signer", "cdt-signer")


class Command(AuditCommand):
    help = "Record a certificate returned by the air-gapped root signing step."

    def add_command_arguments(self, parser):
        parser.add_argument("--cert", required=True,
                            help="signed certificate (.pem or .der)")
        parser.add_argument("--profile", required=True, choices=_PROFILES)
        parser.add_argument("--tenant", default=None)
        parser.add_argument("--name", default=None,
                            help="broadcaster name (for signer leaves)")
        parser.add_argument("--kms-key-id", default="")
        parser.add_argument("--key", default="",
                            help="signing key PEM for a leaf (or use --kms-key-id)")
        parser.add_argument("--responder", action="store_true",
                            help="register the cert as an active OCSP responder")

    def handle(self, *args, **options):
        tenant = (Tenant.objects.filter(slug=options["tenant"]).first()
                  if options["tenant"] else None)
        if options["tenant"] and tenant is None:
            self.stderr.write(self.style.ERROR(
                f"no tenant {options['tenant']!r}"))
            return
        broadcaster = None
        if options["name"]:
            broadcaster = Broadcaster.objects.filter(
                name=options["name"], tenant=tenant).first()
            if broadcaster is None:
                self.stderr.write(self.style.ERROR(
                    f"no broadcaster {options['name']!r} for tenant"))
                return
        cert = services.import_signed_certificate(
            Path(options["cert"]).read_bytes(), options["profile"],
            tenant=tenant, broadcaster=broadcaster,
            kms_key_id=options["kms_key_id"], key_path=options["key"],
            make_responder=options["responder"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"imported {cert.profile}: serial={cert.serial} "
            f"parent={cert.parent.serial if cert.parent_id else '-'}"))
