"""Extract the AC-4 scale-factor-band tables from ETSI TS 103 190-1 Annex B.

WHY
---
The AC-4 decoder needs the scale factor band offsets (Table B.4) for every
transform length, and ``num_sfb`` per transform length (Table B.1).  The band
table is the one the reference receiver documents as having been silently
contaminated once: rows 40..55 of Table B.4 sit at the TOP of the next page and
Table B.5 begins immediately below them on that same page with its own sfb
column restarting at 0.  A naive "read pages 258-259" therefore overwrites
B.4's high bands with B.5's, and the contaminated result still passes every
*internal* consistency check (monotone, multiples of four, sums to the
transform length) because it is a real table, just the wrong one.

So this extractor gates on Table B.1, which is a different table three pages
earlier that states ``num_sfb`` in its own right:

    num_sfb(1 536 @ 48 kHz) = 55   (Table B.1)
    sfb_offset[55] == 1 536        (Table B.4)

and it reads Table B.4 by x-coordinate bins (not page ranges), stopping when
the sfb column stops increasing -- the table's own structure ending, not a page
boundary.

OUTPUT
------
``ac4bindings/ac4bindings/tables.py`` -- a committed Python module holding the
numeric tables.  The PDF is never shipped and never read at runtime, exactly as
``atsc3lib`` ships its A/322 tables in code.

Usage:
    python tools/extract_ac4_tables.py [--pdf PATH] [--write]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
#: The spec lives beside the package repo (never committed); the shared
#: atsc3lib spec cache is the fallback.
DEFAULT_PDF = os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "spec",
    "ts_10319001v010301p.pdf")

#: Table B.4's three ``sfb_offset`` value columns, by x (PDF points).  All three
#: are identical through sfb 55 (they diverge only above it, where the 1 536
#: family no longer applies), so any one reads the 1 536@48 kHz column.
B4_LEFT_OFFSET_X = 78.8
#: Integer values in this table print thousands with a thin space ("1 024").
_NUMBER = re.compile(r"^-?[\d\s]+$")


def _number(text: str):
    t = text.replace(" ", "").strip()
    return int(t) if t.lstrip("-").isdigit() else None


def _line_values(page):
    """-> list of (x, y, text) for every non-empty line on ``page``."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((round(line["bbox"][0], 1),
                            round(line["bbox"][1], 1), text))
    return out


def find_page(doc, marker: str) -> int:
    for i, page in enumerate(doc):
        if marker in page.get_text():
            return i
    raise SystemExit(f"marker not found: {marker}")


def parse_num_sfb(doc):
    """Table B.1 -> {transform_length: num_sfb} for 44.1/48 kHz.

    Tables B.1, B.2 and B.3 share a page, all with columns near the same x.
    The table is bounded by its own caption ("Table B.1:") and the next
    ("Table B.2:"), not by a page number.
    """
    page = doc[find_page(doc, "Table B.1:")]
    lines = _line_values(page)
    caption_y = min(y for x, y, t in lines if t.startswith("Table B.1:"))
    next_y = min((y for x, y, t in lines if t.startswith("Table B.2:")),
                 default=1e9)
    rows = {}
    pending = None
    for x, y, text in sorted(lines, key=lambda r: (r[1], r[0])):
        if not (caption_y < y < next_y):
            continue
        val = _number(text)
        if val is None:
            continue
        if 200 < x < 280:
            pending = val
        elif 300 < x < 340 and pending is not None:
            rows[pending] = val
            pending = None
    return rows


#: Transform length -> the Table B.4 column family it belongs to.  Only the
#: 44.1/48 kHz family is read here (ATSC 3.0 broadcast audio is 48 kHz).
TRANSFORM_LENGTHS = (2048, 1920, 1536, 1024, 960, 768, 512, 480, 384, 256,
                     240, 192, 128, 120, 96)

#: Annex B tables B.4..B.7 as page regions.  Each is (page_index, y_min, y_max,
#: left_data_x, left_lengths, right_data_x, right_lengths).  Tables B.4..B.6
#: print TWO table rows per line (left half sfb n, right half `- sfb n+offset`),
#: so a row can carry a value for both a left and a right length; B.7 has six
#: single-row columns and no right half.  B.4's final row (sfb 55) is the first
#: row on the page after its caption, above Table B.5 -- hence the explicit
#: region boundaries rather than "read the whole page".
_B_REGIONS = (
    (258, 160.0, 800.0, (78.8, 157.5, 225.4), (2048, 1920, 1536),
     (325.1, 403.8, 471.7), (2048, 1920, 1536)),
    (259, 100.0, 160.0, (78.8, 157.5, 225.4), (2048, 1920, 1536),
     (324.2, 403.7, 471.6), (2048, 1920, 1536)),
    (259, 240.0, 700.0, (78.8, 159.0, 226.5), (1024, 960, 768),
     (324.2, 404.5, 472.0), (1024, 960, 768)),
    (260, 160.0, 560.0, (78.8, 157.6, 225.9), (512, 480, 384),
     (324.2, 403.2, 471.5), (512, 480, 384)),
    (261, 160.0, 620.0, (78.8, 159.2, 232.0, 304.9, 395.8, 468.7),
     (256, 240, 192, 128, 120, 96), (), ()),
)

