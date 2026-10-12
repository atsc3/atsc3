"""Create a per-tenant intermediate CA signed by the root (A/360 5.3.1.3)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = "Create a per-tenant intermediate CA (A/360 5.3.1.3)."

    def add_command_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="tenant slug")
        parser.add_argument("--name", default=None,
                            help="tenant display name (when creating the tenant)")
        parser.add_argument("--cn", default=None)
        parser.add_argument("--days", type=int, default=3650)
        parser.add_argument("--kms-key-id", default="",
                            help="KMS asymmetric key id/ARN (external key custody)")

    def handle(self, *args, **options):
        tenant, _ = Tenant.objects.get_or_create(
            slug=options["tenant"],
            defaults={"name": options["name"] or options["tenant"]})
        cert = services.issue_tenant_ca(
            tenant, common_name=options["cn"], days=options["days"],
            kms_key_id=options["kms_key_id"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"tenant CA: tenant={tenant.slug} serial={cert.serial} "
            f"subject={cert.subject_dn}"))
