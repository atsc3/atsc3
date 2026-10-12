"""Close a rollover window: supersede the CurrentCert (A/360 5.2.2.2)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import RolloverPlan


class Command(AuditCommand):
    help = "Complete a rollover window (supersedes the CurrentCert)."

    def add_command_arguments(self, parser):
        parser.add_argument("--plan", type=int, required=True, help="RolloverPlan id")

    def handle(self, *args, **options):
        plan = RolloverPlan.objects.filter(pk=options["plan"]).first()
        if plan is None:
            self.stderr.write(self.style.ERROR(
                f"no rollover plan {options['plan']}"))
            return
        services.complete_rollover(plan, actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"rollover {plan.pk}: state={plan.state} "
            f"current {plan.current.serial} superseded"))
