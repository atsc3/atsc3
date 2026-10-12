"""Orders: Stripe Checkout + manual approval (Phase 2 sales).

Additive: creates the ``Order`` ledger table linking a paid Stripe Checkout to
the ``Tenant``/``Broadcaster`` created on approval and the delivered bundle.
"""

from __future__ import annotations

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("catalog", "0004_tenant_ca")]

    operations = [
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("organization", models.CharField(blank=True, max_length=255)),
                ("broadcaster_name", models.CharField(blank=True, max_length=128)),
                ("bsids", django.contrib.postgres.fields.ArrayField(
                    base_field=models.IntegerField(), blank=True, default=list,
                    size=None)),
                ("amount_cents", models.IntegerField(default=0)),
                ("currency", models.CharField(default="usd", max_length=8)),
                ("status", models.CharField(choices=[
                    ("pending", "Pending checkout"),
                    ("paid", "Paid, awaiting approval"),
                    ("fulfilled", "Fulfilled (issued + delivered)"),
                    ("cancelled", "Cancelled"),
                    ("refunded", "Refunded"),
                ], db_index=True, default="pending", max_length=16)),
                ("stripe_session_id", models.CharField(blank=True, max_length=255)),
                ("stripe_payment_intent", models.CharField(blank=True, max_length=255)),
                ("checkout_url", models.CharField(blank=True, max_length=1024)),
                ("delivery_url", models.CharField(blank=True, max_length=2048)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("fulfilled_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("broadcaster", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="orders", to="catalog.broadcaster")),
                ("tenant", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="orders", to="catalog.tenant")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
