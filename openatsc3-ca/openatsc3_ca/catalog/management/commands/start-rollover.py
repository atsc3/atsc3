"""Open a key-replacement window from a CurrentCert to a NextCert (A/360 5.2.2.2)."""

from __future__ import annotations

import datetime as dt

from django.utils.dateparse import parse_datetime

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Certificate


class Command(AuditCommand):
    help = "Start a rollover window (CertReplacement)."

    def add_command_arguments(self, parser):
        parser.add_argument("--current", required=True, help="CurrentCert serial")
        parser.add_argument("--next", required=True, help="NextCert serial")
        parser.add_argument("--next-from", default=None, help="ISO 8601 (default: now)")
        parser.add_argument("--current-until", default=None,
                            help="ISO 8601 (default: now + 30 days)")

    def handle(self, *args, **options):
        current = Certificate.objects.filter(serial=options["current"]).first()
        next_ = Certificate.objects.filter(serial=options["next"]).first()
        if current is None or next_ is None:
            self.stderr.write(self.style.ERROR("unknown --current/--next serial"))
            return
        now = dt.datetime.now(dt.timezone.utc)
        next_from = self._parse(options["next_from"]) if options["next_from"] else now
        current_until = (self._parse(options["current_until"])
                         if options["current_until"]
                         else now + dt.timedelta(days=30))
        if next_from is None or current_until is None:
            self.stderr.write(self.style.ERROR("could not parse the ISO timestamps"))
            return
        plan = services.start_rollover(current, next_, next_from, current_until,
                                       actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"rollover: id={plan.pk} state={plan.state} "
            f"{current.serial} -> {next_.serial}"))

    @staticmethod
    def _parse(text):
        when = parse_datetime(text)
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return when
