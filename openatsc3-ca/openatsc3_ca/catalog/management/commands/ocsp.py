"""Generate and persist a fresh OCSP response for a certificate."""

from __future__ import annotations

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.management.commands._base import AuditCommand
from openatsc3_ca.catalog.models import Certificate, OcspResponder


class Command(AuditCommand):
    help = "Generate a stapled OCSP response for a certificate (A/360 5.2.2.2)."

    def add_command_arguments(self, parser):
        parser.add_argument("--serial", required=True)
        parser.add_argument("--responder", default=None,
                            help="OcspResponder id (default: active responder)")
        parser.add_argument("--refresh-hours", type=int, default=20)

    def handle(self, *args, **options):
        cert = Certificate.objects.filter(serial=options["serial"]).first()
        if cert is None:
            self.stderr.write(self.style.ERROR(
                f"no certificate with serial {options['serial']}"))
            return
        if options["responder"]:
            responder = OcspResponder.objects.get(pk=options["responder"])
        else:
            responder = OcspResponder.objects.filter(active=True).first()
        if responder is None:
            self.stderr.write(self.style.ERROR(
                "no active OCSP responder; run issue-ocsp-responder"))
            return
        import datetime as dt
        resp = services.generate_ocsp(
            cert, responder=responder,
            refresh=dt.timedelta(hours=options["refresh_hours"]))
        self.stdout.write(self.style.SUCCESS(
            f"ocsp: serial={cert.serial} status={resp.status} "
            f"file={resp.der_file}"))
