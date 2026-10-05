"""Create the self-signed root and record it in the ledger (A/360 5.3.1.2)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Create the self-signed root certificate (A/360 5.3.1.2)."

    def add_command_arguments(self, parser):
        parser.add_argument("--cn", default="OpenATSC3 Root CA")
        parser.add_argument("--org", default="OpenATSC3")
        parser.add_argument("--days", type=int, default=7300)

    def handle(self, *args, **options):
        cert = services.issue_root(
            common_name=options["cn"], organization=options["org"],
            days=options["days"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"root: serial={cert.serial} subject={cert.subject_dn}"))
