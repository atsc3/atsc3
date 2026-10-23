"""Command-line interface for the OpenATSC3 PKI toolkit.

    openatsc3-pki init-root --dir ca [--cn ...]
    openatsc3-pki issue-ca --dir ca
    openatsc3-pki issue-broadcaster --dir ca --name WHUT --bsid 540
    openatsc3-pki show --dir ca --name WHUT
    openatsc3-pki revoke --dir ca --name WHUT
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import ca as ca_mod
from . import oids
from .x509 import SubjectInfo


def _write_0600(path: str, data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
    finally:
        os.close(fd)


def _public_der(public) -> bytes:
    return public.public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _expected_public(path):
    from . import x509
    with open(path, "rb") as fh:
        return x509.load_pem(fh.read()).cert.public_key()


def _matches_expected(key, expected) -> bool:
    return _public_der(key.public) == _public_der(expected)


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


def _read_shares(paths):
    out = []
    for path in paths:
        with open(path, "rb") as fh:
            out.append(fh.read())
    return out


def _load_signer_key(key_path, share_paths):
    """A signing key from a PEM file, or reconstructed from a k-of-n quorum."""
    from . import keys, quorum

    if share_paths:
        try:
            return keys.load_private_bytes(quorum.combine(_read_shares(share_paths)))
        except ValueError:
            print("error: shares did not reconstruct a valid key "
                  "(need at least the threshold)", file=sys.stderr)
            return None
    return keys.load_private(key_path)


def cmd_sign_request(args) -> int:
    """Offline: sign a ceremony request with a CA key, or a k-of-n quorum of shares."""
    from . import ceremony, x509

    key = _load_signer_key(args.key, args.shares)
    if key is None:
        return 2
    if args.issuer and not _matches_expected(key, _expected_public(args.issuer)):
        print("error: quorum/key does not match the expected issuer certificate",
              file=sys.stderr)
        return 2
    with open(args.request, "rb") as fh:
        request = fh.read()
    issuer = None
    if args.issuer:
        with open(args.issuer, "rb") as fh:
            issuer = x509.load_pem(fh.read())
    try:
        cert = ceremony.sign_request(request, key, issuer=issuer)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = cert.pem() if args.out.endswith(".pem") else cert.der()
    _write_0600(args.out, data)
    print(f"signed request -> {args.out}")
    _print_cert(cert)
    return 0


def cmd_cross_sign(args) -> int:
    """Offline: cross-sign a new root under the outgoing root (rotation bridge)."""
    from . import x509

    key = _load_signer_key(args.key, args.shares)
    if key is None:
        return 2
    with open(args.new_root, "rb") as fh:
        new_root = x509.load_pem(fh.read())
    with open(args.old_root, "rb") as fh:
        old_issuer = x509.load_pem(fh.read())
    try:
        cross = x509.cross_sign_root(new_root, old_issuer, key, days=args.days)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = cross.pem() if args.out.endswith(".pem") else cross.der()
    _write_0600(args.out, data)
    print(f"cross-signed new root -> {args.out} (issuer: {old_issuer.subject})")
    _print_cert(cross)
    return 0


def cmd_quorum_split(args) -> int:
    """Split a root key into n shares with threshold k (air-gapped quorum custody)."""
    from . import keys, quorum

    with open(args.key, "rb") as fh:
        secret = fh.read()
    try:
        keys.load_private_bytes(secret)
        shares = quorum.split(secret, args.threshold, args.shares)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if quorum.combine(shares[:args.threshold]) != secret:
        print("error: self-check failed; nothing written", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    written = []
    for index, share in enumerate(shares, start=1):
        path = str(out_dir / f"share-{index}")
        _write_0600(path, share)
        written.append(path)
    print(f"wrote {len(written)} shares ({args.threshold}-of-{args.shares}) "
          f"to {out_dir}")
    print("verify a quorum reconstructs, then destroy the original key media.")
    return 0


def cmd_quorum_combine(args) -> int:
    """Reconstruct a key from shares (recovery/HSM import)."""
    from . import keys, quorum

    try:
        secret = quorum.combine(_read_shares(args.shares))
        key = keys.load_private_bytes(secret)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.expect_cert and not _matches_expected(
            key, _expected_public(args.expect_cert)):
        print("error: shares do not reconstruct the expected key",
              file=sys.stderr)
        return 2
    _write_0600(args.out, secret)
    print(f"reconstructed key -> {args.out} (0600)")
    return 0


def cmd_quorum_reshare(args) -> int:
    """Re-split into fresh shares in memory (custodian change, Ceremony E)."""
    from . import keys, quorum

    old = _read_shares(args.shares)
    try:
        new = quorum.reshare(old, args.threshold, args.shares_out)
        secret = quorum.combine(old)
        key = keys.load_private_bytes(secret)
    except ValueError:
        print("error: shares did not reconstruct a valid key "
              "(need at least the threshold); nothing written", file=sys.stderr)
        return 2
    if args.expect_cert and not _matches_expected(
            key, _expected_public(args.expect_cert)):
        print("error: shares do not reconstruct the expected key; nothing written",
              file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    written = []
    for index, share in enumerate(new, start=1):
        path = str(out_dir / f"share-{index}")
        _write_0600(path, share)
        written.append(path)
    if quorum.combine(new[:args.threshold]) != secret:
        print("error: self-check failed; shares not written correctly",
              file=sys.stderr)
        return 2
    print(f"wrote {len(written)} fresh shares ({args.threshold}-of-{args.shares_out}) "
          f"to {out_dir}")
    print("destroy the superseded shares and any reconstructed key media.")
    return 0


def cmd_quorum_verify(args) -> int:
    """Check that shares reconstruct a valid key without writing it out."""
    from . import keys, quorum

    try:
        key = keys.load_private_bytes(quorum.combine(_read_shares(args.shares)))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.expect_cert and not _matches_expected(
            key, _expected_public(args.expect_cert)):
        print("error: shares do not reconstruct the expected key",
              file=sys.stderr)
        return 2
    print(f"ok: {len(args.shares)} shares reconstruct a valid {key.curve} key")
    return 0


def _read_pairs(specs, loader):
    out = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not name:
            print(f"error: expected NAME=PATH, got {spec!r}", file=sys.stderr)
            return None
        out[name] = loader(path)
    return out


def cmd_minutes(args) -> int:
    """Write a tamper-evident minutes record for a ceremony operation."""
    from . import rotation, x509

    def _read(path):
        with open(path, "rb") as fh:
            return fh.read()

    def _cert(path):
        with open(path, "rb") as fh:
            return x509.load_pem(fh.read())

    artifacts = _read_pairs(args.artifact, _read)
    certs = _read_pairs(args.cert, _cert)
    if artifacts is None or certs is None:
        return 2
    record = rotation.build_minutes(args.op, artifacts=artifacts, certs=certs,
                                    witnesses=args.witness, note=args.note)
    if args.key or args.shares:
        key = _load_signer_key(args.key, args.shares)
        if key is None:
            return 2
        record = rotation.sign_minutes(record, key)
    _write_0600(args.out, (json.dumps(record, indent=2) + "\n").encode())
    signed = "signed" if "signature" in record else "unsigned"
    print(f"minutes ({signed}) -> {args.out} "
          f"(digest {record['digest'][:16]}...)")
    return 0


def cmd_verify_minutes(args) -> int:
    """Verify a minutes record's self-digest (and signature, if present)."""
    from . import rotation, x509

    with open(args.minutes, "rb") as fh:
        record = json.load(fh)
    issuer = None
    if args.issuer:
        with open(args.issuer, "rb") as fh:
            issuer = x509.load_pem(fh.read())
    try:
        rotation.verify_minutes(record, issuer)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"ok: {record.get('op')} minutes, {len(record.get('witnesses', []))} "
          f"witness(es), digest {record['digest'][:16]}...")
    return 0


