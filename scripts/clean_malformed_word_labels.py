#!/usr/bin/env python3
"""Clean malformed ReceiptWordLabel rows (free-text / bare-number labels).

The 2026-01 label-evaluator LLM era wrote its *commentary* into the label
field, so strings like ``SUBTOTAL SHOULD BE 50.63 (OR THE DISCOUNT
AMOUNTS NEED SIGN CORRECTION)`` and bare amounts like ``18.97`` became
real DynamoDB sort keys (#1380 closed the writer; this cleans the
residue). Scope is exactly the three malformed classes:

  A. core-label prefix + free text  ("SUBTOTAL SHOULD BE ...")
  B. bare amount/number             ("18.97", "$141.55")
  D. other free-text                ("$0.00 (OR REMOVE THE ...)")

Single-token identifier labels (AUTH_CODE, GREETING, WEIGHT, ...) are
the PARKED label-vocab cleanup, not malformed rows: untouched here.

For every in-scope row, in order:

1. EXPORT the full item to a local jsonl backup (always, first).
2. SALVAGE where a (core label, value) pair is recoverable -- the label
   token from the malformed string itself, else from its reasoning; the
   value from the first money-shaped number in the string:
     * canonical row for that word+label exists -> absorb a compact note
       into its reasoning;
     * no canonical row -> create one carrying the malformed row's
       validation_status and a "[salvaged from ...]" reasoning prefix,
       via the CONDITIONAL singular add (never the batch clobber path).
       Creation happens on DEV only (labels derive in dev and sync via
       scripts/copy_missing_receipt_word_labels.py); on prod the salvage
       is note-absorption into existing rows only.
3. DELETE the malformed row.

DRY-RUN BY DEFAULT: pass ``--apply`` to write.

Usage:
    python scripts/clean_malformed_word_labels.py --table ReceiptsTable-dc5be22
        [--apply] [--allow-create]
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import boto3

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _pkg))

from receipt_dynamo.constants import CORE_LABELS, NON_CORE_LABEL_ALIASES
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

BACKUP_DIR = Path.home() / "portfolio-backups" / "2026-08-07"

# Bare amount / number (optionally $-signed / %-suffixed); also the odd
# "82.00 -> 62.00" arrow form.
NUM_RE = re.compile(r"^\$?-?\d[\d.,]*\d?%?$|^\$?\d[\d.,]*\s*(?:→|->)")
# Single identifier-shaped token: the parked vocab cleanup, NOT ours.
IDENT_RE = re.compile(r"^[A-Z][A-Z_0-9]*$")
LEAD_TOKEN_RE = re.compile(r"^([A-Z_]+)")
MONEY_IN_TEXT_RE = re.compile(r"\$?(-?\d[\d,]*\.\d{2})")
CORE_OR_ALIAS = set(CORE_LABELS) | set(NON_CORE_LABEL_ALIASES)
# Any core-label token appearing inside free text / reasoning.
CORE_TOKEN_RE = re.compile(
    r"\b(" + "|".join(sorted(CORE_LABELS, key=len, reverse=True)) + r")\b"
)


def canonical(token: str) -> str | None:
    if token in CORE_LABELS:
        return token
    return NON_CORE_LABEL_ALIASES.get(token)


def in_scope(label: str) -> bool:
    """True for malformed classes A/B/D; False for core, alias, vocab."""
    if label in CORE_OR_ALIAS:
        return False
    if NUM_RE.match(label):
        return True  # class B
    if IDENT_RE.match(label):
        return False  # vocab-ish single token: parked, not malformed
    return True  # classes A and D (free text)


def recover_pair(label: str, reasoning: str) -> tuple[str | None, str | None]:
    """(core label, value) recoverable from the malformed row, or Nones."""
    target = None
    lead = LEAD_TOKEN_RE.match(label)
    if lead:
        target = canonical(lead.group(1))
    if target is None:
        hit = CORE_TOKEN_RE.search(label)
        if hit:
            target = hit.group(1)
    if target is None and reasoning:
        hit = CORE_TOKEN_RE.search(reasoning)
        if hit:
            target = hit.group(1)
    value = None
    money = MONEY_IN_TEXT_RE.search(label)
    if money:
        value = money.group(1)
    return target, value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--allow-create",
        action="store_true",
        help="Permit creating canonical rows for salvaged pairs (DEV "
        "only -- labels derive in dev and sync to prod via the copy "
        "script).",
    )
    args = ap.parse_args()

    raw = boto3.client("dynamodb", region_name="us-east-1")
    client = DynamoClient(table_name=args.table)

    # ------------------------------------------------------------ scan
    rows = []
    kwargs = dict(
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_WORD_LABEL"}},
    )
    while True:
        resp = raw.query(**kwargs)
        for item in resp["Items"]:
            sk = item["SK"]["S"]
            if "#LABEL#" not in sk:
                continue
            if in_scope(sk.split("#LABEL#")[-1]):
                rows.append(item)
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    logger.info("%s: %d malformed rows in scope", args.table, len(rows))

    # ---------------------------------------------------------- export
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    export_path = (
        BACKUP_DIR / f"malformed_word_labels_{args.table}_{stamp}.jsonl.gz"
    )
    with gzip.open(export_path, "wt") as f:
        for item in rows:
            f.write(json.dumps(item, default=str) + "\n")
    logger.info("exported %d rows to %s", len(rows), export_path)

    # --------------------------------------------------------- process
    stats: Counter = Counter()
    for item in rows:
        pk, sk = item["PK"]["S"], item["SK"]["S"]
        label = sk.split("#LABEL#")[-1]
        reasoning = item.get("reasoning", {}).get("S", "") or ""
        target, value = recover_pair(label, reasoning)

        entity = item_to_receipt_word_label(item)
        if target is None:
            stats["unrecoverable_deleted"] += 1
            if args.apply:
                client.delete_receipt_word_label(entity)
            continue

        canon_sk = sk.rsplit("#LABEL#", 1)[0] + f"#LABEL#{target}"
        existing = raw.get_item(
            TableName=args.table,
            Key={"PK": {"S": pk}, "SK": {"S": canon_sk}},
        ).get("Item")
        note = (
            f"[2026-08-07 malformed-label cleanup: absorbed row "
            f"'{label[:80]}'"
            + (f", value {value}" if value else "")
            + f", status {entity.validation_status}]"
        )
        if existing is not None:
            stats["salvaged_into_existing"] += 1
            if args.apply:
                canon = item_to_receipt_word_label(existing)
                canon.reasoning = ((canon.reasoning or "") + " " + note)[:2000]
                client.update_receipt_word_label(canon)
                client.delete_receipt_word_label(entity)
        elif args.allow_create:
            stats["salvaged_into_new_row"] += 1
            if args.apply:
                entity_new = item_to_receipt_word_label(item)
                entity_new.label = target
                entity_new.reasoning = (
                    f"[salvaged from malformed label '{label[:80]}'] "
                    + reasoning
                )[:2000]
                try:
                    client.add_receipt_word_label(entity_new)
                except Exception as exc:  # noqa: BLE001 - keep sweeping
                    stats["create_failed"] += 1
                    logger.warning("create failed %s: %s", canon_sk, exc)
                client.delete_receipt_word_label(entity)
        else:
            stats["salvage_needs_create_skipped"] += 1
            continue  # row left in place: no canonical target row here

    print("=" * 64)
    print(
        f"MALFORMED LABEL CLEANUP {args.table} "
        f"({'APPLY' if args.apply else 'DRY RUN'})"
    )
    print("=" * 64)
    print(f"in scope:   {len(rows)}")
    for key, count in stats.most_common():
        print(f"  {key:28s} {count:5d}")
    print(f"export: {export_path}")
    if not args.apply:
        print("\nDRY RUN -- nothing written.")


if __name__ == "__main__":
    main()
