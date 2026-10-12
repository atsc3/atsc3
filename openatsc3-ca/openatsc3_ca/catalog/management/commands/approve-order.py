"""Approve a paid order: issue the tenant CA, publish, and deliver the bundle."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Order


class Command(AuditCommand):
    help = "Fulfill a paid order (issue tenant CA, publish, deliver)."

    def add_command_arguments(self, parser):
        parser.add_argument("--id", type=int, required=True, dest="order_id")

    def handle(self, *args, **options):
        order = Order.objects.filter(pk=options["order_id"]).first()
        if order is None:
            self.stderr.write(self.style.ERROR(f"no order {options['order_id']}"))
            return
        services.fulfill_order(order, actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"order {order.pk}: {order.status} delivery={order.delivery_url}"))
