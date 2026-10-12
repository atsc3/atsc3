"""Create the root-delegated revocation responder (A/360 5.3.1.7).

Signed by the offline root; the only authority that may sign OCSP responses for
per-tenant intermediate CAs.
"""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = ("Create the root-delegated OCSP responder (signs tenant-CA "
            "revocation; A/360 5.3.1.7).")

    def add_command_arguments(self, parser):
        parser.add_argument("--cn", default="OpenATSC3 Root OCSP Responder")
        parser.add_argument("--days", type=int, default=365)
        parser.add_argument("--kms-key-id", default="")

    def handle(self, *args, **options):
        responder = services.issue_root_responder(
            common_name=options["cn"], days=options["days"],
            kms_key_id=options["kms_key_id"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"root ocsp responder: id={responder.pk} "
            f"cn={responder.common_name}"))