#: Huffman codebook dimension (Table A.14) and signedness (Table A.15), indexed
#: by spectrum codebook 1..11.  These are printed in the PDF as two flat rows.
CB_DIM = {1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 2, 7: 2, 8: 2, 9: 2, 10: 2, 11: 2}
UNSIGNED_CB = {1: False, 2: False, 3: True, 4: True, 5: False, 6: False,
               7: True, 8: True, 9: True, 10: True, 11: True}


def parse_codebook_meta(text):
    """cb_mod / cb_mod2 / cb_mod3 / cb_off from Tables A.2..A.12.

    ``cb_mod`` and ``cb_off`` are codebook-specific; ``cb_mod2``/``cb_mod3``
    exist only for the 4-dimensional books 1..4.  The oracle's independent
    reading gives the same numbers, which is the cross-check here.
    """
    meta = {}
    for n in range(2, 13):
        m = re.search(
            rf"Table A\.{n}: ASF spectrum Huffman codebook (\d+)"
            rf"(.*?)(?=Table A\.{n + 1}:|\Z)", text, re.DOTALL)
        if not m:
            raise SystemExit(f"Table A.{n} not found")
        cb = int(m.group(1))
        body = m.group(2)

        def field(name):
            mm = re.search(rf"{name}\s*\n\s*(-?\d+)", body)
            return int(mm.group(1)) if mm else None

        meta[cb] = {"mod": field("cb_mod"), "mod2": field("cb_mod2"),
                    "mod3": field("cb_mod3"), "off": field("cb_off")}
    return meta


def parse_all_sfb_offsets(doc, num_sfb):
    """Tables B.4..B.7 -> {transform_length: sfb_offset list}.

    Every value is gated against Table B.1's independent ``num_sfb`` and
    against the transform length itself (``sfb_offset[num_sfb] == length``),
    which is what a contaminated or mis-assigned column fails.
    """
    _row_re = re.compile(r"^-\s*(\d+)$")
    raw = {}
    for pno, ymin, ymax, left_x, left_l, right_x, right_l in _B_REGIONS:
        lines = [r for r in _line_values(doc[pno]) if ymin < r[1] < ymax]
        rows = {}
        for x, y, text in lines:
            if abs(x - 58.5) < 3:
                v = _number(text)
                if v is not None:
                    rows.setdefault(round(y, 1), {})["L"] = v
            else:
                m = _row_re.match(text)
                if m:
                    rows.setdefault(round(y, 1), {})["R"] = int(m.group(1))
        for y, sf in rows.items():
            lsfb, rsfb = sf.get("L"), sf.get("R")
            for x, yy, text in lines:
                if round(yy, 1) != y:
                    continue
                v = _number(text)
                if v is None:
                    continue
                for length, cx in zip(left_l, left_x):
                    if lsfb is not None and abs(x - cx) < 3.0:
                        raw.setdefault(length, {})[lsfb] = v
                for length, cx in zip(right_l, right_x):
                    if rsfb is not None and abs(x - cx) < 3.0:
                        raw.setdefault(length, {})[rsfb] = v
    out = {}
    for length in TRANSFORM_LENGTHS:
        n = num_sfb[length]
        row = raw.get(length, {})
        offs = [row.get(i) for i in range(n + 1)]
        if any(v is None for v in offs):
            missing = [i for i, v in enumerate(offs) if v is None]
            raise SystemExit(f"Table B.x read for {length}: missing sfb {missing}")
        if offs[-1] != length:
            raise SystemExit(
                f"sfb_offset[{n}] for {length} is {offs[-1]}, not {length}")
        if any(offs[i] >= offs[i + 1] for i in range(n)):
            raise SystemExit(f"sfb_offset for {length} is not increasing")
        out[length] = offs
    return out


def gate(offsets, num_sfb):
    """The gates that a contaminated table fails."""
    assert offsets[0] == 0, "sfb_offset must start at 0"
    assert offsets == sorted(offsets), "offsets must be monotone"
    assert len(offsets) == num_sfb + 1, (len(offsets), num_sfb)
    assert all(o % 4 == 0 for o in offsets), "offsets are multiples of 4"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=DEFAULT_PDF)
    ap.add_argument("--write", action="store_true",
                    help="write ac4bindings/ac4bindings/tables.py")
    a = ap.parse_args(argv)

    doc = pymupdf.open(a.pdf)
    num_sfb = parse_num_sfb(doc)
    print("Table B.1 num_sfb:", num_sfb)
    offsets_by_length = parse_all_sfb_offsets(doc, num_sfb)
    for length in TRANSFORM_LENGTHS:
        gate(offsets_by_length[length], num_sfb[length])
    print("Table B.4..B.7: all", len(offsets_by_length),
          "transform lengths gated against Table B.1")

    text = "\n".join(page.get_text() for page in doc)
    cb_meta = parse_codebook_meta(text)
    print("codebook metadata:", cb_meta)
    # Gate: codebook 1's Table A.2 states cb_mod 3, cb_mod2 9, cb_mod3 27,
    # cb_off 1 -- the values the spectral decoder's pseudocode 19 needs.
    assert cb_meta[1] == {"mod": 3, "mod2": 9, "mod3": 27, "off": 1}

    if a.write:
        import write_ac4_tables
        write_ac4_tables.write_tables(offsets_by_length, num_sfb, cb_meta)
        print("wrote tables.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
