"""Create an OCSP responder certificate/key pair (A/360 5.3.1.7)."""

from __future__ import annotations

from catalog import services
from catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Create the OCSP responder certificate/key (A/360 5.3.1.7)."

    def add_command_arguments(self, parser):
        parser.add_argument("--cn", default="OpenATSC3 OCSP Responder")
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **options):
        responder = services.issue_ocsp_responder(
            common_name=options["cn"], days=options["days"],
            actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"ocsp responder: id={responder.pk} cn={responder.common_name}"))
