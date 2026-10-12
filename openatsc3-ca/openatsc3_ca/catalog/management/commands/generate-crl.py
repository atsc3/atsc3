"""Generate a CRL for a CA certificate's revoked children (RFC 5280)."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Certificate


class Command(AuditCommand):
    help = "Generate a CRL for a CA certificate's revoked children."

    def add_command_arguments(self, parser):
        parser.add_argument("--serial", required=True,
                            help="issuing CA certificate serial")
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        cert = Certificate.objects.filter(serial=options["serial"]).first()
        if cert is None:
            self.stderr.write(self.style.ERROR(
                f"no certificate with serial {options['serial']}"))
            return
        der = services.generate_crl(cert, days=options["days"],
                                    actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(
            f"crl: issuer={cert.serial} bytes={len(der)}"))
