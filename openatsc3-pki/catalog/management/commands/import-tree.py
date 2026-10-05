"""Import a legacy on-disk CA tree into the ledger (idempotent)."""

from __future__ import annotations

from pathlib import Path

from catalog import services
from catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Import an on-disk CertificateAuthority tree into the ledger."

    def add_command_arguments(self, parser):
        parser.add_argument("--path", required=True,
                            help="base directory of the classic CA tree")

    def handle(self, *args, **options):
        certs = services.import_tree(Path(options["path"]),
                                     actor=options["actor"])
        for cert in certs:
            self.stdout.write(
                f"{cert.profile:16} serial={cert.serial} {cert.common_name}")
        self.stdout.write(self.style.SUCCESS(f"imported {len(certs)} certificate(s)"))
