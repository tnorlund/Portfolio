#!/usr/bin/env python3
"""Backfill ReceiptRow entities and row-granularity ReceiptSections.

One-shot migration for the font-intelligence row-anchoring amendment
(tools/glyph-studio/ROW_SCHEMA.md):

1. For every receipt, group its lines into visual rows with the existing
   ``group_lines_into_visual_rows`` (the same grouping the embedding
   pipeline uses) and write one ``ReceiptRow`` per visual row, keyed by the
   row's primary (leftmost) line id.
2. For every ReceiptSection, compute ``row_ids``: a section *claims* a row
   when it contains any of the row's line_ids. Straddle rows (rows whose
   lines are split across sections — measured ~3% of sectioned rows) are
   resolved by majority-of-lines; ties between the leading sections go to
   the leader owning the row's primary line, with a deterministic
   alphabetical fallback when the primary line's section is not among the
   leaders. Every straddle resolution is logged (JSONL).
3. Update each section with ``row_ids`` plus reconciled ``line_ids`` (the
   union of its claimed rows' line_ids), and verify
   ``validate_section_row_coverage`` on every updated section. Sections
   whose every row is won by a competitor ("emptied") are DELETED — leaving
   them would keep their stale line_ids authoritative and double-assign the
   straddle lines. Deletions are logged like straddle resolutions.

Safety:
  - Local-first. The DynamoDB endpoint comes from ``--endpoint-url`` or
    ``DYNAMODB_ENDPOINT_URL`` / ``AWS_ENDPOINT_URL_DYNAMODB``; a non-local
    (or absent) endpoint is refused unless ``--allow-remote`` is passed.
  - Dry-run by default: enumerates, groups, resolves and reports, but
    writes nothing without ``--apply``.
  - Idempotent + resumable: rows are batch-put (overwrite-safe) and each
    completed receipt drops a per-receipt cache marker in ``--cache-dir``;
    re-runs skip cached receipts unless ``--force``.

Usage (local sandbox):
  python scripts/backfill_receipt_rows.py \
      --endpoint-url http://127.0.0.1:8100 --table ReceiptsTable-dc5be22 \
      --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Prefer the sibling checkouts of the monorepo packages over any installed
# copies so the backfill always runs the branch's code.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo", "receipt_chroma"):
    _path = _REPO_ROOT / _pkg
    if _path.is_dir():
        sys.path.insert(0, str(_path))

GROUPING_VERSION = "visual-rows-v1"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _is_local_endpoint(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    return urlparse(endpoint).hostname in LOCAL_HOSTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill ReceiptRow entities + section row_ids"
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME"),
        help="DynamoDB table name (or DYNAMODB_TABLE_NAME env)",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("DYNAMODB_ENDPOINT_URL")
        or os.environ.get("AWS_ENDPOINT_URL_DYNAMODB"),
        help=(
            "DynamoDB endpoint (or DYNAMODB_ENDPOINT_URL / "
            "AWS_ENDPOINT_URL_DYNAMODB env). Non-local endpoints are "
            "refused without --allow-remote."
        ),
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit running against a non-local (real AWS) endpoint",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write ReceiptRow entities and section updates (default: "
        "dry-run, report only)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Per-receipt resume cache (default: .row_backfill_cache/"
        "<table> under the repo root)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess receipts that already have a cache marker",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Process at most N receipts"
    )
    parser.add_argument("--image-id", default=None, help="Only process this image_id")
    parser.add_argument(
        "--report-json",
        default=None,
        help="Write the final summary as JSON to this path",
    )
    return parser.parse_args()


def build_receipt_rows(client: Any, image_id: str, receipt_id: int):
    """Group a receipt's lines into visual rows -> ReceiptRow entities.

    Returns (rows_entities, grouped_rows) where grouped_rows is the raw
    list-of-lists of ReceiptLine from group_lines_into_visual_rows (top to
    bottom, each row left to right).
    """
    from receipt_chroma.embedding.formatting import (
        get_primary_line_id,
        group_lines_into_visual_rows,
    )
    from receipt_dynamo.entities.receipt_row import ReceiptRow

    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    grouped = group_lines_into_visual_rows(lines)
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)

    entities = []
    for row in grouped:
        line_ids = [ln.line_id for ln in row]
        y_min = min(ln.bounding_box.get("y", 0.0) for ln in row)
        y_max = max(
            ln.bounding_box.get("y", 0.0) + ln.bounding_box.get("height", 0.0)
            for ln in row
        )
        x_min = min(ln.bounding_box.get("x", 0.0) for ln in row)
        x_max = max(
            ln.bounding_box.get("x", 0.0) + ln.bounding_box.get("width", 0.0)
            for ln in row
        )
        entities.append(
            ReceiptRow(
                receipt_id=receipt_id,
                image_id=image_id,
                row_id=get_primary_line_id(row),
                line_ids=line_ids,
                grouping_version=GROUPING_VERSION,
                y_min=y_min,
                y_max=y_max,
                x_min=x_min,
                x_max=x_max,
                created_at=now,
            )
        )
    return entities, grouped


def resolve_row_owner(
    row_entity: Any,
    sections: list[Any],
) -> tuple[Any | None, str, dict[str, int]]:
    """Decide which section owns a visual row.

    Returns (winning_section_or_None, method, votes) where method is one of
    "whole" (no straddle), "majority", "primary-tie", "tie-fallback-alpha",
    or "unsectioned".
    """
    row_line_set = set(row_entity.line_ids)
    votes: dict[str, int] = {}
    candidates: dict[str, Any] = {}
    for section in sections:
        n = len(row_line_set & set(section.line_ids))
        if n:
            votes[section.section_type] = n
            candidates[section.section_type] = section

    if not votes:
        return None, "unsectioned", votes
    if len(votes) == 1:
        (only_type,) = votes
        return candidates[only_type], "whole", votes

    # Straddle: majority of lines wins.
    ranked = Counter(votes).most_common()
    top_count = ranked[0][1]
    leaders = [t for t, n in ranked if n == top_count]
    if len(leaders) == 1:
        return candidates[leaders[0]], "majority", votes

    # Tie: the section owning the row's primary (leftmost) line wins.
    primary = row_entity.row_id
    for leader in leaders:
        if primary in candidates[leader].line_ids:
            return candidates[leader], "primary-tie", votes

    # Primary line is in a non-leading section (or unsectioned): fall back
    # to a deterministic choice among the tied leaders.
    leader = sorted(leaders)[0]
    return candidates[leader], "tie-fallback-alpha", votes


def process_receipt(
    client: Any,
    image_id: str,
    receipt_id: int,
    apply: bool,
    straddle_log,
) -> dict[str, Any]:
    """Backfill one receipt. Returns per-receipt stats (cache payload)."""
    from receipt_dynamo.entities.receipt_section import (
        validate_section_row_coverage,
    )

    row_entities, grouped = build_receipt_rows(client, image_id, receipt_id)
    row_text = {
        entity.row_id: " ".join(ln.text for ln in row)
        for entity, row in zip(row_entities, grouped)
    }
    sections = client.get_receipt_sections_from_receipt(image_id, receipt_id)

    stats: dict[str, Any] = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "lines": sum(len(r.line_ids) for r in row_entities),
        "rows": len(row_entities),
        "sections": len(sections),
        "sectioned_rows": 0,
        "straddle_rows": 0,
        "straddle_methods": {},
        "sections_updated": 0,
        "sections_line_ids_changed": 0,
        "sections_emptied": 0,
        "sections_row_aligned_before": 0,
        "stale_rows_deleted": 0,
    }

    # --- before-stat: is each section already a union of whole rows? ------
    lines_by_row = {r.row_id: set(r.line_ids) for r in row_entities}
    for section in sections:
        section_lines = set(section.line_ids)
        covered = set()
        for row_lines in lines_by_row.values():
            if row_lines & section_lines:
                covered |= row_lines
        if covered == section_lines:
            stats["sections_row_aligned_before"] += 1

    # --- claim rows -------------------------------------------------------
    claims: dict[str, list[int]] = {s.section_type: [] for s in sections}
    for entity in row_entities:
        winner, method, votes = resolve_row_owner(entity, sections)
        if winner is None:
            continue
        stats["sectioned_rows"] += 1
        claims[winner.section_type].append(entity.row_id)
        if method != "whole":
            stats["straddle_rows"] += 1
            stats["straddle_methods"][method] = (
                stats["straddle_methods"].get(method, 0) + 1
            )
            straddle_log.write(
                json.dumps(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "row_id": entity.row_id,
                        "row_text": row_text[entity.row_id],
                        "votes": votes,
                        "winner": winner.section_type,
                        "method": method,
                    }
                )
                + "\n"
            )

    # --- reconcile sections ------------------------------------------------
    updated_sections = []
    emptied_sections = []
    for section in sections:
        row_ids = claims[section.section_type]
        if not row_ids:
            # Every row this section touched is majority-owned by another
            # section. Leaving it would keep its stale line_ids
            # authoritative (double-assigning those lines), so delete it.
            stats["sections_emptied"] += 1
            emptied_sections.append(section)
            straddle_log.write(
                json.dumps(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "section_type": section.section_type,
                        "line_ids": section.line_ids,
                        "method": "section-emptied-deleted",
                    }
                )
                + "\n"
            )
            continue
        new_line_ids = sorted(lid for rid in row_ids for lid in lines_by_row[rid])
        if new_line_ids != sorted(set(section.line_ids)):
            stats["sections_line_ids_changed"] += 1
        section.row_ids = row_ids
        section.line_ids = new_line_ids
        validate_section_row_coverage(section, row_entities)
        updated_sections.append(section)
    stats["sections_updated"] = len(updated_sections)

    if apply:
        # Prune rows from a previous run/grouping whose primary line id is
        # no longer a row key, so re-runs never leave a mixed generation.
        current_ids = {r.row_id for r in row_entities}
        stale = [
            r
            for r in client.get_receipt_rows_from_receipt(image_id, receipt_id)
            if r.row_id not in current_ids
        ]
        if stale:
            client.delete_receipt_rows(stale)
            stats["stale_rows_deleted"] = len(stale)
        if row_entities:
            client.add_receipt_rows(row_entities)  # batch put: idempotent
        if updated_sections:
            client.update_receipt_sections(updated_sections)
        if emptied_sections:
            client.delete_receipt_sections(emptied_sections)

    return stats


def main() -> int:
    args = parse_args()

    if not args.table:
        print("error: --table (or DYNAMODB_TABLE_NAME) is required")
        return 2

    if _is_local_endpoint(args.endpoint_url):
        os.environ["AWS_ENDPOINT_URL_DYNAMODB"] = args.endpoint_url
        # DynamoDB Local (non-sharedDb) namespaces tables by access key +
        # region; the sandbox is hydrated under the "local" key.
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    elif not args.allow_remote:
        print(
            "error: endpoint "
            f"{args.endpoint_url or '<default AWS>'} is not local. "
            "Refusing to run against a remote table without --allow-remote."
        )
        return 2
    elif args.endpoint_url:
        os.environ["AWS_ENDPOINT_URL_DYNAMODB"] = args.endpoint_url

    from receipt_dynamo import DynamoClient

    client = DynamoClient(args.table)

    cache_dir = Path(args.cache_dir or _REPO_ROOT / ".row_backfill_cache" / args.table)
    cache_dir.mkdir(parents=True, exist_ok=True)
    straddle_path = cache_dir / "straddle_resolutions.jsonl"

    # --- enumerate receipts -------------------------------------------------
    receipts = []
    last_key = None
    while True:
        page, last_key = client.list_receipts(limit=1000, last_evaluated_key=last_key)
        receipts.extend(page)
        if last_key is None:
            break
    receipts.sort(key=lambda r: (r.image_id, r.receipt_id))
    if args.image_id:
        receipts = [r for r in receipts if r.image_id == args.image_id]
    if args.limit is not None:
        receipts = receipts[: args.limit]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] {len(receipts)} receipts on table {args.table} "
        f"(endpoint={args.endpoint_url or 'default AWS'})"
    )

    totals: Counter = Counter()
    straddle_methods: Counter = Counter()
    processed = skipped = failed = 0

    with open(straddle_path, "a", encoding="utf-8") as straddle_log:
        for i, receipt in enumerate(receipts, 1):
            marker = cache_dir / f"{receipt.image_id}_{receipt.receipt_id:05d}.json"
            if marker.exists() and not args.force:
                skipped += 1
                continue
            try:
                stats = process_receipt(
                    client,
                    receipt.image_id,
                    receipt.receipt_id,
                    args.apply,
                    straddle_log,
                )
            except Exception as exc:  # noqa: BLE001 - keep the sweep going
                failed += 1
                print(f"  FAIL {receipt.image_id} r{receipt.receipt_id}: {exc}")
                continue
            processed += 1
            for key in (
                "lines",
                "rows",
                "sections",
                "sectioned_rows",
                "straddle_rows",
                "sections_updated",
                "sections_line_ids_changed",
                "sections_emptied",
                "sections_row_aligned_before",
                "stale_rows_deleted",
            ):
                totals[key] += stats[key]
            straddle_methods.update(stats["straddle_methods"])
            if args.apply:
                marker.write_text(json.dumps(stats, indent=2))
            if i % 100 == 0:
                print(f"  ... {i}/{len(receipts)}")

    # --- report -------------------------------------------------------------
    sectioned = totals["sectioned_rows"] or 1
    n_sections = totals["sections"] or 1
    summary = {
        "mode": mode,
        "table": args.table,
        "receipts_processed": processed,
        "receipts_skipped_cached": skipped,
        "receipts_failed": failed,
        "rows_created": totals["rows"],
        "lines_covered": totals["lines"],
        "sections_seen": totals["sections"],
        "sections_updated_with_row_ids": totals["sections_updated"],
        "sections_line_ids_changed": totals["sections_line_ids_changed"],
        "sections_emptied_deleted": totals["sections_emptied"],
        "stale_rows_deleted": totals["stale_rows_deleted"],
        "sectioned_rows": totals["sectioned_rows"],
        "straddle_rows_resolved": totals["straddle_rows"],
        "straddle_methods": dict(straddle_methods),
        "pct_rows_section_atomic_before": round(
            100.0 * (1 - totals["straddle_rows"] / sectioned), 2
        ),
        "pct_sections_row_aligned_before": round(
            100.0 * totals["sections_row_aligned_before"] / n_sections, 2
        ),
        "pct_sections_row_aligned_after": (
            100.0 if totals["sections_updated"] else 0.0
        ),
        "straddle_log": str(straddle_path),
    }
    print(json.dumps(summary, indent=2))
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(summary, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
