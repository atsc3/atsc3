"""Create the issuing CA, signed by the root (A/360 5.3.1.3)."""

from __future__ import annotations

from catalog import services
from catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Create the issuing CA certificate (A/360 5.3.1.3)."

    def add_command_arguments(self, parser):
        parser.add_argument("--cn", default="OpenATSC3 Issuing CA")
        parser.add_argument("--days", type=int, default=3650)

    def handle(self, *args, **options):
        cert = services.issue_issuing(
            common_name=options["cn"], days=options["days"],
            actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"issuing CA: serial={cert.serial} subject={cert.subject_dn}"))
