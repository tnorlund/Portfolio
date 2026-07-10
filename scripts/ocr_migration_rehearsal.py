#!/usr/bin/env python3
"""Rehearse the destructive re-OCR -> DynamoDB migration against a LOCAL table.

This is the safety harness for the gated "re-OCR ~211 images, regenerating their
words + word-labels" migration. It never touches real dev/prod DynamoDB. The
workflow (built on top of ``scripts/local_analytics_cache.py`` / PR #1097):

  1. ``local_analytics_cache.py serve --env dev``  -> DynamoDB Local hydrated from
     the SQLite snapshot ``<cache>/dynamodb.sqlite3`` (this file is the exact
     "BEFORE" image).
  2. Run the real migration with ``DYNAMODB_ENDPOINT_URL`` pointed at the local
     container (writes go through ``DynamoClient`` unmodified).
  3. ``ocr_migration_rehearsal.py snapshot`` -> scan the post-migration local
     table into an "AFTER" SQLite (same schema as the snapshot).
  4. ``ocr_migration_rehearsal.py diff`` -> row-level diff, blast-radius check,
     and label-preservation check, with a hard verdict.

Why label-preservation is not simple key-equality: re-OCR reassigns line/word
ids, so a label's PK/SK (``...#LINE#..#WORD#..#LABEL#..``) changes even when the
label is faithfully re-applied. We therefore compare, per (image_id, receipt_id),
the MULTISET of (label, word_text, validation_status) - joining each label to its
word's text - and separately the word-text-INSENSITIVE (label, validation_status)
counts. A tuple that disappears only because a word was re-read to different text
is churn; a drop in the word-text-insensitive count is a real lost label.

The ``diff`` command is pure Python over two SQLite files and needs no Docker or
AWS; only ``snapshot`` talks to the live local container.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Reuse the cache tooling from PR #1097 for an identical SQLite schema/normalization.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import local_analytics_cache as cache  # noqa: E402

LOG = logging.getLogger("ocr_migration_rehearsal")

# SK grammars (see receipt_dynamo entities receipt_word.py / receipt_word_label.py).
# Word:  RECEIPT#<rid>#LINE#<lid>#WORD#<wid>
# Label: RECEIPT#<rid>#LINE#<lid>#WORD#<wid>#LABEL#<label>
_WORD_SK = re.compile(r"^RECEIPT#(\d+)#LINE#(\d+)#WORD#(\d+)$")
_LABEL_SK = re.compile(r"^RECEIPT#(\d+)#LINE#(\d+)#WORD#(\d+)#LABEL#(.+)$")


def parse_word_sk(sk: str) -> tuple[int, int, int] | None:
    """Return (receipt_id, line_id, word_id) for a ReceiptWord SK, else None."""
    m = _WORD_SK.match(sk or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_label_sk(sk: str) -> tuple[int, int, int, str] | None:
    """Return (receipt_id, line_id, word_id, label) for a label SK, else None."""
    m = _LABEL_SK.match(sk or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def image_id_from_pk(pk: str) -> str | None:
    return pk.split("#", 1)[1] if pk.startswith("IMAGE#") else None


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Row:
    pk: str
    sk: str
    entity_type: str | None
    image_id: str | None
    receipt_id: int | None
    native: dict[str, Any]
    wire_json: str


def load_rows(sqlite_path: Path) -> list[Row]:
    """Read every dynamo_items row from a snapshot/after SQLite file."""
    with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as conn:
        cur = conn.execute(
            "SELECT pk, sk, entity_type, image_id, receipt_id, item_json, "
            "dynamodb_json FROM dynamo_items"
        )
        rows: list[Row] = []
        for pk, sk, etype, image_id, receipt_id, item_json, wire_json in cur:
            try:
                native = json.loads(item_json)
            except (TypeError, json.JSONDecodeError):
                native = {}
            rows.append(
                Row(
                    pk=pk,
                    sk=sk,
                    entity_type=etype,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    native=native,
                    wire_json=wire_json,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# Row-level diff + blast radius                                               #
# --------------------------------------------------------------------------- #


@dataclass
class RowDiff:
    added: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    mutated: list[tuple[str, str]] = field(default_factory=list)
    by_entity: Counter = field(default_factory=Counter)  # (op, entity_type) -> n

    @property
    def changed_pks(self) -> set[str]:
        return {pk for pk, _ in self.added + self.deleted + self.mutated}


def diff_rows(before: list[Row], after: list[Row]) -> RowDiff:
    """Diff two snapshots on (pk, sk) using the exact wire JSON for mutation."""
    before_map = {(r.pk, r.sk): r for r in before}
    after_map = {(r.pk, r.sk): r for r in after}
    diff = RowDiff()
    for key, r in after_map.items():
        if key not in before_map:
            diff.added.append(key)
            diff.by_entity[("added", r.entity_type)] += 1
    for key, r in before_map.items():
        if key not in after_map:
            diff.deleted.append(key)
            diff.by_entity[("deleted", r.entity_type)] += 1
        else:
            other = after_map[key]
            if _canonical(r.wire_json) != _canonical(other.wire_json):
                diff.mutated.append(key)
                diff.by_entity[("mutated", r.entity_type)] += 1
    return diff


def _canonical(wire_json: str) -> str:
    """Order-insensitive canonical form of a wire-JSON item for comparison."""
    try:
        return json.dumps(json.loads(wire_json), sort_keys=True)
    except (TypeError, json.JSONDecodeError):
        return wire_json


def blast_radius_violations(
    changed_pks: Iterable[str], target_image_ids: Iterable[str]
) -> list[str]:
    """Changed PKs that are NOT one of the migration's target images.

    A clean migration only mutates rows under IMAGE#<id> for the images it
    re-OCRs; anything else changing is an unexpected, out-of-scope write.
    """
    allowed = {f"IMAGE#{i}" for i in target_image_ids}
    return sorted({pk for pk in changed_pks if pk not in allowed})


# --------------------------------------------------------------------------- #
# Label preservation                                                          #
# --------------------------------------------------------------------------- #

# Per (image_id, receipt_id): Counter over an identity tuple.
LabelIndex = dict[tuple[str, int], Counter]


def build_word_text_map(rows: list[Row]) -> dict[tuple[str, int, int, int], str]:
    """(image_id, receipt_id, line_id, word_id) -> word text."""
    out: dict[tuple[str, int, int, int], str] = {}
    for r in rows:
        image_id = r.image_id or image_id_from_pk(r.pk)
        if image_id is None:
            continue
        parsed = parse_word_sk(r.sk)
        if parsed is None:
            continue
        rid, lid, wid = parsed
        text = r.native.get("text")
        out[(image_id, rid, lid, wid)] = "" if text is None else str(text)
    return out


def build_label_index(rows: list[Row]) -> tuple[LabelIndex, LabelIndex]:
    """Build two per-receipt label indexes.

    Returns (tuple_index, count_index):
      - tuple_index counts (label, word_text, validation_status) - sensitive to a
        word being re-read to different text (churn shows here);
      - count_index counts (label, validation_status) - word-text-INSENSITIVE, so
        a decrease is a genuinely lost label, not churn.
    """
    words = build_word_text_map(rows)
    tuple_index: LabelIndex = {}
    count_index: LabelIndex = {}
    for r in rows:
        image_id = r.image_id or image_id_from_pk(r.pk)
        if image_id is None:
            continue
        parsed = parse_label_sk(r.sk)
        if parsed is None:
            continue
        rid, lid, wid, label = parsed
        status = r.native.get("validation_status")
        status = "" if status is None else str(status)
        word_text = words.get((image_id, rid, lid, wid), "")
        key = (image_id, rid)
        tuple_index.setdefault(key, Counter())[(label, word_text, status)] += 1
        count_index.setdefault(key, Counter())[(label, status)] += 1
    return tuple_index, count_index


@dataclass
class LabelReport:
    # (image_id, receipt_id) -> list of lost identity tuples with counts
    lost_labels: dict[tuple[str, int], list[tuple[Any, int]]] = field(
        default_factory=dict
    )
    churn_only: dict[tuple[str, int], list[tuple[Any, int]]] = field(
        default_factory=dict
    )
    receipts_checked: int = 0
    labels_before: int = 0
    labels_after: int = 0

    @property
    def has_real_loss(self) -> bool:
        return bool(self.lost_labels)


def check_label_preservation(
    before: list[Row],
    after: list[Row],
    target_image_ids: Iterable[str] | None = None,
) -> LabelReport:
    """Assert every pre-migration label survives (per receipt, multiset >=).

    A real loss is a decrease in the word-text-INSENSITIVE (label,
    validation_status) count for a receipt. A tuple that only disappears from the
    word-text-SENSITIVE view (same label+status count preserved) is churn from a
    re-read word and is reported separately, not as loss.
    """
    targets = set(target_image_ids) if target_image_ids is not None else None
    before_tuples, before_counts = build_label_index(before)
    _, after_counts = build_label_index(after)
    after_tuples, _ = build_label_index(after)

    report = LabelReport()
    for key, before_counter in before_counts.items():
        image_id, _rid = key
        if targets is not None and image_id not in targets:
            continue
        report.receipts_checked += 1
        report.labels_before += sum(before_counter.values())
        after_counter = after_counts.get(key, Counter())
        report.labels_after += sum(after_counter.values())

        # Real loss: word-text-insensitive (label, status) count dropped.
        lost = before_counter - after_counter  # only positive residuals
        if lost:
            report.lost_labels[key] = sorted(lost.items())

        # Churn (informational): a (label, text, status) tuple vanished but the
        # (label, status) count was preserved -> the label moved to a re-read word.
        tuple_lost = before_tuples.get(key, Counter()) - after_tuples.get(
            key, Counter()
        )
        churn = [item for item in tuple_lost.items() if not lost]
        if churn:
            report.churn_only[key] = sorted(churn)
    return report


# --------------------------------------------------------------------------- #
# Snapshot (live: talks to DynamoDB Local)                                     #
# --------------------------------------------------------------------------- #


def snapshot_local_table(
    endpoint_url: str,
    table_name: str,
    out_path: Path,
    region: str | None = None,
) -> dict[str, Any]:
    """Scan the running DynamoDB Local table into an 'after' SQLite file.

    Reuses the cache's DynamoSQLiteWriter so the schema/normalization exactly
    matches the pre-migration snapshot, making them directly diffable.
    """
    client = cache._local_dynamo_client(endpoint_url, region)
    writer = cache.DynamoSQLiteWriter(out_path)
    stats = cache._scan_segment(
        client=client,
        table_name=table_name,
        segment=0,
        total_segments=1,
        consistent_read=True,
        writer=writer,
    )
    writer.finalize(
        {
            "source": "dynamodb-local",
            "endpoint_url": endpoint_url,
            "table_name": table_name,
            "scan": stats,
        }
    )
    LOG.info("Snapshotted %s -> %s (%d items)", table_name, out_path, writer.row_count)
    return {"path": str(out_path), "items": writer.row_count, "scan": stats}


# --------------------------------------------------------------------------- #
# Reporting / CLI                                                             #
# --------------------------------------------------------------------------- #


def _read_image_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            ids.append(s)
    return ids


def run_diff(
    before_path: Path,
    after_path: Path,
    target_image_ids: list[str],
) -> dict[str, Any]:
    before = load_rows(before_path)
    after = load_rows(after_path)
    row_diff = diff_rows(before, after)
    violations = blast_radius_violations(row_diff.changed_pks, target_image_ids)
    labels = check_label_preservation(before, after, target_image_ids)

    ok = not violations and not labels.has_real_loss
    return {
        "ok": ok,
        "row_diff": {
            "added": len(row_diff.added),
            "deleted": len(row_diff.deleted),
            "mutated": len(row_diff.mutated),
            "by_entity": {
                f"{op}:{etype}": n
                for (op, etype), n in sorted(row_diff.by_entity.items())
            },
        },
        "blast_radius": {
            "target_images": len(target_image_ids),
            "changed_pks": len(row_diff.changed_pks),
            "violations": violations,
        },
        "labels": {
            "receipts_checked": labels.receipts_checked,
            "labels_before": labels.labels_before,
            "labels_after": labels.labels_after,
            "receipts_with_lost_labels": len(labels.lost_labels),
            "lost": {
                f"{img}#{rid}": items
                for (img, rid), items in labels.lost_labels.items()
            },
            "receipts_with_churn_only": len(labels.churn_only),
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    rd = report["row_diff"]
    br = report["blast_radius"]
    lb = report["labels"]
    print("=== Row diff ===")
    print(f"  added={rd['added']} deleted={rd['deleted']} mutated={rd['mutated']}")
    for k, n in rd["by_entity"].items():
        print(f"    {k}: {n}")
    print("=== Blast radius ===")
    print(
        f"  target_images={br['target_images']} changed_pks={br['changed_pks']} "
        f"violations={len(br['violations'])}"
    )
    for pk in br["violations"][:20]:
        print(f"    OUT-OF-SCOPE CHANGE: {pk}")
    print("=== Label preservation ===")
    print(
        f"  receipts_checked={lb['receipts_checked']} "
        f"labels_before={lb['labels_before']} labels_after={lb['labels_after']}"
    )
    print(
        f"  receipts_with_lost_labels={lb['receipts_with_lost_labels']} "
        f"receipts_with_churn_only={lb['receipts_with_churn_only']}"
    )
    for rk, items in list(lb["lost"].items())[:20]:
        print(f"    LOST {rk}: {items}")
    print("=== VERDICT ===")
    print("  PASS" if report["ok"] else "  FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="scan the local table into an AFTER sqlite")
    snap.add_argument("--endpoint-url", default="http://127.0.0.1:8000")
    snap.add_argument("--table-name", required=True)
    snap.add_argument("--region", default=None)
    snap.add_argument("--out", type=Path, required=True)

    d = sub.add_parser("diff", help="diff BEFORE vs AFTER, verdict on labels/radius")
    d.add_argument(
        "--before", type=Path, required=True, help="dynamodb.sqlite3 snapshot"
    )
    d.add_argument("--after", type=Path, required=True, help="snapshot of local table")
    d.add_argument(
        "--images",
        type=Path,
        required=True,
        help="file of migration target image_ids (one per line)",
    )
    d.add_argument("--json", type=Path, default=None, help="write full report JSON")

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    if args.command == "snapshot":
        result = snapshot_local_table(
            args.endpoint_url, args.table_name, args.out, args.region
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "diff":
        target_ids = _read_image_ids(args.images)
        report = run_diff(args.before, args.after, target_ids)
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        _print_report(report)
        return 0 if report["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
