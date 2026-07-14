#!/usr/bin/env python3
"""Emit face-map v2: (merchant, section) -> face priors from QA'd sections.

Where the M2 CLI (section_face_map_cli.py) read each merchant's committed
stylemap.json (sections attributed by stylescan's RULE tables), this measures:
it walks every receipt that has VALID ``ReceiptSection`` rows in Dynamo, vets
its OCR (``ocr_overlap_score`` <= --max-overlaps, the locked M3 rule), runs
stylescan's per-line typography measurement over the real scan (cdn image
preferred by the loader), joins measured lines to the QA'd sections by OCR
line id, and aggregates per (merchant, section) with M2's Face semantics.
Pure join/aggregation logic lives in :mod:`glyphstudio.face_map_v2`.

Reads Dynamo + S3 only; writes nothing but the output JSON.

Usage:
  python face_map_v2_cli.py --out ../maps/section_face_map_v2.json \
      [--table ReceiptsTable-dc5be22] [--workers 8] [--limit N] \
      [--merchant-slug sprouts ...]

Env: AWS creds; DYNAMODB_TABLE_NAME (or --table).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_upload"),
    os.path.join(_ROOT, "synthesis_loop"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from m3_acceptance import ocr_overlap_score  # noqa: E402
from glyphstudio.face_map_v2 import (  # noqa: E402
    LineFace,
    aggregate_cell,
    assign_sections,
    items_body_baseline,
    line_face,
)
from glyphstudio.section_seeds import merchant_slug  # noqa: E402

_LOCAL = threading.local()


def _client(table: str):
    if getattr(_LOCAL, "client", None) is None:
        from receipt_dynamo.data.dynamo_client import DynamoClient

        _LOCAL.client = DynamoClient(table)
    return _LOCAL.client


def generic_slug(name: str) -> str:
    """Fallback merchant key for merchants outside MERCHANT_SLUGS.

    Same squashed-lowercase style as the calibrated slugs so the map speaks
    one naming convention ("Whole Foods Market" -> "wholefoodsmarket").
    """
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "unknown"


def resolve_slug(merchant_name: str | None) -> str | None:
    if not merchant_name or merchant_name.strip().lower() == "unknown":
        return None
    return merchant_slug(merchant_name) or generic_slug(merchant_name)


def load_corpus(table: str):
    """All VALID sections grouped per receipt + the merchant of each receipt."""
    client = _client(table)
    sections, lek = [], None
    while True:
        batch, lek = client.list_receipt_sections(
            limit=1000, last_evaluated_key=lek
        )
        sections.extend(batch)
        if lek is None:
            break
    valid = [s for s in sections if s.validation_status == "VALID"]

    places, lek = [], None
    while True:
        batch, lek = client.list_receipt_places(
            limit=1000, last_evaluated_key=lek
        )
        places.extend(batch)
        if lek is None:
            break
    merchant_of = {(p.image_id, p.receipt_id): p.merchant_name for p in places}

    by_receipt: dict[tuple[str, int], list] = defaultdict(list)
    for s in valid:
        by_receipt[(s.image_id, s.receipt_id)].append(s)
    return by_receipt, merchant_of, Counter(s.model_source for s in valid)


def process_receipt(table: str, image_id: str, receipt_id: int, slug: str, sections):
    """Vet -> measure -> join -> per-line faces for ONE receipt."""
    from glyphstudio.stylescan import _MERCHANT_RULES, measure

    client = _client(table)
    words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    word_dicts = [
        {
            "bbox": [
                w.top_left["x"] * 1000,
                w.top_left["y"] * 1000,
                w.bottom_right["x"] * 1000,
                w.bottom_right["y"] * 1000,
            ]
        }
        for w in words
    ]
    overlaps = ocr_overlap_score(word_dicts)
    if overlaps > process_receipt.max_overlaps:
        return {"status": "vetted_out", "overlaps": overlaps}

    # stylescan's rule slug only steers its own body fallback / section names
    # (which v2 ignores in favor of the QA'd join); unknown merchants use the
    # generic rules.
    rule_slug = slug if slug in _MERCHANT_RULES else "sprouts"
    measurement = measure(image_id, receipt_id, merchant=rule_slug)

    sec_dicts = [
        {"section_type": s.section_type, "line_ids": s.line_ids}
        for s in sections
    ]
    assigned, ambiguous = assign_sections(measurement["lines"], sec_dicts)
    body_cap, body_stroke, baseline = items_body_baseline(
        assigned, measurement["body_cap_px"], measurement["body_stroke_px"]
    )
    faces: list[tuple[str, LineFace]] = []
    unmeasured = 0
    for line, section in assigned:
        if section is None:
            continue
        lf = line_face(line, body_cap, body_stroke, (image_id, receipt_id))
        if lf is None:
            unmeasured += 1
            continue
        faces.append((section, lf))
    return {
        "status": "ok",
        "faces": faces,
        "ambiguous": ambiguous,
        "unmeasured": unmeasured,
        "baseline": baseline,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--fonts", default=os.path.abspath(os.path.join(_HERE, "..", "fonts"))
    )
    ap.add_argument("--threshold", type=float, default=0.60)
    ap.add_argument(
        "--max-overlaps",
        type=int,
        default=2,
        help="M3 OCR vetting: skip receipts with more x-overlapping pairs",
    )
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="debug: first N receipts")
    ap.add_argument(
        "--merchant-slug",
        action="append",
        default=None,
        help="debug: restrict to these merchant slugs (repeatable)",
    )
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    process_receipt.max_overlaps = args.max_overlaps

    by_receipt, merchant_of, model_sources = load_corpus(args.table)
    jobs = []
    skipped_no_merchant = 0
    for key, sections in sorted(by_receipt.items()):
        slug = resolve_slug(merchant_of.get(key))
        if slug is None:
            skipped_no_merchant += 1
            continue
        if args.merchant_slug and slug not in args.merchant_slug:
            continue
        jobs.append((key, slug, sections))
    if args.limit:
        jobs = jobs[: args.limit]
    print(
        f"corpus: {len(by_receipt)} receipts with VALID sections; "
        f"{len(jobs)} to measure ({skipped_no_merchant} without merchant)",
        file=sys.stderr,
    )

    cells: dict[tuple[str, str], list[LineFace]] = defaultdict(list)
    stats = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_receipt, args.table, key[0], key[1], slug, sections
            ): (key, slug)
            for key, slug, sections in jobs
        }
        for i, fut in enumerate(as_completed(futures), 1):
            (image_id, receipt_id), slug = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001 - missing image etc.
                stats["load_failed"] += 1
                print(
                    f"  [{i}/{len(futures)}] {image_id[:8]}#{receipt_id} "
                    f"({slug}): FAILED {exc}",
                    file=sys.stderr,
                )
                continue
            if res["status"] == "vetted_out":
                stats["vetted_out"] += 1
                print(
                    f"  [{i}/{len(futures)}] {image_id[:8]}#{receipt_id} "
                    f"({slug}): vetted out ({res['overlaps']} OCR overlaps)",
                    file=sys.stderr,
                )
                continue
            stats["measured"] += 1
            stats["lines_ambiguous"] += res["ambiguous"]
            stats["lines_unmeasured"] += res["unmeasured"]
            stats[f"baseline_{res['baseline']}"] += 1
            for section, lf in res["faces"]:
                cells[(slug, section)].append(lf)
            if i % 50 == 0:
                print(f"  [{i}/{len(futures)}] ...", file=sys.stderr)

    # families: same discovery the v1 CLI runs (glyph-atlas IoU over the
    # merchants that HAVE committed fonts); everyone else is their own family.
    from glyphstudio.family_cluster import discover_families
    from glyphstudio.section_face_map import families_to_merchant_family

    font_dirs = {
        n: os.path.join(args.fonts, n)
        for n in sorted(os.listdir(args.fonts))
        if os.path.exists(os.path.join(args.fonts, n, "font.json"))
    }
    fam_res = discover_families(font_dirs, threshold=args.threshold)
    merchant_family = families_to_merchant_family(fam_res.families)

    entries = []
    for (merchant, section), members in sorted(cells.items()):
        agg = aggregate_cell(members)
        entries.append(
            {
                "merchant": merchant,
                "section": section,
                "family": merchant_family.get(merchant, merchant),
                "face": asdict(agg["face"]),
                "n_lines": agg["n_lines"],
                "n_receipts": agg["n_receipts"],
                "low_confidence": agg["low_confidence"],
                "reverse_video_rate": agg["reverse_video_rate"],
            }
        )

    obj = {
        "version": 2,
        "source": "receipt-sections-valid",
        "table": args.table,
        "families": fam_res.families,
        "threshold": fam_res.threshold,
        "map": entries,
        "meta": {
            "receipts_with_valid_sections": len(by_receipt),
            "receipts_measured": stats["measured"],
            "receipts_vetted_out": stats["vetted_out"],
            "receipts_load_failed": stats["load_failed"],
            "receipts_without_merchant": skipped_no_merchant,
            "baseline_items": stats["baseline_items"],
            "baseline_stylescan": stats["baseline_stylescan"],
            "lines_ambiguous": stats["lines_ambiguous"],
            "lines_unmeasured": stats["lines_unmeasured"],
            "max_overlaps": args.max_overlaps,
            "model_sources": dict(sorted(model_sources.items())),
        },
    }
    text = json.dumps(obj, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        n_ok = sum(1 for e in entries if not e["low_confidence"])
        print(
            f"wrote {args.out}: {len(entries)} cells "
            f"({n_ok} confident, {len(entries) - n_ok} low-confidence) "
            f"over {len({e['merchant'] for e in entries})} merchants",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
