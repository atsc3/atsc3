"""Write a root-delegated OCSP-responder request for the offline root."""

from __future__ import annotations

from pathlib import Path

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = ("Build a root-delegated OCSP-responder signing request (placeholder "
            "DER) for the air-gapped root to sign.")

    def add_command_arguments(self, parser):
        parser.add_argument("--out", required=True, help="output request .der path")
        parser.add_argument("--cn", default="OpenATSC3 Root OCSP Responder")
        parser.add_argument("--days", type=int, default=365)
        parser.add_argument("--kms-key-id", default="")

    def handle(self, *args, **options):
        request = services.request_root_responder(
            common_name=options["cn"], days=options["days"],
            kms_key_id=options["kms_key_id"], actor=options["actor"])
        Path(options["out"]).write_bytes(request)
        self.stdout.write(self.style.SUCCESS(
            f"request written: {options['out']} ({len(request)} bytes)"))
