#!/usr/bin/env python3
"""M0 section-seed coverage report (READ-ONLY — never writes SECTION_* rows).

For a merchant, load its receipts' words + validated labels from DynamoDB,
project the two M0 seed sources (word labels + stylescan rules) into the
canonical section vocabulary, and report per-merchant coverage and
cross-source agreement. This is the M0 exit deliverable; the actual SECTION_*
write is gated on explicit approval (dev table, additive-only).

Usage:
  python seed_section_report.py --merchant-name "Sprouts Farmers Market" \
      [--slug sprouts] [--limit 20] [--include-medium] [--out report.json]

Env:
  DYNAMODB_TABLE_NAME   dev table (default ReceiptsTable-dc5be22)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# glyphstudio package (this dir) + sibling receipt_dynamo
sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _pkg in ("receipt_dynamo",):
    _p = os.path.join(_ROOT, _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from glyphstudio.section_seeds import (  # noqa: E402
    SeedReport,
    SeedWord,
    merchant_slug,
    seed_receipt,
)


def _validated_labels(label_dicts) -> tuple[str, ...]:
    """Keep only VALID CORE label names for a word (seeds must be ground
    truth). ``label_dicts`` is the entity list for one (line_id, word_id)."""
    out = []
    for lbl in label_dicts:
        status = getattr(lbl, "validation_status", None) or "NONE"
        if str(status).upper() == "VALID":
            out.append(str(lbl.label))
    return tuple(out)


def build_seed_words(details) -> list[SeedWord]:
    """ReceiptDetails -> SeedWord list (validated labels only)."""
    from collections import defaultdict

    labels_by_word = defaultdict(list)
    for lbl in details.labels:
        labels_by_word[(lbl.line_id, lbl.word_id)].append(lbl)

    words = []
    for w in sorted(details.words, key=lambda w: (w.line_id, w.word_id)):
        words.append(
            SeedWord(
                line_id=w.line_id,
                word_id=w.word_id,
                text=w.text,
                labels=_validated_labels(
                    labels_by_word.get((w.line_id, w.word_id), [])
                ),
            )
        )
    return words


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant-name", required=True)
    ap.add_argument(
        "--slug",
        default=None,
        help="stylescan rule slug; inferred from merchant name if omitted",
    )
    ap.add_argument("--limit", type=int, default=0, help="0 = all receipts")
    ap.add_argument("--include-medium", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--table", default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    )
    args = ap.parse_args(argv)

    slug = args.slug or merchant_slug(args.merchant_name)
    if not slug:
        ap.error(
            f"no stylescan slug for merchant {args.merchant_name!r}; pass --slug"
        )

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)

    # enumerate receipts for this merchant (stop early once --limit is met so a
    # small sample of a large merchant doesn't page the whole merchant)
    pairs, last_key = [], None
    while True:
        page_size = 1000
        if args.limit:
            page_size = min(1000, args.limit - len(pairs))
        places, last_key = client.get_receipt_places_by_merchant(
            merchant_name=args.merchant_name,
            limit=page_size,
            last_evaluated_key=last_key,
        )
        pairs.extend((p.image_id, p.receipt_id) for p in places)
        if last_key is None or (args.limit and len(pairs) >= args.limit):
            break
    if args.limit:
        pairs = pairs[: args.limit]

    report = SeedReport(merchant=slug)
    for image_id, receipt_id in pairs:
        try:
            details = client.get_receipt_details(image_id, receipt_id)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {image_id}#{receipt_id}: {e}", file=sys.stderr)
            continue
        seeds = seed_receipt(
            build_seed_words(details), slug, include_medium=args.include_medium
        )
        report.add_receipt(seeds, image_id=image_id, receipt_id=receipt_id)

    result = report.to_dict()
    print(json.dumps(result, indent=2))
    print(
        f"\n{slug}: {report.receipts} receipts, {report.total_words} words, "
        f"coverage {report.coverage:.1%}, "
        f"cross-source agreement {report.agreement:.1%} "
        f"({report.both_agree}/{report.both_present})",
        file=sys.stderr,
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
