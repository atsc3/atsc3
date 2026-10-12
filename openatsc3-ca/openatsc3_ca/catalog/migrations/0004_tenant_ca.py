"""Per-tenant intermediate CAs: the ``Tenant`` model and its FKs.

Additive only: ``Broadcaster`` and ``Certificate`` gain nullable ``tenant``
foreign keys and ``Certificate`` gains ``kms_key_id``.  The legacy single
``issuing/`` tree and its certificates stay ``tenant=None``, so an existing
database keeps working unchanged; new customers get a ``Tenant`` and a
``tenant-ca`` certificate whose revocation disables only them.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("catalog", "0003_import_legacy_tree")]

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=128, unique=True)),
                ("slug", models.SlugField(max_length=128, unique=True)),
                ("organization", models.CharField(blank=True, max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="broadcaster",
            name="tenant",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="broadcasters", to="catalog.tenant"),
        ),
        migrations.AddField(
            model_name="certificate",
            name="kms_key_id",
            field=models.CharField(blank=True, max_length=2048),
        ),
        migrations.AddField(
            model_name="certificate",
            name="tenant",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="certificates", to="catalog.tenant"),
        ),
        migrations.AlterField(
            model_name="certificate",
            name="profile",
            field=models.CharField(choices=[
                ("root", "Root CA (A/360 5.3.1.2)"),
                ("issuing-ca", "Issuing CA (A/360 5.3.1.3)"),
                ("tenant-ca", "Tenant intermediate CA (A/360 5.3.1.3)"),
                ("signaling-signer", "Broadcast signaling signer (A/360 5.3.1.6)"),
                ("cdt-signer", "CertificationData signer (A/360 5.2.2.2)"),
                ("ocsp-responder", "OCSP responder (A/360 5.3.1.7)"),
            ], max_length=32),
        ),
    ]
