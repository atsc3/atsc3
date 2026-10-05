"""Revoke a certificate persistently (closes the missing ``revoke`` command)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Certificate


class Command(AuditCommand):
    help = "Revoke a certificate by serial (persistent, audited)."

    def add_command_arguments(self, parser):
        parser.add_argument("--serial", default=None,
                            help="certificate serial (decimal)")
        parser.add_argument("--name", default=None,
                            help="revoke every GOOD signer of this broadcaster")

    def handle(self, *args, **options):
        if not options["serial"] and not options["name"]:
            self.stderr.write(self.style.ERROR("provide --serial or --name"))
            return

        targets = []
        if options["serial"]:
            cert = Certificate.objects.filter(serial=options["serial"]).first()
            if cert is None:
                self.stderr.write(self.style.ERROR(
                    f"no certificate with serial {options['serial']}"))
                return
            targets = [cert]
        elif options["name"]:
            targets = list(Certificate.objects.filter(
                broadcaster__name=options["name"], status="good"))
            if not targets:
                self.stderr.write(self.style.ERROR(
                    f"no GOOD certificates for broadcaster {options['name']}"))
                return

        for cert in targets:
            services.revoke(cert, actor=options["actor"])
            self.stdout.write(self.style.SUCCESS(
                f"revoked serial={cert.serial} ({cert.common_name}) "
                f"at={cert.revoked_at:%Y-%m-%d %H:%M:%SZ}"))
