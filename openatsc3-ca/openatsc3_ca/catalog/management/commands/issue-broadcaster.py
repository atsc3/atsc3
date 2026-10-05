"""Issue a broadcast signaling-signer certificate (A/360 5.3.1.6)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Issue a signaling-signer certificate (A/360 5.3.1.6)."

    def add_command_arguments(self, parser):
        parser.add_argument("--name", required=True, help="broadcaster name")
        parser.add_argument("--bsid", type=int, action="append", required=True,
                            help="broadcast stream id (repeatable)")
        parser.add_argument("--cn", default=None)
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **options):
        cert = services.issue_broadcaster(
            options["name"], options["bsid"], common_name=options["cn"],
            days=options["days"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"signer: serial={cert.serial} bsids={list(cert.bsids)} "
            f"subject={cert.subject_dn}"))
