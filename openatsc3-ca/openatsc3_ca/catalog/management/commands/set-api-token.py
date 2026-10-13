"""Set (or rotate) a tenant's customer-API token."""

from __future__ import annotations

import datetime as _dt
import secrets

from django.utils import timezone

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Tenant


class Command(AuditCommand):
    help = "Set or rotate a tenant's customer-API token."

    def add_command_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="tenant slug")
        parser.add_argument("--token", default="",
                            help="explicit token (default: generate one)")
        parser.add_argument("--days", type=int, default=0,
                            help="expire the token after N days (0 = never)")
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
        expires = (timezone.now() + _dt.timedelta(days=options["days"])
                   if token and options["days"] else None)
        tenant.api_token = token
        tenant.api_token_expires_at = expires
        tenant.save(update_fields=["api_token", "api_token_expires_at"])
        services.audit("set-api-token", actor=options["actor"], target=tenant,
                       detail={"tenant": tenant.slug, "cleared": not token,
                               "days": options["days"]})
        if token:
            suffix = f" (expires {expires:%Y-%m-%d})" if expires else ""
            self.stdout.write(self.style.SUCCESS(f"api token: {token}{suffix}"))
        else:
            self.stdout.write(self.style.SUCCESS("api token cleared"))

