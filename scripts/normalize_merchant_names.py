"""
Normalize merchant_name casing in ReceiptPlace records, using sibling
records as ground truth.

Two write paths populate `merchant_name` today with no normalization:

  1. Google Places API → "Sprouts Farmers Market" (title case)
  2. Apple Vision OCR / LLM fallback → "SPROUTS FARMERS MARKET" (all caps)

Both casings coexist in DynamoDB and show as separate merchants when the
spending rollup groups by raw `merchant_name`.

Strategy: group ReceiptPlace records by `place_id`. Within each group,
if any record has a mixed-case `merchant_name`, treat that as the
canonical casing and rewrite the all-caps records in the group to
match. This avoids dumb `str.title()` mistakes like
  - "CVS" → "Cvs"  (CVS is an acronym; should stay)
  - "TRADER JOE'S" → "Trader Joe'S"  (Python's title() breaks on apostrophes)

Records whose `place_id` group has *no* mixed-case sibling are left
alone and listed at the end of the dry-run for separate manual review.

The GSI1 key is unaffected: it's derived from `merchant_name.upper()`
so "SPROUTS FARMERS MARKET" and "Sprouts Farmers Market" produce the
same normalized key.

Usage:
    PORTFOLIO_ENV=dev  python scripts/normalize_merchant_names.py --dry-run
    PORTFOLIO_ENV=dev  python scripts/normalize_merchant_names.py --apply
    PORTFOLIO_ENV=prod python scripts/normalize_merchant_names.py --dry-run
    PORTFOLIO_ENV=prod python scripts/normalize_merchant_names.py --apply

After --apply: regenerate ReceiptSummaryRecord rows so the rollup tables
inherit the new casing.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _is_all_caps(name: str) -> bool:
    """True if every alphabetic character is uppercase. Skips '', digits, '#'."""
    name = (name or "").strip()
    if not name or not any(c.isalpha() for c in name):
        return False
    return name == name.upper()


def _build_canonical_map(
    places: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """
    Group places by place_id. For each group, if a mixed-case
    merchant_name exists alongside all-caps ones, return the mixed-case
    name as canonical for that place_id.

    Returns (canonical_by_place_id, orphan_all_caps_records).
    `orphan_all_caps_records` are records where the only casing observed
    for their place_id is all-caps — i.e., we have no evidence of the
    intended casing.
    """
    by_place: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in places:
        place_id = item.get("place_id") or ""
        by_place[place_id].append(item)

    canonical: dict[str, str] = {}
    orphans: list[dict[str, Any]] = []
    for place_id, group in by_place.items():
        names = [(r.get("merchant_name") or "").strip() for r in group]
        names = [n for n in names if n]
        if not names:
            continue
        # Pick a mixed-case name as canonical if any exists
        mixed = [n for n in names if not _is_all_caps(n)]
        if mixed:
            # Prefer the most common mixed-case spelling
            canonical[place_id] = Counter(mixed).most_common(1)[0][0]
        else:
            # All names for this place_id are all-caps — no evidence
            for r in group:
                if _is_all_caps(r.get("merchant_name") or ""):
                    orphans.append(r)
    return canonical, orphans


def _load_dynamo():
    """Return (DynamoClient, table_name) for the configured env."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets
    from receipt_dynamo.data.dynamo_client import DynamoClient

    env = os.environ.get("PORTFOLIO_ENV", "dev")
    logger.info("Loading %s Pulumi env...", env)
    config = load_env(env=env)
    for key, value in load_secrets(env=env).items():
        config[key.replace("portfolio:", "").lower().replace("-", "_")] = value
    table_name = config["dynamodb_table_name"]
    logger.info("DynamoDB table: %s", table_name)
    return DynamoClient(table_name=table_name), table_name


