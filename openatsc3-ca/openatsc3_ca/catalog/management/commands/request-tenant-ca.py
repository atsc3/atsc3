"""Write a tenant-CA signing request for the offline root (ceremony step 1)."""

from __future__ import annotations

from pathlib import Path

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = ("Build a tenant-CA signing request (placeholder DER) for the "
            "air-gapped root to sign.")

    def add_command_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="tenant slug")
        parser.add_argument("--out", required=True, help="output request .der path")
        parser.add_argument("--days", type=int, default=3650)
        parser.add_argument("--kms-key-id", default="")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options["tenant"]).first()
        if tenant is None:
            self.stderr.write(self.style.ERROR(
                f"no tenant {options['tenant']!r}"))
            return
        request = services.request_tenant_ca(
            tenant, days=options["days"], kms_key_id=options["kms_key_id"],
            actor=options["actor"])
        Path(options["out"]).write_bytes(request)
        self.stdout.write(self.style.SUCCESS(
            f"request written: {options['out']} ({len(request)} bytes)"))
