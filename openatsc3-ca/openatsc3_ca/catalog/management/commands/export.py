"""Materialize the classic PEM tree from the ledger (DB is authoritative)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Write root/issuing/broadcaster PEM files from the database."

    def handle(self, *args, **options):
        written = services.export_tree(actor=options["actor"])
        for path in written:
            self.stdout.write(path)
        self.stdout.write(self.style.SUCCESS(f"exported {len(written)} file(s)"))
