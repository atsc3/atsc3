"""Management command base: adds a consistent ``--actor`` audit field."""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand


class AuditCommand(BaseCommand):
    """A command that records the acting principal on the audit trail."""

    def add_arguments(self, parser):
        parser.add_argument(
            "--actor",
            default=os.environ.get("OPENATSC3_CA_ACTOR", "cli"),
            help="principal recorded on the audit trail (default: $USER/cli)")
        self.add_command_arguments(parser)

    def add_command_arguments(self, parser):
        """Subclasses add their own arguments here."""
