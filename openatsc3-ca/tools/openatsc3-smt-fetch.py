#!/usr/bin/env python3
"""Fetch signed ATSC 3.0 LLS tables from the OpenATSC3 CA (customer helper).

The managed service **signs and staples**: you POST your LLS table bodies and it
returns the signed CertificationData (``0x06``) — with one stapled OCSPResponse
per certificate embedded — plus the signed SignedMultiTable (``0x07``).  This
script does the whole client side: build the request, POST it, decode the
response, and write the LLS-ready bodies (with the A/331 4-byte LLS header) that
you feed to your broadcast equipment.

It is **stdlib-only** — copy it anywhere and run it with any Python 3.10+; no
install, no dependencies.

    python openatsc3-smt-fetch.py \
        --base-url https://ca.example.com --token "$TOKEN" \
        --broadcaster WHUT \
        --table 0x01:slt.xml.gz --table 0x03:app.bin \
        --out-dir out/signed

Refresh contract: each stapled OCSP response is valid for ``OCSPRefresh`` (the
CDT default is **20 hours**) from ``thisUpdate``, with an absolute ceiling of 10
days.  Re-run this script **at least daily** so the responses you carry on air
never go stale (a stale response makes the receiver reject the tables).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

#: LLS table ids carried in the broadcast (A/331 Table 6.1, A/360 5.2.2.2).
LLS_SLT = 0x01
LLS_CERTIFICATION_DATA = 0x06
LLS_SIGNED_MULTITABLE = 0x07

#: Default per-request HTTP timeout, seconds.
DEFAULT_TIMEOUT = 30.0

#: The default LLS table version for the bodies we carry.
DEFAULT_VERSION = 1


def lls_wrap(table_id: int, body: bytes, version: int = DEFAULT_VERSION) -> bytes:
    """An LLS table on the wire: ``[table_id, 0, 0, version] + body`` (A/331)."""
    return bytes([table_id & 0xFF, 0, 0, version & 0xFF]) + body


def build_request(broadcaster: str,
                  tables: Iterable[Tuple[int, int, bytes]]) -> Dict:
    """The ``POST /api/v1/smt/`` JSON body from ``(table_id, version, body)`` rows."""
    return {
        "broadcaster": broadcaster,
        "tables": [
            {"table_id": int(table_id), "version": int(version),
             "payload_b64": base64.b64encode(body).decode("ascii")}
            for table_id, version, body in tables
        ],
    }


def decode_response(payload: Dict) -> Dict[str, Optional[bytes]]:
    """Decode ``{cdt_b64, smt_b64}`` into the raw LLS bodies.

    ``cdt`` is the gzipped CertificationData XML *exactly as it goes on air*
    (LLS ``0x06`` body) — it is not decompressed here.  ``smt`` is the signed
    SignedMultiTable (LLS ``0x07`` body), or ``None`` when no tables were signed.
    """
    if "cdt_b64" not in payload:
        raise ValueError("response has no cdt_b64")
    cdt = base64.b64decode(payload["cdt_b64"], validate=True)
    smt_b64 = payload.get("smt_b64")
    smt = base64.b64decode(smt_b64, validate=True) if smt_b64 else None
    return {"cdt": cdt, "smt": smt}


def post_smt(base_url: str, token: str, request: Dict, *,
             timeout: float = DEFAULT_TIMEOUT, opener=None) -> Dict:
    """POST the request and return the decoded JSON response.

    ``opener`` is injectable for tests (default: ``urllib.request.urlopen``).
    Raises ``RuntimeError`` on an HTTP error (with the server's message).
    """
    opener = opener or urllib.request.urlopen
    url = base_url.rstrip("/") + "/api/v1/smt/"
    data = json.dumps(request).encode("utf-8")
    http_request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Token {token}"})
    try:
        with opener(http_request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def fetch(base_url: str, token: str, broadcaster: str,
          tables: Iterable[Tuple[int, int, bytes]], *,
          timeout: float = DEFAULT_TIMEOUT, opener=None) -> Dict[str, Optional[bytes]]:
    """Build, POST, and decode in one call."""
    response = post_smt(base_url, token, build_request(broadcaster, tables),
                        timeout=timeout, opener=opener)
    if "error" in response:
        raise RuntimeError(response["error"])
    return decode_response(response)


def _parse_table(spec: str) -> Tuple[int, int, Path]:
    """``TABLE_ID[:VERSION]:PATH`` -> ``(table_id, version, path)``."""
    parts = spec.split(":")
    if len(parts) == 2:
        table_id, version, path = parts[0], str(DEFAULT_VERSION), parts[1]
    elif len(parts) == 3:
        table_id, version, path = parts
    else:
        raise ValueError(f"expected TABLE_ID[:VERSION]:PATH, got {spec!r}")
    return int(table_id, 0), int(version, 0), Path(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="openatsc3-smt-fetch",
        description="Fetch signed LLS tables (CDT 0x06 + SMT 0x07) from the CA.")
    parser.add_argument("--base-url", required=True,
                        help="CA base URL, e.g. https://ca.example.com")
    parser.add_argument("--token", required=True, help="tenant API token")
    parser.add_argument("--broadcaster", required=True, help="broadcaster name")
    parser.add_argument("--table", action="append", default=[], metavar="ID[:VER]:PATH",
                        help="an LLS table body to sign (repeatable)")
    parser.add_argument("--out-dir", default="signed",
                        help="where to write the signed bodies")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    if not args.table:
        parser.error("at least one --table is required")

    try:
        tables = [_parse_table(spec) for spec in args.table]
        bodies = [(table_id, version, path.read_bytes())
                  for table_id, version, path in tables]
        result = fetch(args.base_url, args.token, args.broadcaster, bodies,
                       timeout=args.timeout)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cdt = result["cdt"]
    (out_dir / "cdt.xml.gz").write_bytes(cdt)
    (out_dir / f"lls-0x{LLS_CERTIFICATION_DATA:02x}.bin").write_bytes(
        lls_wrap(LLS_CERTIFICATION_DATA, cdt))
    written = ["cdt.xml.gz", f"lls-0x{LLS_CERTIFICATION_DATA:02x}.bin"]
    if result["smt"] is not None:
        smt = result["smt"]
        (out_dir / "signing.smt").write_bytes(smt)
        (out_dir / f"lls-0x{LLS_SIGNED_MULTITABLE:02x}.bin").write_bytes(
            lls_wrap(LLS_SIGNED_MULTITABLE, smt))
        written += ["signing.smt", f"lls-0x{LLS_SIGNED_MULTITABLE:02x}.bin"]

    print(f"wrote {len(written)} file(s) to {out_dir}: {', '.join(written)}")
    print("carry the lls-0x06 / lls-0x07 tables in your broadcast; re-fetch "
          "before the stapled OCSP expires (OCSPRefresh, default 20 h).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
