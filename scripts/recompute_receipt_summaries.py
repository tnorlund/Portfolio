"""Recompute stored ReceiptSummary rows through the production code path.

Walks every RECEIPT_SUMMARY row in a table, recomputes each one with the
summary updater Lambda's own ``compute_receipt_summary`` (the function
``update_receipt_summary`` calls before it writes) and prints the stored
value next to the recomputed value per receipt. Nothing is written unless
``--apply`` is given; writes go through ``update_receipt_summary`` so the
tombstone guard, the offline bank-field carry-over and the upsert are the
Lambda's, not a copy. The prod table is refused before anything is read.

Typical use after a parser fix::

    python scripts/recompute_receipt_summaries.py \\
        --table ReceiptsTable-dc5be22 --only-missing-date
    python scripts/recompute_receipt_summaries.py \\
        --table ReceiptsTable-dc5be22 --only-missing-date --apply

``--receipt <image_id>:<receipt_id>`` (repeatable) restricts the run to
those receipts. When a stored date would drop to None, the receipt's
DATE labels are printed with their validation status.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# The Lambda module builds (and describes) a table client at import time
# from this variable. The script only ever uses the client it builds from
# --table after the prod check, so an inherited environment value must
# not open a connection before that check runs.
os.environ.pop("DYNAMODB_TABLE_NAME", None)

# isort: off
# The receipt_agent CI leg lints changed files with an environment that
# classifies the local packages differently from repository-tests; fence
# the local-package block so both legs accept one ordering.
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

from infra.receipt_summary_updater import summary_processor

# isort: on

PROD_TABLE_FRAGMENTS = ("d7ff76a",)


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "None"


def _fmt_amount(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "None"


def _describe(
    date: datetime | None,
    grand_total: float | None,
    item_count: int,
) -> str:
    return (
        f"date={_fmt_date(date)} total={_fmt_amount(grand_total)} "
        f"items={item_count}"
    )


def iter_summaries(client: DynamoClient) -> list[ReceiptSummaryRecord]:
    """Every stored summary record, following pagination."""
    records: list[ReceiptSummaryRecord] = []
    last_key: dict | None = None
    while True:
        page, last_key = client.list_receipt_summaries(
            last_evaluated_key=last_key
        )
        records.extend(page)
        if last_key is None:
            return records


def _fields_without_item_count(summary: ReceiptSummary) -> dict[str, Any]:
    fields = summary.to_dict()
    fields.pop("item_count", None)
    return fields


def parse_receipt_key(value: str) -> tuple[str, int]:
    """``<image_id>:<receipt_id>`` -> (image_id, receipt_id)."""
    image_id, sep, receipt_id = value.rpartition(":")
    if not sep or not image_id or not receipt_id.isdigit():
        raise argparse.ArgumentTypeError(
            f"expected <image_id>:<receipt_id>, got {value!r}"
        )
    return image_id, int(receipt_id)


def _date_label_statuses(
    client: DynamoClient, image_id: str, receipt_id: int
) -> str:
    """Every DATE label on the receipt with its text and validation status.

    Printed when a stored date would drop to None so the reason (labels
    rejected by the evaluator, or text the parser refuses) is visible.
    """
    text_by_key = {
        (w.line_id, w.word_id): w.text
        for w in client.list_receipt_words_from_receipt(image_id, receipt_id)
    }
    entries: list[str] = []
    last_key = None
    while True:
        labels, last_key = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id, last_evaluated_key=last_key
        )
        for label in labels:
            if label.label != "DATE":
                continue
            text = text_by_key.get((label.line_id, label.word_id), "")
            entries.append(
                f"{label.line_id}:{label.word_id} {text!r} "
                f"{label.validation_status}"
            )
        if last_key is None:
            break
    return "; ".join(sorted(entries)) if entries else "none"


def recompute(
    client: DynamoClient,
    *,
    only_missing_date: bool,
    apply: bool,
    receipts: set[tuple[str, int]] | None = None,
    out: TextIO | None = None,
) -> Counter:
    """Print before/after per receipt; write only when ``apply`` is True.

    ``item_count`` is owned by the line-item worker: a summary write bumps
    ``timestamp_computed``, which routes the receipt to the line-item
    updater, which rewrites the RECEIPT_LINE_ITEM rows, finalizes
    ``item_count`` from its own rows and then hands the receipt to the
    Swift refine pass, which rewrites the rows once more without
    touching ``item_count``. Recomputing from the refined rows and
    writing would restart that cycle and flip the count on every run, so
    a summary whose only difference is ``item_count`` is reported as
    drift and never written here.
    """
    out = out if out is not None else sys.stdout
    counts: Counter = Counter()
    unseen = set(receipts) if receipts else set()
    for stored in sorted(
        iter_summaries(client), key=lambda r: (r.image_id, r.receipt_id)
    ):
        if receipts is not None:
            if (stored.image_id, stored.receipt_id) not in receipts:
                counts["skipped_not_selected"] += 1
                continue
            unseen.discard((stored.image_id, stored.receipt_id))
        if only_missing_date and stored.date is not None:
            counts["skipped_has_date"] += 1
            continue
        counts["examined"] += 1
        key = f"{stored.image_id}#{stored.receipt_id}"
        before = _describe(stored.date, stored.grand_total, stored.item_count)

        try:
            client.get_receipt(stored.image_id, stored.receipt_id)
        except EntityNotFoundError:
            counts["skipped_parent_deleted"] += 1
            print(f"{key} SKIPPED (parent receipt deleted)", file=out)
            continue
        computed, _category = summary_processor.compute_receipt_summary(
            stored.image_id, stored.receipt_id, client
        )
        after = _describe(
            computed.date, computed.grand_total, computed.item_count
        )
        if stored.date is not None and computed.date is None:
            after += " DATE labels: " + _date_label_statuses(
                client, stored.image_id, stored.receipt_id
            )

        if after == before:
            counts["unchanged"] += 1
            print(f"{key} unchanged: {before}", file=out)
            continue
        if _fields_without_item_count(computed) == _fields_without_item_count(
            stored.summary
        ):
            counts["item_count_drift"] += 1
            print(
                f"{key} ITEM_COUNT DRIFT (not written; finalized by the "
                f"line-item worker): {before} -> {after}",
                file=out,
            )
            continue

        counts["changed"] += 1
        if stored.date is None and computed.date is not None:
            counts["date_filled"] += 1
        tag = "WOULD UPDATE"
        if apply:
            result: dict[str, Any] = summary_processor.update_receipt_summary(
                stored.image_id, stored.receipt_id, client
            )
            if result.get("skipped"):
                counts["changed"] -= 1
                counts["skipped_parent_deleted"] += 1
                print(f"{key} SKIPPED ({result['skipped']})", file=out)
                continue
            tag = "UPDATED"
        print(f"{key} {tag}: {before} -> {after}", file=out)
    for image_id, receipt_id in sorted(unseen):
        counts["not_found"] += 1
        print(
            f"{image_id}#{receipt_id} NOT FOUND (no stored summary)", file=out
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table", required=True, help="dev table name to recompute"
    )
    parser.add_argument(
        "--only-missing-date",
        action="store_true",
        help="only receipts whose stored summary has no date",
    )
    parser.add_argument(
        "--receipt",
        action="append",
        type=parse_receipt_key,
        metavar="IMAGE_ID:RECEIPT_ID",
        help="examine only this receipt (repeatable)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write (default: dry run)"
    )
    args = parser.parse_args(argv)
    if any(fragment in args.table for fragment in PROD_TABLE_FRAGMENTS):
        parser.error("refusing to recompute the prod table")

    client = DynamoClient(args.table)
    counts = recompute(
        client,
        only_missing_date=args.only_missing_date,
        apply=args.apply,
        receipts=set(args.receipt) if args.receipt else None,
    )
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"{mode}: {counts['examined']} receipts examined")
    for key, value in sorted(counts.items()):
        print(f"  {key:24s} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
