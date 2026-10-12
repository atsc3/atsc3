"""Refund an order and, by policy, revoke the tenant CA (disable the customer)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Order


class Command(AuditCommand):
    help = "Refund an order; revoke its tenant CA unless --no-revoke."

    def add_command_arguments(self, parser):
        parser.add_argument("--id", type=int, required=True, help="order id")
        parser.add_argument("--no-revoke", action="store_true",
                            help="record the refund without revoking the tenant")
        parser.add_argument("--reason", default="refund")

    def handle(self, *args, **options):
        order = Order.objects.filter(pk=options["id"]).first()
        if order is None:
            self.stderr.write(self.style.ERROR(f"no order {options['id']}"))
            return
        services.refund_order(order, revoke_tenant=not options["no_revoke"],
                              reason=options["reason"], actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"order {order.pk} refunded"
            f"{' (tenant revoked)' if not options['no_revoke'] else ''}"))
