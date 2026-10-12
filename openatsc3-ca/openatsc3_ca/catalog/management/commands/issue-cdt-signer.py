"""Issue the distinct CertificationData (CDT 0x06) signing certificate."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = "Issue a CertificationData signer (A/360 5.2.2.2, 5.3.1.6)."

    def add_command_arguments(self, parser):
        parser.add_argument("--name", required=True, help="broadcaster name")
        parser.add_argument("--bsid", type=int, action="append", required=True,
                            help="broadcast stream id (repeatable)")
        parser.add_argument("--tenant", default=None,
                            help="tenant slug (issues under the tenant CA)")
        parser.add_argument("--cn", default=None)
        parser.add_argument("--days", type=int, default=365)
        parser.add_argument("--kms-key-id", default="")

    def handle(self, *args, **options):
        tenant = None
        if options["tenant"]:
            tenant = Tenant.objects.get(slug=options["tenant"])
        cert = services.issue_cdt_signer(
            options["name"], options["bsid"], tenant=tenant,
            common_name=options["cn"], days=options["days"],
            kms_key_id=options["kms_key_id"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"cdt signer: serial={cert.serial} bsids={list(cert.bsids)} "
            f"subject={cert.subject_dn}"))
