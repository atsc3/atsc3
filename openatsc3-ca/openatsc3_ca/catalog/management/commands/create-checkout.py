"""Create a Stripe Checkout Session for an order."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Order


class Command(AuditCommand):
    help = "Create a Stripe Checkout Session for an order."

    def add_command_arguments(self, parser):
        parser.add_argument("--id", type=int, required=True, dest="order_id")

    def handle(self, *args, **options):
        order = Order.objects.filter(pk=options["order_id"]).first()
        if order is None:
            self.stderr.write(self.style.ERROR(f"no order {options['order_id']}"))
            return
        services.create_checkout(order, actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"order {order.pk}: checkout {order.checkout_url}"))