def cmd_rotation_bundle(args) -> int:
    """Offline: assemble the distribution artifacts for a root rotation."""
    from . import rotation, x509

    with open(args.new_root, "rb") as fh:
        new_root = x509.load_pem(fh.read())
    with open(args.cross, "rb") as fh:
        cross = x509.load_pem(fh.read())
    old_root = None
    if args.old_root:
        with open(args.old_root, "rb") as fh:
            old_root = x509.load_pem(fh.read())
    files = rotation.rotation_bundle(new_root, cross, old_root,
                                     witnesses=args.witness)
    out_dir = Path(args.out_dir)
    for name, data in files.items():
        _write_0600(str(out_dir / name), data)
    print(f"rotation bundle -> {out_dir} ({len(files)} files, "
          f"manifest {rotation.MANIFEST_NAME})")
    return 0


def _print_cert(cert) -> None:
    from cryptography import x509 as _cx509

    try:
        bc = cert.cert.extensions.get_extension_for_class(
            _cx509.BasicConstraints).value
        ca = f"{bc.ca} (pathLen={bc.path_length})"
    except _cx509.ExtensionNotFound:
        ca = "absent"
    public = cert.cert.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    print(cert.cert.subject.rfc4514_string())
    print(f"  issuer:    {cert.cert.issuer.rfc4514_string()}")
    print(f"  serial:    {cert.cert.serial_number}")
    print(f"  notBefore: {cert.cert.not_valid_before_utc.isoformat()}")
    print(f"  notAfter:  {cert.cert.not_valid_after_utc.isoformat()}")
    print(f"  basicConstraints: {ca}")
    print(f"  publicKeySha256:  {hashlib.sha256(public).hexdigest()}")
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
    p.add_argument("--issuer", default=None,
                   help="expected issuer certificate PEM (binds key/request)")
    p.add_argument("--out", required=True, help="output certificate (.pem or .der)")
    p.set_defaults(func=cmd_sign_request)

    p = subs.add_parser("cross-sign",
                        help="offline: cross-sign a new root under the outgoing root")
    p.add_argument("--new-root", required=True,
                   help="new root's self-signed certificate PEM")
    p.add_argument("--old-root", required=True,
                   help="outgoing root certificate PEM (issuer/anchor)")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--key", help="outgoing root private key PEM")
    source.add_argument("--shares", nargs="+", metavar="SHARE",
                        help="k-of-n quorum shares (>= the threshold)")
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--out", required=True, help="output certificate (.pem or .der)")
    p.set_defaults(func=cmd_cross_sign)

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
    p.add_argument("--expect-cert", default=None,
                   help="expected issuer/root certificate PEM")
    p.set_defaults(func=cmd_quorum_combine)

    p = subs.add_parser("quorum-reshare",
                        help="air-gapped: re-split into fresh shares (custodian change)")
    p.add_argument("--shares", nargs="+", required=True, metavar="SHARE",
                   help="current quorum shares (>= the threshold)")
    p.add_argument("--threshold", type=int, required=True, help="k shares required")
    p.add_argument("--shares-out", type=int, required=True,
                   help="n fresh shares to create")
    p.add_argument("--out-dir", required=True, help="directory for new share-N files")
    p.add_argument("--expect-cert", default=None,
                   help="expected issuer/root certificate PEM")
    p.set_defaults(func=cmd_quorum_reshare)

    p = subs.add_parser("quorum-verify",
                        help="check that shares reconstruct a valid key (no output)")
    p.add_argument("--shares", nargs="+", required=True, metavar="SHARE")
    p.add_argument("--expect-cert", default=None,
                   help="expected issuer/root certificate PEM")
    p.set_defaults(func=cmd_quorum_verify)

    p = subs.add_parser("minutes",
                        help="write a tamper-evident minutes record for a ceremony")
    p.add_argument("--op", required=True,
                   help="operation name (e.g. sign-request, root-rotation)")
    p.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH",
                   help="an input/output file bound by SHA-256 (repeatable)")
    p.add_argument("--cert", action="append", default=[], metavar="NAME=PATH",
                   help="a certificate bound by fingerprint (repeatable)")
    p.add_argument("--witness", action="append", default=[], metavar="NAME",
                   help="a witness present (repeatable)")
    p.add_argument("--note", default="", help="free-text reason/context")
    sign = p.add_mutually_exclusive_group()
    sign.add_argument("--key", help="sign the minutes with this key PEM")
    sign.add_argument("--shares", nargs="+", metavar="SHARE",
                      help="sign the minutes with a k-of-n quorum")
    p.add_argument("--out", required=True, help="output minutes JSON")
    p.set_defaults(func=cmd_minutes)

    p = subs.add_parser("verify-minutes",
                        help="verify a minutes record's digest and signature")
    p.add_argument("--minutes", required=True, help="minutes JSON to verify")
    p.add_argument("--issuer", default=None,
                   help="issuer certificate PEM (required if signed)")
    p.set_defaults(func=cmd_verify_minutes)

    p = subs.add_parser("rotation-bundle",
                        help="offline: assemble a root-rotation distribution bundle")
    p.add_argument("--new-root", required=True,
                   help="new root's self-signed certificate PEM")
    p.add_argument("--cross", required=True,
                   help="cross-signed bridge certificate PEM (from cross-sign)")
    p.add_argument("--old-root", default=None,
                   help="outgoing root certificate PEM (included when given)")
    p.add_argument("--witness", action="append", default=[], metavar="NAME")
    p.add_argument("--out-dir", required=True, help="output bundle directory")
    p.set_defaults(func=cmd_rotation_bundle)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
