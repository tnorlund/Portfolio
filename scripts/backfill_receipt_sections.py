#!/usr/bin/env python3
"""Backfill ReceiptSections for receipts that have none, via the
CANONICAL ingest assigner (assign_and_persist_sections) -- no forked
logic. Designed for the prod rollout: writing a canonical ITEMS section
routes through the DynamoDB stream to the LINE_ITEMS queue, so the
deployed line-item Lambda computes RECEIPT_LINE_ITEM rows per receipt
automatically; reconciliation mismatches self-trigger capped regional
re-OCR. The section backfill IS the line-item rollout.

Prerequisite: ReceiptRow entities exist (scripts/backfill_receipt_rows.py).

Safety:
  - Dry-run by default; --apply to write.
  - Additive only: assign_and_persist_sections writes ONLY section types
    the receipt does not already have (existing sections untouched).
  - Sections land PENDING with model_source upload-determinism-v1, the
    same provenance as ingest.
  - --throttle sleeps between receipts so the stream->SQS->Lambda chain
    drains steadily instead of bursting.

Usage:
  python scripts/backfill_receipt_sections.py \
      --table ReceiptsTable-d7ff76a --apply
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from receipt_dynamo import DynamoClient
from receipt_upload.section_assignment import (
    assign_and_persist_sections,
    assign_row_sections,
    load_prior_model,
    sections_from_assignments,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill sections via the canonical ingest assigner"
    )
    p.add_argument("--table", required=True)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write sections (default: dry-run, report only)",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--image-id", default=None)
    p.add_argument(
        "--throttle",
        type=float,
        default=0.5,
        help="Seconds to sleep between receipts when applying",
    )
    p.add_argument("--report-json", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    client = DynamoClient(args.table)
    model = load_prior_model()

    receipts, lek = client.list_receipts(limit=None)
    while lek:
        more, lek = client.list_receipts(limit=None, last_evaluated_key=lek)
        receipts.extend(more)
    if args.image_id:
        receipts = [r for r in receipts if r.image_id == args.image_id]
    print(f"{len(receipts)} receipts in {args.table}")

    stats = Counter()
    predicted_types = Counter()
    done = 0
    for r in receipts:
        if args.limit and done >= args.limit:
            break
        try:
            existing = client.get_receipt_sections_from_receipt(
                r.image_id, r.receipt_id
            )
            if any(str(s.section_type).upper() == "ITEMS" for s in existing):
                stats["has-items-section"] += 1
                continue
            rows = client.get_receipt_rows_from_receipt(
                r.image_id, r.receipt_id
            )
            if not rows:
                stats["no-rows"] += 1
                continue
            lines = client.list_receipt_lines_from_receipt(
                r.image_id, r.receipt_id
            )
            merchant = None
            try:
                rec = client.get_receipt_summary(r.image_id, r.receipt_id)
                merchant = getattr(
                    getattr(rec, "summary", None), "merchant_name", None
                )
            except Exception:  # noqa: BLE001 - summary optional here
                pass

            if args.apply:
                created, _ = assign_and_persist_sections(
                    client, rows, lines, merchant, model
                )
                for s in created:
                    predicted_types[str(s.section_type)] += 1
                stats["applied" if created else "nothing-to-add"] += 1
                if args.throttle:
                    time.sleep(args.throttle)
            else:
                preds = sections_from_assignments(
                    assign_row_sections(rows, lines, model, merchant)
                )
                existing_types = {str(s.section_type) for s in existing}
                new = [
                    s
                    for s in preds
                    if str(s.section_type) not in existing_types
                ]
                for s in new:
                    predicted_types[str(s.section_type)] += 1
                stats["would-apply" if new else "nothing-to-add"] += 1
            done += 1
            if done % 50 == 0:
                print(f"  {done} processed", flush=True)
        except Exception as exc:  # noqa: BLE001 - report, keep going
            stats["error"] += 1
            print(f"  ERROR {r.image_id[:8]} r{r.receipt_id}: {exc}")

    report = {
        "stats": dict(stats),
        "section_types": dict(predicted_types),
        "mode": "apply" if args.apply else "dry-run",
    }
    print(json.dumps(report, indent=1))
    if args.report_json:
        json.dump(report, open(args.report_json, "w"), indent=1)


if __name__ == "__main__":
    main()
