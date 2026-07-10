#!/usr/bin/env python3
"""Split a merchant's letter-crop pool into BODY vs HEAVY rows for a
section-conditioned mint.

Joins two sources per receipt:
  * QA'd ``ReceiptSection`` rows (validation_status=VALID) -> the line_ids that
    belong to a real section (~99% line purity), so pooling ignores OCR noise.
  * ``glyphstudio.stylescan`` per-line measurements -> tier + stroke/cap, so a
    row is judged BOLD relative to the receipt's own body median (scanner
    exposure cancels out).

A visual line is put in the HEAVY pool when it is inside a VALID section AND
prints bold with the PR#1098 noise guard (stylescan tier=bold, i.e. stroke
>=1.30x body_stroke and cap < 1.45x body_cap, AND stroke/cap ratio >=1.30x the
body stroke/cap ratio -- a genuinely thicker stroke, not merely larger text).
``normal``-tier VALID lines go to the BODY pool; ``large``-tier and
bold-but-fails-guard rows are excluded from both (ambiguous).

Reads pre-computed stylescan JSONs from ``--scan-dir`` (files named
``<image_id>_<receipt_id>_<slug>.json`` as written by ``stylescan``); it does
not hit S3 itself. Writes ``<slug>_heavy_pool.json`` / ``<slug>_body_pool.json``
in ``--out-dir`` as ``{"<image_id>#<receipt_id>": [line_id, ...]}`` maps ready
for ``build_merchant_glyphs.py``'s GLYPH_LINE_KEYS_JSON.

Usage:
  section_bold_pool.py "Sprouts Farmers Market" sprouts \\
      --scan-dir SCANS --out-dir OUT [--max-ocr-overlap 2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter


def is_bold_guarded(line: dict, body_cap, body_stroke) -> bool:
    if line.get("tier") != "bold":
        return False
    cap, stroke = line.get("cap_px"), line.get("stroke_med")
    if not cap or not stroke or not body_cap or not body_stroke:
        return False
    return (stroke / cap) >= 1.30 * (body_stroke / body_cap)


def valid_sections(table: str, region: str = "us-east-1"):
    """Map (image_id, receipt_id) -> {section_type: [line_id,...]} for VALID."""
    sys.path.insert(
        0,
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "receipt_dynamo"),
    )
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table_name=table, region=region)
    out: dict[tuple[str, int], dict[str, list[int]]] = {}
    lek = None
    while True:
        rows, lek = client.list_receipt_sections(limit=500, last_evaluated_key=lek)
        for s in rows:
            if s.validation_status == "VALID":
                key = (str(s.image_id), int(s.receipt_id))
                out.setdefault(key, {})[s.section_type] = [
                    int(x) for x in s.line_ids
                ]
        if not lek:
            break
    return out


def build(scan_dir, slug, valid, max_ocr_overlap=2):
    heavy: dict[str, list[int]] = {}
    body: dict[str, list[int]] = {}
    stats = Counter()
    sect = Counter()
    charhist = Counter()
    for (iid, rid), sections in valid.items():
        fp = os.path.join(scan_dir, f"{iid}_{rid}_{slug}.json")
        if not os.path.exists(fp):
            continue
        try:
            res = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if res.get("error") or not res.get("lines"):
            continue
        if (res.get("_ocr_overlap") or 0) > max_ocr_overlap:
            stats["ocr_rejected"] += 1
            continue
        valid_lineids: set[int] = set()
        sect_of: dict[int, str] = {}
        for stype, lids in sections.items():
            for lid in lids:
                valid_lineids.add(int(lid))
                sect_of[int(lid)] = stype
        bc, bs = res.get("body_cap_px"), res.get("body_stroke_px")
        stats["receipts"] += 1
        hl: list[int] = []
        bl: list[int] = []
        for line in res["lines"]:
            inside = {int(x) for x in line.get("line_ids", [])} & valid_lineids
            if not inside:
                continue
            if line.get("tier") == "large":
                continue
            if is_bold_guarded(line, bc, bs):
                hl += list(inside)
                stats["bold_lines"] += 1
                for lid in sorted(inside):
                    sect[sect_of.get(lid, "?")] += 1
                    break
                for chd in line.get("letters", []):
                    ch = (chd.get("ch") or "").strip()
                    if len(ch) == 1 and 33 <= ord(ch) < 127:
                        charhist[ch] += 1
            elif line.get("tier") == "normal":
                bl += list(inside)
                stats["body_lines"] += 1
        if hl:
            heavy[f"{iid}#{rid}"] = sorted(set(hl))
        if bl:
            body[f"{iid}#{rid}"] = sorted(set(bl))
    return heavy, body, stats, sect, charhist


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("merchant")
    ap.add_argument("slug")
    ap.add_argument("--scan-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-ocr-overlap", type=int, default=2)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    valid = valid_sections(args.table, args.region)
    heavy, body, stats, sect, charhist = build(
        args.scan_dir, args.slug, valid, args.max_ocr_overlap
    )
    hp = os.path.join(args.out_dir, f"{args.slug}_heavy_pool.json")
    bp = os.path.join(args.out_dir, f"{args.slug}_body_pool.json")
    json.dump(heavy, open(hp, "w", encoding="utf-8"), indent=1)
    json.dump(body, open(bp, "w", encoding="utf-8"), indent=1)
    ge10 = "".join(sorted(c for c in charhist if charhist[c] >= 10))
    print(
        json.dumps(
            {
                "heavy_pool": hp,
                "body_pool": bp,
                "heavy_receipts": len(heavy),
                "body_receipts": len(body),
                "stats": dict(stats),
                "bold_sections": dict(sect.most_common()),
                "chars_ge10_samples": ge10,
                "n_chars_ge10": len(ge10),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
