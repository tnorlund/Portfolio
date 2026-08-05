#!/usr/bin/env python3
"""Backfill ``ReceiptSummary.item_count`` from real ReceiptLineItem rows.

``item_count`` was derived solely from VALID ``LINE_TOTAL`` word labels.
Receipts ingested through the current pipeline never receive those
labels -- their line items are ``ReceiptLineItem`` rows written by the
line-item updater's band-block decoder -- so freshly-ingested receipts
holding real line items reported ``item_count == 0``.

The shipped rule (``receipt_dynamo.entities.receipt_summary
.resolve_item_count``) prefers the row count when rows exist and keeps
the label count otherwise. This script applies that same rule to
summaries already in the table. It rewrites nothing else: the record is
re-put with only ``item_count`` changed, so the offline bank/tender
fields are carried through untouched.

Measured 2026-08-04 (before any write):

    prod ReceiptsTable-d7ff76a   826 summaries
        220 with item_count == 0, of which 121 hold line-item rows
        327 summaries where stored != row count
        302 would change under the fallback rule
         25 are protected BY the fallback (labels but no rows)

    dev  ReceiptsTable-dc5be22   828 summaries
        246 with item_count == 0, of which 118 hold line-item rows

Blast radius: ``item_count`` is not read by the Next.js frontend, by any
API route, or by the Swift worker. Its only substantive consumers are
the QA agent's evidence prose and the MCP ``get_receipt_summaries``
pass-through.

Note that every write emits a DynamoDB stream event that routes to the
LINE_ITEMS queue, so a full backfill also triggers one line-item
recompute per changed receipt. That is idempotent but not free; use
``--limit`` to stage it.

DRY-RUN BY DEFAULT: pass ``--apply`` to write.

Usage:
    python scripts/backfill_summary_item_count.py --env dev
    python scripts/backfill_summary_item_count.py --env dev --apply
    python scripts/backfill_summary_item_count.py \
        --table-name ReceiptsTable-d7ff76a --apply --limit 25
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_summary import resolve_item_count
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

logger = logging.getLogger("backfill_summary_item_count")

ENV_TABLES = {
    "dev": "ReceiptsTable-dc5be22",
    "prod": "ReceiptsTable-d7ff76a",
}


@dataclass
class Change:
    """One summary whose stored item_count disagrees with its rows."""

    image_id: str
    receipt_id: int
    merchant_name: str | None
    stored: int
    computed: int


def _iter_summaries(client: DynamoClient):
    """Yield every ReceiptSummaryRecord in the table, via GSI2."""
    last_key = None
    while True:
        records, last_key = client.list_receipt_summaries(
            last_evaluated_key=last_key
        )
        yield from records
        if last_key is None:
            return


def plan(client: DynamoClient, limit: int | None) -> list[Change]:
    """Compute the pending changes without writing anything."""
    changes: list[Change] = []
    scanned = 0
    for record in _iter_summaries(client):
        scanned += 1
        rows = client.get_receipt_line_items_from_receipt(
            record.image_id, record.receipt_id
        )
        computed = resolve_item_count(
            label_item_count=record.item_count,
            line_item_count=len(rows),
        )
        if computed != record.item_count:
            changes.append(
                Change(
                    image_id=record.image_id,
                    receipt_id=record.receipt_id,
                    merchant_name=record.merchant_name,
                    stored=record.item_count,
                    computed=computed,
                )
            )
            if limit is not None and len(changes) >= limit:
                break
    logger.info("Scanned %d summaries", scanned)
    return changes


def apply(client: DynamoClient, changes: list[Change]) -> int:
    """Re-put each changed summary with the corrected item_count."""
    written = 0
    for change in changes:
        record = client.get_receipt_summary(change.image_id, change.receipt_id)
        summary = record.to_summary()
        summary.item_count = change.computed
        client.upsert_receipt_summary(
            ReceiptSummaryRecord.from_summary(summary)
        )
        written += 1
        if written % 50 == 0:
            logger.info("Wrote %d/%d", written, len(changes))
    return written


def _report(changes: list[Change]) -> None:
    ups = sum(1 for c in changes if c.computed > c.stored)
    downs = len(changes) - ups
    print(f"\n{len(changes)} summaries would change ({ups} up, {downs} down)")
    hist = Counter(c.computed - c.stored for c in changes)
    print(f"delta histogram: {dict(sorted(hist.items()))}\n")
    for change in changes[:40]:
        print(
            f"  {change.image_id} #{change.receipt_id:05d}  "
            f"{change.stored:>3} -> {change.computed:<3}  "
            f"{change.merchant_name!r}"
        )
    if len(changes) > 40:
        print(f"  ... and {len(changes) - 40} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=sorted(ENV_TABLES))
    parser.add_argument(
        "--table-name",
        help="Explicit table name; overrides --env.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the script only reports.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Stop after this many pending changes (staging aid).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    table = args.table_name or ENV_TABLES.get(args.env or "")
    if not table:
        parser.error("pass --env dev|prod or --table-name NAME")

    print(f"Table: {table}   mode: {'APPLY' if args.apply else 'dry-run'}")
    client = DynamoClient(table)
    changes = plan(client, args.limit)
    _report(changes)

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    written = apply(client, changes)
    print(f"\nWrote {written} summaries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
