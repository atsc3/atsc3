"""Publish a broadcaster's signed bundle: CDT (0x06) plus optional signed SMT (0x07)."""

from __future__ import annotations

import datetime as dt

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Broadcaster


class Command(AuditCommand):
    help = "Assemble/sign the CDT (0x06) and, given LLS tables, the SMT (0x07)."

    def add_command_arguments(self, parser):
        parser.add_argument("--name", required=True, help="broadcaster name")
        parser.add_argument("--lls", action="append", default=[],
                            metavar="TID:VER:FILE",
                            help="LLS table to sign (repeatable), e.g. 0x01:13:slt.xml")
        parser.add_argument("--refresh-hours", type=int, default=20)

    def handle(self, *args, **options):
        broadcaster = Broadcaster.objects.filter(name=options["name"]).first()
        if broadcaster is None:
            self.stderr.write(self.style.ERROR(
                f"no broadcaster named {options['name']}"))
            return
        tables = [self._table(spec) for spec in options["lls"]]
        published, _table, smt = services.publish(
            broadcaster, tables=tables,
            refresh=dt.timedelta(hours=options["refresh_hours"]),
            actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"published: id={published.pk} cdt={published.cdt_file} "
            f"smt={published.smt_file or '-'}"))

    def _table(self, spec):
        from openatsc3_pki import cdt

        try:
            tid, ver, path = spec.split(":", 2)
            with open(path, "rb") as fh:
                payload = fh.read()
            return cdt.LlsTable(table_id=int(tid, 0), version=int(ver, 0),
                                payload=payload)
        except (ValueError, OSError) as exc:
            raise SystemExit(f"bad --lls {spec!r}: {exc}")