def _scan_receipt_places(table_name: str) -> list[dict[str, Any]]:
    """Page through every ReceiptPlace in the table via boto3 resource."""
    import boto3
    from boto3.dynamodb.conditions import Attr

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    places: list[dict[str, Any]] = []
    scan_kwargs = {
        "FilterExpression": Attr("TYPE").eq("RECEIPT_PLACE"),
    }
    while True:
        resp = table.scan(**scan_kwargs)
        places.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        logger.info("Scanned %d ReceiptPlace records so far...", len(places))
    logger.info("Total ReceiptPlace records: %d", len(places))
    return places


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List proposed changes without writing. Default (without --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update DynamoDB records. Required to make changes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most this many candidate records (0 = no limit)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        args.dry_run = True
        logger.info("Neither --dry-run nor --apply specified; defaulting to --dry-run")

    if args.apply and args.dry_run:
        logger.warning("Both --dry-run and --apply specified; --apply wins")
        args.dry_run = False

    env = os.environ.get("PORTFOLIO_ENV", "dev")
    logger.info("Running against %s. dry_run=%s", env, args.dry_run)

    _, table_name = _load_dynamo()
    import boto3

    boto3_table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)

    places = _scan_receipt_places(table_name)

    canonical, orphans = _build_canonical_map(places)

    candidates: list[tuple[str, str, str, str]] = []  # (pk, sk, old, new)
    pair_counts: Counter[tuple[str, str]] = Counter()
    rejected_diff_text: list[tuple[str, str]] = []
    for item in places:
        merchant = (item.get("merchant_name") or "").strip()
        if not merchant or not _is_all_caps(merchant):
            continue
        place_id = item.get("place_id") or ""
        new = canonical.get(place_id)
        if not new or new == merchant:
            continue
        # Safety gate: only apply when the only difference is casing.
        # Same place_id can carry different merchant_name values in
        # practice (data-quality artifacts), and we don't want to
        # silently rewrite "IMPERIAL PARKING" into a completely
        # different merchant just because it shares a place_id row.
        if merchant.upper() != new.upper():
            rejected_diff_text.append((merchant, new))
            continue
        pk = item.get("PK")
        sk = item.get("SK")
        pair_counts[(merchant, new)] += 1
        candidates.append((pk, sk, merchant, new))

    logger.info("Records to normalize via sibling evidence: %d", len(candidates))
    print()
    print(f"{'COUNT':>6} {'OLD':<45} → {'NEW':<45}")
    print("-" * 100)
    for (old_name, new_name), count in pair_counts.most_common(30):
        print(f"{count:>6} {old_name[:44]:<45} → {new_name[:44]:<45}")
    if len(pair_counts) > 30:
        print(f"... +{len(pair_counts) - 30} more rewrites")
    print()

    if orphans:
        orphan_counts: Counter[str] = Counter(
            (o.get("merchant_name") or "").strip() for o in orphans
        )
        print(
            f"All-caps records with NO mixed-case sibling (left alone): "
            f"{len(orphans)} records, {len(orphan_counts)} distinct names"
        )
        for name, count in orphan_counts.most_common(20):
            print(f"  {count:>4}  {name}")
        print()

    if rejected_diff_text:
        print(
            f"REJECTED: {len(rejected_diff_text)} candidates where sibling "
            "name differs by more than casing (likely data-quality issue, "
            "needs manual review):"
        )
        for old, new in Counter(rejected_diff_text).most_common(20):
            o, n = old
            print(f"  '{o}' ≠ '{n}' (place_id collision or unrelated merchants)")
        print()

    if args.limit and len(candidates) > args.limit:
        logger.info("Limiting to first %d candidates", args.limit)
        candidates = candidates[: args.limit]

    if args.dry_run:
        logger.info("Dry-run: would update %d records.", len(candidates))
        return 0

    logger.info("Applying %d updates...", len(candidates))
    updated = 0
    failed = 0
    for pk, sk, _old, new in candidates:
        try:
            boto3_table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="SET merchant_name = :n",
                ExpressionAttributeValues={":n": new},
                ConditionExpression="attribute_exists(PK)",
            )
            updated += 1
            if updated % 50 == 0:
                logger.info("  ... updated %d/%d", updated, len(candidates))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed PK=%s SK=%s: %s", pk, sk, exc)
            failed += 1

    logger.info("Done. updated=%d failed=%d", updated, failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
