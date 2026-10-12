"""Set (or rotate) a tenant's customer-API token."""

from __future__ import annotations

import secrets

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = "Set or rotate a tenant's customer-API token."

    def add_command_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="tenant slug")
        parser.add_argument("--token", default="",
                            help="explicit token (default: generate one)")
        parser.add_argument("--clear", action="store_true",
                            help="disable API access (clear the token)")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options["tenant"]).first()
        if tenant is None:
            self.stderr.write(self.style.ERROR(
                f"no tenant {options['tenant']!r}"))
            return
        token = "" if options["clear"] else (options["token"]
                                             or secrets.token_urlsafe(32))
        tenant.api_token = token
        tenant.save(update_fields=["api_token"])
        services.audit("set-api-token", actor=options["actor"], target=tenant,
                       detail={"tenant": tenant.slug, "cleared": not token})
        if token:
            self.stdout.write(self.style.SUCCESS(f"api token: {token}"))
        else:
            self.stdout.write(self.style.SUCCESS("api token cleared"))
