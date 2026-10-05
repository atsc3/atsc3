"""Command-line interface for the OpenATSC3 PKI toolkit.

    openatsc3-pki init-root --dir ca [--cn ...]
    openatsc3-pki issue-ca --dir ca
    openatsc3-pki issue-broadcaster --dir ca --name WHUT --bsid 540
    openatsc3-pki show --dir ca --name WHUT
    openatsc3-pki revoke --dir ca --name WHUT
"""

from __future__ import annotations

import argparse
import sys

from . import ca as ca_mod
from . import oids
from .x509 import SubjectInfo


def _ca(args) -> ca_mod.CertificateAuthority:
    return ca_mod.CertificateAuthority(args.dir)


def cmd_init_root(args) -> int:
    authority = _ca(args)
    cert = authority.init_root(common_name=args.cn, organization=args.org)
    print(f"root written: {authority.root_cert_path}")
    print(f"  subject: {cert.subject.rfc4514_string()}")
    print(f"  expires: {cert.cert.not_valid_after_utc.isoformat()}")
    return 0


def cmd_issue_ca(args) -> int:
    authority = _ca(args)
    cert = authority.issue_issuing(common_name=args.cn)
    print(f"issuing CA written: {authority.issuing_cert_path}")
    print(f"  subject: {cert.subject.rfc4514_string()}")
    return 0


def cmd_issue_broadcaster(args) -> int:
    authority = _ca(args)
    cert = authority.issue_broadcaster(args.name, tuple(args.bsid),
                                       common_name=args.cn, days=args.days)
    print(f"broadcaster signer written: {authority.broadcaster_dir(args.name)}")
    print(f"  subject: {cert.subject.rfc4514_string()}")
    print(f"  bsids:   {cert.bsids()}")
    print(f"  EKU id-atsc-kp-signalingSigning: {cert.signaling_eku()}")
    return 0


def cmd_show(args) -> int:
    authority = _ca(args)
    cert = (authority.load_broadcaster(args.name) if args.name
            else authority.load_root())
    print(cert.cert.subject.rfc4514_string())
    print(f"  issuer:    {cert.cert.issuer.rfc4514_string()}")
    print(f"  serial:    {cert.cert.serial_number}")
    print(f"  notBefore: {cert.cert.not_valid_before_utc.isoformat()}")
    print(f"  notAfter:  {cert.cert.not_valid_after_utc.isoformat()}")
    print(f"  bsids:     {cert.bsids()}")
    print(f"  signaling EKU: {cert.signaling_eku()}")
    print(f"  SKI: {cert.subject_key_identifier().hex()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openatsc3-pki",
                                     description="OpenATSC3 certificate authority")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p = subs.add_parser("init-root", help="create the self-signed root")
    p.add_argument("--dir", required=True)
    p.add_argument("--cn", default="OpenATSC3 Root CA")
    p.add_argument("--org", default="OpenATSC3")
    p.set_defaults(func=cmd_init_root)

    p = subs.add_parser("issue-ca", help="create the issuing CA")
    p.add_argument("--dir", required=True)
    p.add_argument("--cn", default="OpenATSC3 Issuing CA")
    p.set_defaults(func=cmd_issue_ca)

    p = subs.add_parser("issue-broadcaster", help="issue a signaling signer")
    p.add_argument("--dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--bsid", type=int, action="append", required=True,
                   help="broadcast stream id (repeatable)")
    p.add_argument("--cn", default=None)
    p.add_argument("--days", type=int, default=365)
    p.set_defaults(func=cmd_issue_broadcaster)

    p = subs.add_parser("show", help="show a certificate")
    p.add_argument("--dir", required=True)
    p.add_argument("--name", default=None, help="broadcaster name; omit for root")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
