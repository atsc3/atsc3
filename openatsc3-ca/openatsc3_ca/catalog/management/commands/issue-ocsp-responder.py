"""Create an OCSP responder certificate/key pair (A/360 5.3.1.7)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = "Create the OCSP responder certificate/key (A/360 5.3.1.7)."

    def add_command_arguments(self, parser):
        parser.add_argument("--tenant", default=None,
                            help="tenant slug (responder issued by the tenant CA)")
        parser.add_argument("--cn", default="OpenATSC3 OCSP Responder")
        parser.add_argument("--days", type=int, default=365)
        parser.add_argument("--kms-key-id", default="")

    def handle(self, *args, **options):
        tenant = None
        if options["tenant"]:
            tenant = Tenant.objects.get(slug=options["tenant"])
        responder = services.issue_ocsp_responder(
            tenant=tenant, common_name=options["cn"], days=options["days"],
            kms_key_id=options["kms_key_id"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"ocsp responder: id={responder.pk} cn={responder.common_name}"))
