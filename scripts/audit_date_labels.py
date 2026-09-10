#!/usr/bin/env python3
"""Audit DATE-label coverage: which receipts have no usable DATE label?

The summary's ``date`` is derived from VALID DATE word labels only, so a
receipt with no DATE label, or with DATE labels that are all INVALID /
PENDING / NEEDS_REVIEW / NONE, has no printed date as far as every
downstream consumer is concerned. For each such receipt this script
joins the OCR lines and reports whether any date-like text is present,
so the output splits into "labeling gap" (date printed, label missing)
and "no legible date" (candidates for the bank-date fallback,
``ReceiptSummary.effective_date``).

Usage:
    python scripts/audit_date_labels.py ReceiptsTable-dc5be22 out.json

Read-only. The 2026-09-10 run on dev (917 receipts) found 11 with no
DATE label and 7 with only INVALID ones; 12 of those 18 turned out to be
labeling gaps and were fixed by hand.
"""

from __future__ import annotations

import argparse
import collections
import json
import re

from receipt_dynamo import DynamoClient

DATE_RE = re.compile(
    r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
    r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
    r"\s+\d{1,2}(,?\s*\d{2,4})?"
    r"|\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?\s*\d{2,4}?)\b",
    re.I,
)


def _paginate(method, **kwargs):
    last_key = None
    while True:
        page, last_key = method(
            limit=500, last_evaluated_key=last_key, **kwargs
        )
        yield from page
        if not last_key:
            break


def receipt_lines(client: DynamoClient, image_id: str, receipt_id: int):
    words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    by_line: dict[int, list] = collections.defaultdict(list)
    for word in words:
        by_line[word.line_id].append(word)
    return [
        " ".join(w.text for w in sorted(by_line[k], key=lambda w: w.word_id))
        for k in sorted(by_line)
    ]


def merchant_name(client: DynamoClient, image_id: str, receipt_id: int):
    try:
        return client.get_receipt_place(image_id, receipt_id).merchant_name
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def run(table: str, out: str) -> dict:
    client = DynamoClient(table)

    receipts = {
        (r.image_id, r.receipt_id): r for r in _paginate(client.list_receipts)
    }

    # DATE labels grouped by receipt, any validation status. Labels whose
    # receipt is gone (orphans left behind by a merge or delete) are
    # counted, not joined -- they must not crash the audit.
    statuses_by_receipt: dict[tuple, list[str]] = collections.defaultdict(list)
    orphan_labels = 0
    for label in _paginate(
        client.get_receipt_word_labels_by_label, label="DATE"
    ):
        key = (label.image_id, label.receipt_id)
        if key not in receipts:
            orphan_labels += 1
            continue
        statuses_by_receipt[key].append(str(label.validation_status))

    rows = []
    for key, receipt in sorted(receipts.items()):
        statuses = statuses_by_receipt.get(key, [])
        if any(s == "VALID" for s in statuses):
            continue
        kind = "no_date_label" if not statuses else "no_valid_date_label"
        lines = receipt_lines(client, *key)
        hits = [ln for ln in lines if DATE_RE.search(ln)]
        rows.append(
            {
                "image_id": key[0],
                "receipt_id": key[1],
                "kind": kind,
                "label_statuses": dict(collections.Counter(statuses)),
                "merchant": merchant_name(client, *key),
                "n_lines": len(lines),
                "regex_date_hits": hits,
                "cdn_s3_key": getattr(receipt, "cdn_s3_key", None),
                "lines": lines,
            }
        )

    summary = {
        "table": table,
        "total_receipts": len(receipts),
        "receipts_with_valid_date_label": len(receipts) - len(rows),
        "receipts_without_valid_date_label": len(rows),
        "no_date_label": sum(r["kind"] == "no_date_label" for r in rows),
        "no_valid_date_label": sum(
            r["kind"] == "no_valid_date_label" for r in rows
        ),
        "with_regex_date_hit": sum(bool(r["regex_date_hits"]) for r in rows),
        "without_regex_date_hit": sum(not r["regex_date_hits"] for r in rows),
        "orphan_date_labels": orphan_labels,
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, indent=1)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("table", help="DynamoDB table name")
    parser.add_argument("out", help="JSON report path")
    args = parser.parse_args()
    print(json.dumps(run(args.table, args.out), indent=1))


if __name__ == "__main__":
    main()
