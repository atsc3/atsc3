"""Command-line interface for the OpenATSC3 PKI toolkit.

    openatsc3-pki init-root --dir ca [--cn ...]
    openatsc3-pki issue-ca --dir ca
    openatsc3-pki issue-broadcaster --dir ca --name WHUT --bsid 540
    openatsc3-pki show --dir ca --name WHUT
    openatsc3-pki revoke --dir ca --name WHUT
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import ca as ca_mod
from . import oids
from .x509 import SubjectInfo


def _write_0600(path: str, data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


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
    _print_cert(cert)
    return 0


def cmd_sign_request(args) -> int:
    """Offline: sign a ceremony request with a CA key, or a k-of-n quorum of shares."""
    from . import ceremony, keys, quorum

    with open(args.request, "rb") as fh:
        request = fh.read()
    if args.shares:
        share_bytes = []
        for path in args.shares:
            with open(path, "rb") as fh:
                share_bytes.append(fh.read())
        try:
            key = keys.load_private_bytes(quorum.combine(share_bytes))
        except ValueError:
            print("error: shares did not reconstruct a valid key "
                  "(need at least the threshold)", file=sys.stderr)
            return 2
    else:
        key = keys.load_private(args.key)
    cert = ceremony.sign_request(request, key)
    data = cert.pem() if args.out.endswith(".pem") else cert.der()
    _write_0600(args.out, data)
    print(f"signed request -> {args.out}")
    _print_cert(cert)
    return 0


def cmd_quorum_split(args) -> int:
    """Split a root key into n shares with threshold k (air-gapped quorum custody)."""
    from . import quorum

    with open(args.key, "rb") as fh:
        secret = fh.read()
    shares = quorum.split(secret, args.threshold, args.shares)
    out_dir = Path(args.out_dir)
    written = []
    for index, share in enumerate(shares, start=1):
        path = str(out_dir / f"share-{index}")
        _write_0600(path, share)
        written.append(path)
    if quorum.combine(shares[:args.threshold]) != secret:
        print("error: self-check failed; shares not written correctly",
              file=sys.stderr)
        return 2
    print(f"wrote {len(written)} shares ({args.threshold}-of-{args.shares}) "
          f"to {out_dir}")
    print("verify a quorum reconstructs, then destroy the original key media.")
    return 0


def cmd_quorum_combine(args) -> int:
    """Reconstruct a key from shares (recovery/HSM import)."""
    from . import quorum

    share_bytes = []
    for path in args.shares:
        with open(path, "rb") as fh:
            share_bytes.append(fh.read())
    try:
        secret = quorum.combine(share_bytes)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _write_0600(args.out, secret)
    print(f"reconstructed key -> {args.out} (0600)")
    return 0


def _print_cert(cert) -> None:
    print(cert.cert.subject.rfc4514_string())
    print(f"  issuer:    {cert.cert.issuer.rfc4514_string()}")
    print(f"  serial:    {cert.cert.serial_number}")
    print(f"  notBefore: {cert.cert.not_valid_before_utc.isoformat()}")
    print(f"  notAfter:  {cert.cert.not_valid_after_utc.isoformat()}")
    print(f"  bsids:     {cert.bsids()}")
    print(f"  signaling EKU: {cert.signaling_eku()}")
    print(f"  SKI: {cert.subject_key_identifier().hex()}")


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

    p = subs.add_parser("sign-request",
                        help="offline: sign a ceremony request with a CA key/quorum")
    p.add_argument("--request", required=True,
                   help="request DER produced by the online side")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--key", help="issuing/root private key PEM")
    source.add_argument("--shares", nargs="+", metavar="SHARE",
                        help="k-of-n quorum shares (>= the threshold)")
    p.add_argument("--out", required=True, help="output certificate (.pem or .der)")
    p.set_defaults(func=cmd_sign_request)

    p = subs.add_parser("quorum-split",
                        help="air-gapped: split a root key into k-of-n shares")
    p.add_argument("--key", required=True, help="root private key PEM")
    p.add_argument("--threshold", type=int, required=True, help="k shares required")
    p.add_argument("--shares", type=int, required=True, help="n shares to create")
    p.add_argument("--out-dir", required=True, help="directory for share-N files")
    p.set_defaults(func=cmd_quorum_split)

    p = subs.add_parser("quorum-combine",
                        help="reconstruct a key from shares (recovery/import)")
    p.add_argument("--shares", nargs="+", required=True, metavar="SHARE")
    p.add_argument("--out", required=True, help="output key PEM (0600)")
    p.set_defaults(func=cmd_quorum_combine)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
