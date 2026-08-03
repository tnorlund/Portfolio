#!/usr/bin/env python3
"""Harvest SMART re-OCR outcomes into the strategy-ladder asset.

Scans completed REGIONAL_REOCR OCRJobs (which carry reocr_strategy /
reocr_mechanism from the trigger and reocr_words_accepted /
reocr_words_rejected / reocr_delta_before / reocr_delta_after from the
overlay) and aggregates them per mechanism x strategy: attempts,
word-level acceptance rate, and mean |delta| improvement. The result
is written to receipt_upload/receipt_upload/line_items/assets/
reocr_ladder.json -- a committed asset (same pattern as the block-role
priors) that choose_strategy() uses to override the hand-written
default ladder ordering by measured success.

Usage:
    python scripts/harvest_reocr_outcomes.py --table ReceiptsTable-dc5be22
    python scripts/harvest_reocr_outcomes.py --table ... --dry-run

The aggregation itself is pure and lives in
receipt_upload.line_items.reocr_strategy.build_ledger so it is unit
tested next to the ladder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from receipt_dynamo import DynamoClient

from receipt_upload.line_items.reocr_strategy import (
    LEDGER_ASSET,
    build_ledger,
)


def fetch_ocr_jobs(client: DynamoClient) -> list:
    """Page through every OCR job in the table."""
    jobs: list = []
    last_evaluated_key = None
    while True:
        page, last_evaluated_key = client.list_ocr_jobs(
            limit=500, last_evaluated_key=last_evaluated_key
        )
        jobs.extend(page)
        if not last_evaluated_key:
            break
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", ""),
        help="DynamoDB table name (default: $DYNAMODB_TABLE_NAME)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=LEDGER_ASSET,
        help=f"Ledger JSON path (default: {LEDGER_ASSET})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ledger instead of writing the asset",
    )
    args = parser.parse_args(argv)

    if not args.table:
        parser.error("--table (or $DYNAMODB_TABLE_NAME) is required")

    client = DynamoClient(args.table)
    jobs = fetch_ocr_jobs(client)
    mechanisms = build_ledger(jobs)

    payload = {
        "schema": "reocr-ladder-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"scripts/harvest_reocr_outcomes.py --table {args.table}",
        "mechanisms": mechanisms,
    }
    rendered = json.dumps(payload, indent=2) + "\n"

    total = sum(
        entry["attempts"]
        for per_strategy in mechanisms.values()
        for entry in per_strategy.values()
    )
    print(
        f"Harvested {total} completed re-OCR attempts across "
        f"{len(mechanisms)} mechanism(s) from {len(jobs)} OCR jobs."
    )
    if args.dry_run:
        print(rendered)
        return 0

    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
