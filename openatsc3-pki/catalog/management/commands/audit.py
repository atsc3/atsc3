"""Show the append-only audit trail."""

from __future__ import annotations

from catalog.models import AuditEvent
from catalog.management.commands._base import AuditCommand


class Command(AuditCommand):
    help = "Print the append-only audit trail."

    def add_command_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        for event in AuditEvent.objects.all()[:options["limit"]]:
            self.stdout.write(
                f"{event.created_at:%Y-%m-%d %H:%M:%S} "
                f"{event.actor:12} {event.action:20} {event.target_type}#"
                f"{event.target_id}")
