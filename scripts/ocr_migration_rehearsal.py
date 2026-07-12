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
label is faithfully re-applied. It can also RE-SEGMENT an image (split/merge
receipts), moving labels between receipt_ids. We therefore compare per IMAGE
(pooled across the image's receipts, so re-segmentation is not counted as loss)
the MULTISET of (label, word_text, validation_status) - joining each label to its
word's text - and separately the word-text-INSENSITIVE (label, validation_status)
counts. A tuple that disappears only because a word was re-read to different text
is churn; a drop in the word-text-insensitive count is a real lost label, and an
increase is a surplus (a duplicated/mis-attached label).

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

import boto3

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


def _canonicalize(obj: Any) -> Any:
    """Recursively sort DynamoDB set members (SS/NS/BS are unordered) so an
    otherwise-identical item does not read as 'mutated' from set reordering."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in ("SS", "NS", "BS") and isinstance(v, list):
                out[k] = sorted(v)
            else:
                out[k] = _canonicalize(v)
        return out
    if isinstance(obj, list):
        return [_canonicalize(v) for v in obj]
    return obj


def _canonical(wire_json: str) -> str:
    """Order-insensitive canonical form of a wire-JSON item for comparison."""
    try:
        return json.dumps(_canonicalize(json.loads(wire_json)), sort_keys=True)
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

# Per image_id: Counter over an identity tuple, pooled across the image's
# receipts (robust to re-OCR re-segmentation reassigning receipt_ids).
LabelIndex = dict[str, Counter]


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
    """Build two per-IMAGE label indexes (aggregated across the image's receipts).

    Keyed by image_id, NOT (image_id, receipt_id): re-OCR can RE-SEGMENT an image
    (our splitter can turn one receipt into two, or merge two), which reassigns
    words - and their labels - to different receipt_ids. A per-receipt invariant
    would then read that legitimate movement as loss-in-one + surplus-in-another.
    Pooling per image makes "no label was lost from this image" the invariant,
    which is what actually matters, while still catching a genuine drop.

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
        # Normalize case so an off-cased legacy row can't false-FAIL. Entities
        # already normalize via normalize_enum; this hardens against raw rows.
        status = "" if status is None else str(status).upper()
        word_text = words.get((image_id, rid, lid, wid), "")
        tuple_index.setdefault(image_id, Counter())[(label, word_text, status)] += 1
        count_index.setdefault(image_id, Counter())[(label, status)] += 1
    return tuple_index, count_index


@dataclass
class LabelReport:
    # image_id -> list of lost identity tuples with counts (pooled per image)
    lost_labels: dict[str, list[tuple[Any, int]]] = field(default_factory=dict)
    churn_only: dict[str, list[tuple[Any, int]]] = field(default_factory=dict)
    # image_id -> (label, status) counts that INCREASED. A clean re-apply
    # preserves per-image counts exactly; a surplus means the migration failed to
    # delete an old label row whose (line,word) id collided with a regenerated
    # word (so the orphan check can't see it) -> a duplicated, mis-attached label.
    surplus_labels: dict[str, list[tuple[Any, int]]] = field(default_factory=dict)
    images_checked: int = 0
    labels_before: int = 0
    labels_after: int = 0

    @property
    def has_real_loss(self) -> bool:
        return bool(self.lost_labels)

    @property
    def has_surplus(self) -> bool:
        return bool(self.surplus_labels)


def check_label_preservation(
    before: list[Row],
    after: list[Row],
    target_image_ids: Iterable[str] | None = None,
) -> LabelReport:
    """Assert every pre-migration label survives (per IMAGE, multiset >=).

    Labels are pooled per image (not per receipt) so a re-OCR that re-segments an
    image and moves labels between receipt_ids reads as preserved. A real loss is
    a decrease in the word-text-INSENSITIVE (label, validation_status) count for
    an image; a surplus is an increase (duplicated/mis-attached label). A tuple
    that only disappears from the word-text-SENSITIVE view (same (label,status)
    count) is churn from a re-read word, reported separately, not as loss.
    """
    targets = set(target_image_ids) if target_image_ids is not None else None
    before_tuples, before_counts = build_label_index(before)
    after_tuples, after_counts = build_label_index(after)

    def _in_scope(image_id: str) -> bool:
        return targets is None or image_id in targets

    report = LabelReport()
    for image_id, before_counter in before_counts.items():
        if not _in_scope(image_id):
            continue
        report.images_checked += 1
        report.labels_before += sum(before_counter.values())
        after_counter = after_counts.get(image_id, Counter())
        report.labels_after += sum(after_counter.values())

        # Real loss: word-text-insensitive (label, status) count dropped.
        lost = before_counter - after_counter  # only positive residuals
        if lost:
            report.lost_labels[image_id] = sorted(lost.items())

        # Surplus: (label, status) count INCREASED for an image present in both.
        # A faithful re-apply keeps per-image counts equal; a surplus is a
        # duplicated label -- the stale-label id-collision the orphan check can't
        # see (the stale row lands on a regenerated word at the same id).
        surplus = after_counter - before_counter
        if surplus:
            report.surplus_labels[image_id] = sorted(surplus.items())

        # Churn (informational): a (label, word_text, status) tuple vanished but
        # its (label, status) count is preserved -> the label moved to a re-read
        # word. Test EACH tuple's (label, status) against the real-loss set, not
        # the image-level truthiness of `lost`.
        tuple_lost = before_tuples.get(image_id, Counter()) - after_tuples.get(
            image_id, Counter()
        )
        churn = [
            item for item in tuple_lost.items() if (item[0][0], item[0][2]) not in lost
        ]
        if churn:
            report.churn_only[image_id] = sorted(churn)

    # Count labels on images that exist only in AFTER (so labels_after is a true
    # total, not just the before-images' contribution).
    for image_id, after_counter in after_counts.items():
        if _in_scope(image_id) and image_id not in before_counts:
            report.labels_after += sum(after_counter.values())
    return report


def find_orphan_label_keys(
    rows: list[Row], target_image_ids: Iterable[str] | None = None
) -> list[tuple[str, int, int, int, str]]:
    """AFTER label rows whose (image, receipt, line, word) has no word row.

    A migration that regenerates words under new ids but fails to delete the OLD
    label rows leaves dangling labels: they still parse and would be counted as
    'preserved' by the multiset check even though they point at a word that no
    longer exists. Every label must have its word.
    """
    targets = set(target_image_ids) if target_image_ids is not None else None
    words = build_word_text_map(rows)
    orphans: list[tuple[str, int, int, int, str]] = []
    for r in rows:
        image_id = r.image_id or image_id_from_pk(r.pk)
        if image_id is None or (targets is not None and image_id not in targets):
            continue
        parsed = parse_label_sk(r.sk)
        if parsed is None:
            continue
        rid, lid, wid, label = parsed
        if (image_id, rid, lid, wid) not in words:
            orphans.append((image_id, rid, lid, wid, label))
    return sorted(orphans)


def _word_counts(rows: list[Row], targets: set[str] | None) -> Counter:
    """image_id -> number of ReceiptWord rows (pooled across receipts).

    Per image, not per receipt: re-OCR can re-segment (merge receipts), so a
    single receipt_id dropping to zero words is legitimate movement, whereas an
    IMAGE dropping to zero words is a genuine wipe.
    """
    counts: Counter = Counter()
    for r in rows:
        image_id = r.image_id or image_id_from_pk(r.pk)
        if image_id is None or (targets is not None and image_id not in targets):
            continue
        if parse_word_sk(r.sk) is not None:
            counts[image_id] += 1
    return counts


def wiped_images(
    before: list[Row],
    after: list[Row],
    target_image_ids: Iterable[str] | None = None,
) -> list[str]:
    """Target images that had words BEFORE but have zero AFTER.

    Catches a wholesale nuke the label check misses for an image whose receipts
    carried no labels (nothing to 'lose') yet whose words all vanished.
    """
    targets = set(target_image_ids) if target_image_ids is not None else None
    before_wc = _word_counts(before, targets)
    after_wc = _word_counts(after, targets)
    return sorted(
        img for img, n in before_wc.items() if n > 0 and after_wc.get(img, 0) == 0
    )


def unchanged_targets(
    changed_pks: Iterable[str], target_image_ids: Iterable[str]
) -> list[str]:
    """Target images with NO changed row at all.

    A silent no-op, a migration that crashed before writing, or a driver that
    bypassed DYNAMODB_ENDPOINT_URL (writing to REAL dev instead) all leave the
    local table untouched -> an empty diff that would otherwise print PASS. A
    genuine re-OCR of every target must touch every target.
    """
    changed_images = {
        pk.split("#", 1)[1] for pk in changed_pks if pk.startswith("IMAGE#")
    }
    return sorted(i for i in target_image_ids if i not in changed_images)


def targets_without_word_changes(
    row_diff: "RowDiff", target_image_ids: Iterable[str]
) -> list[str]:
    """Target images with no changed WORD/LABEL row.

    Stronger than ``unchanged_targets``: a mixed-endpoint bypass (benign
    receipt-metadata writes hit the local table while word/label writes go to
    real dev) mutates one row per image and defeats the any-change check, yet
    leaves words/labels locally untouched. A genuine re-OCR deletes+recreates
    words and re-applies labels for every target, so every target must have at
    least one changed word/label-shaped (pk, sk).
    """
    changed = set()
    for pk, sk in row_diff.added + row_diff.deleted + row_diff.mutated:
        if not pk.startswith("IMAGE#"):
            continue
        if parse_word_sk(sk) is not None or parse_label_sk(sk) is not None:
            changed.add(pk.split("#", 1)[1])
    return sorted(i for i in target_image_ids if i not in changed)


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
    client = cache._robust_local_dynamo_client(endpoint_url, region)
    writer = cache.DynamoSQLiteWriter(out_path)
    # Own scan loop with a modest page Limit: DynamoDB Local's Scan can throw
    # InternalFailure on large pages after heavy write churn; small pages are
    # reliable (per-partition Queries never exhibited the failure).
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "ConsistentRead": True,
        "Limit": 250,
    }
    pages = scanned = 0
    while True:
        resp = client.scan(**kwargs)
        writer.add(resp.get("Items", []))
        pages += 1
        scanned += int(resp.get("ScannedCount", 0))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    stats = {"pages": pages, "scanned": scanned}
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
# Scoped pull (live: Query real dev for a handful of images -> BEFORE snapshot) #
# --------------------------------------------------------------------------- #


def _index_summary(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "IndexName": index["IndexName"],
        "KeySchema": index["KeySchema"],
        "Projection": index["Projection"],
    }


def pull_scoped_images(
    client: Any,
    table_name: str,
    image_ids: list[str],
    dest_path: Path,
    exclude_types: set[str] | None = None,
) -> dict[str, Any]:
    """Query ALL rows for the given images into a serve-compatible cache SQLite.

    Instead of scanning the full ~2M-item table, Query PK=IMAGE#<id> per image.
    Every entity of a receipt hierarchy (image, receipts, lines, words, letters,
    labels) shares PK=IMAGE#<id>, so one Query per image captures it whole. The
    output ``dynamodb.sqlite3`` + returned component dict match what the full
    ``local_analytics_cache`` sync produces, so ``serve`` can hydrate it and the
    diff can treat it as the exact BEFORE snapshot.

    ``exclude_types`` skips entity TYPEs on disk-constrained hosts (e.g. LETTER /
    RECEIPT_LETTER: 68% of rows, no labels, pure re-OCR regen). NEVER use it for
    a rollback ``backup`` — a backup must be complete to be a backup.
    """
    table = client.describe_table(TableName=table_name)["Table"]
    writer = cache.DynamoSQLiteWriter(dest_path)
    scanned = 0
    try:
        for image_id in image_ids:
            kwargs: dict[str, Any] = {
                "TableName": table_name,
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {":pk": {"S": f"IMAGE#{image_id}"}},
                "ConsistentRead": True,
            }
            while True:
                resp = client.query(**kwargs)
                items = resp.get("Items", [])
                if exclude_types:
                    items = [
                        it
                        for it in items
                        if it.get("TYPE", {}).get("S") not in exclude_types
                    ]
                writer.add(items)
                scanned += len(items)
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                kwargs["ExclusiveStartKey"] = lek
        synced_at = cache._utc_now()
        entity_counts = writer.finalize(
            {
                "table_name": table_name,
                "table_arn": table.get("TableArn"),
                "synced_at": synced_at,
                "scan": {"scanned": scanned},
                "scoped_image_ids": image_ids,
            }
        )
    except Exception:
        writer.abort()
        raise
    return {
        "valid": True,
        "path": "dynamodb.sqlite3",
        "table_name": table_name,
        "table_arn": table.get("TableArn"),
        "row_count": writer.row_count,
        "entity_counts": entity_counts,
        "scoped_image_ids": image_ids,
        "scan": {"scanned": scanned},
        "consistent_read": True,
        "table_schema": {
            "KeySchema": table["KeySchema"],
            "AttributeDefinitions": table["AttributeDefinitions"],
            "GlobalSecondaryIndexes": [
                _index_summary(i) for i in table.get("GlobalSecondaryIndexes", [])
            ],
            "LocalSecondaryIndexes": [
                _index_summary(i) for i in table.get("LocalSecondaryIndexes", [])
            ],
        },
        "synced_at": synced_at,
    }


def run_pull(
    env: str,
    cache_dir: Path,
    table_name: str,
    image_ids: list[str],
    region: str | None = None,
    profile: str | None = None,
    exclude_types: set[str] | None = None,
) -> dict[str, Any]:
    """Scoped pull into ``<cache_dir>/<env>/dynamodb.sqlite3`` + a serve manifest."""
    cache_root = cache_dir.expanduser().resolve() / env
    cache_root.mkdir(parents=True, exist_ok=True)
    client = boto3.Session(profile_name=profile, region_name=region).client("dynamodb")
    component = pull_scoped_images(
        client,
        table_name,
        image_ids,
        cache_root / "dynamodb.sqlite3",
        exclude_types=exclude_types,
    )
    manifest = cache._load_manifest(cache_root)
    manifest.setdefault("components", {})
    manifest["components"]["dynamodb"] = component
    manifest["schema_version"] = cache.SCHEMA_VERSION
    manifest["environment"] = env
    manifest["cache_root"] = str(cache_root)
    manifest["scoped"] = True
    now = cache._utc_now()
    manifest["updated_at"] = now
    manifest.setdefault("created_at", now)
    cache._write_manifest(cache_root, manifest)
    return {"cache_root": str(cache_root), "component": component}


# --------------------------------------------------------------------------- #
# Backup / restore (rollback)                                                  #
# --------------------------------------------------------------------------- #

LOCAL_ENDPOINT_HINTS = ("127.0.0.1", "localhost", "http://192.168.")


def _is_local_endpoint(endpoint_url: str | None) -> bool:
    return bool(endpoint_url) and any(h in endpoint_url for h in LOCAL_ENDPOINT_HINTS)


def _make_client(endpoint_url: str | None, region: str | None, allow_aws: bool):
    """Local-first guardrail: a real-AWS client requires an explicit opt-in.

    Uses the ROBUST local client (60s read timeout, retries): backup/restore/
    snapshot are bulk scan/write paths, and the 1s probe client dies mid-run.
    """
    if _is_local_endpoint(endpoint_url):
        return cache._robust_local_dynamo_client(endpoint_url, region)
    if not allow_aws:
        raise SystemExit(
            "REFUSING to touch a non-local endpoint without --allow-aws. "
            f"(endpoint_url={endpoint_url!r}) Pass --endpoint-url http://127.0.0.1:8000 "
            "for the local table, or add --allow-aws to deliberately target real AWS."
        )
    return boto3.Session(region_name=region).client("dynamodb")


def backup_images(
    client: Any,
    table_name: str,
    image_ids: list[str],
    out_path: Path,
) -> dict[str, Any]:
    """Wire-exact snapshot of the given IMAGE partitions (rollback source)."""
    component = pull_scoped_images(client, table_name, image_ids, out_path)
    return {
        "path": str(out_path),
        "rows": component["row_count"],
        "images": len(image_ids),
    }


def compute_restore_plan(
    backup_rows: list[Row],
    current_rows: list[Row],
    image_ids: list[str],
) -> tuple[list[Row], list[tuple[str, str]]]:
    """(puts, deletes) that return the target partitions to the backup state.

    puts    = every backup row (wire-exact overwrite; unchanged rows are no-ops)
    deletes = (pk, sk) present now but absent from the backup (rows the
              migration added), scoped to the target images only.
    """
    targets = {f"IMAGE#{i}" for i in image_ids}
    backup_keys = {(r.pk, r.sk) for r in backup_rows if r.pk in targets}
    puts = [r for r in backup_rows if r.pk in targets]
    deletes = [
        (r.pk, r.sk)
        for r in current_rows
        if r.pk in targets and (r.pk, r.sk) not in backup_keys
    ]
    return puts, deletes


def _query_partition(client: Any, table_name: str, pk: str) -> list[dict[str, Any]]:
    items, kwargs = [], {
        "TableName": table_name,
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": {"S": pk}},
        "ConsistentRead": True,
    }
    while True:
        resp = client.query(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


def _batch_write(client: Any, table_name: str, requests: list[dict[str, Any]]) -> None:
    """Chunked batch_write_item with EXPONENTIAL BACKOFF on UnprocessedItems.

    Under sustained load (a 370k-row restore) DynamoDB keeps shedding items;
    immediate re-submits never let it drain — the retry loop must back off.
    """
    import time as _time

    for i in range(0, len(requests), 25):
        chunk = {table_name: requests[i : i + 25]}
        for attempt in range(12):
            resp = client.batch_write_item(RequestItems=chunk)
            chunk = resp.get("UnprocessedItems") or {}
            if not chunk:
                break
            _time.sleep(min(5.0, 0.1 * (2**attempt)))
        if chunk:
            raise RuntimeError("batch_write left unprocessed items after retries")


def restore_images(
    client: Any,
    table_name: str,
    backup_path: Path,
    image_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Roll the target partitions back to the backup, then VERIFY byte-exact.

    Deletes rows the migration added, rewrites every backed-up row from its
    exact wire JSON, then re-reads each partition and compares canonical wire
    forms. Raises if verification fails — a rollback you can't verify is not a
    rollback.
    """
    backup_rows = load_rows(backup_path)
    ids = image_ids or sorted(
        {r.image_id or image_id_from_pk(r.pk) for r in backup_rows} - {None}
    )
    current: list[Row] = []
    for img in ids:
        for it in _query_partition(client, table_name, f"IMAGE#{img}"):
            native = cache._native_item(it)
            current.append(
                Row(
                    pk=str(native.get("PK", "")),
                    sk=str(native.get("SK", "")),
                    entity_type=None,
                    image_id=img,
                    receipt_id=None,
                    native=native,
                    wire_json=json.dumps(it),
                )
            )
    puts, deletes = compute_restore_plan(backup_rows, current, ids)
    requests = [
        {"DeleteRequest": {"Key": {"PK": {"S": pk}, "SK": {"S": sk}}}}
        for pk, sk in deletes
    ] + [{"PutRequest": {"Item": json.loads(r.wire_json)}} for r in puts]
    _batch_write(client, table_name, requests)

    # Verify: every partition must match the backup — modulo LIVE-TABLE churn:
    # background pipelines legitimately mutate embedding_status/updated_at and
    # append COMPACTION_RUN rows between our write and read. Ignore exactly
    # those; anything else is a real mismatch.
    VOLATILE_ATTRS = ("embedding_status", "updated_at")
    VOLATILE_TYPES = ("COMPACTION_RUN",)

    def _stable(wire: str) -> str:
        try:
            it = json.loads(wire)
        except (TypeError, json.JSONDecodeError):
            return wire
        for a in VOLATILE_ATTRS:
            it.pop(a, None)
        return _canonical(json.dumps(it))

    mismatches = []
    for img in ids:
        want = {
            (r.pk, r.sk): _stable(r.wire_json)
            for r in backup_rows
            if r.pk == f"IMAGE#{img}"
        }
        got, got_types = {}, {}
        for it in _query_partition(client, table_name, f"IMAGE#{img}"):
            native = cache._native_item(it)
            key = (str(native.get("PK", "")), str(native.get("SK", "")))
            got[key] = _stable(json.dumps(it))
            got_types[key] = native.get("TYPE")
        if want != got:
            extra = {
                k
                for k in set(got) - set(want)
                if got_types.get(k) not in VOLATILE_TYPES
            }
            missing = set(want) - set(got)
            changed = {k for k in set(want) & set(got) if want[k] != got[k]}
            if missing or extra or changed:
                mismatches.append((img, len(missing), len(extra), len(changed)))
    if mismatches:
        raise RuntimeError(f"RESTORE VERIFICATION FAILED: {mismatches[:5]}")
    return {
        "images": len(ids),
        "puts": len(puts),
        "deletes": len(deletes),
        "verified": True,
    }


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


def reconcile_parked(
    labels: LabelReport, expect_parked: dict[str, int]
) -> dict[str, Any]:
    """Cancel loss/surplus pairs explained by PARKING, in place.

    A parked label legitimately transitions (label, ORIG_STATUS) ->
    (label, NEEDS_REVIEW) on its image, which the raw multiset check reads as
    one loss plus one surplus. For each image, cancel each lost (label, S)
    against available (label, NEEDS_REVIEW) surplus, capped by the apply
    report's parked count for that image. Anything left after reconciliation
    is REAL loss/surplus and still fails the verdict.
    """
    reconciled: dict[str, int] = {}
    images = set(labels.lost_labels) | set(labels.surplus_labels)
    for img in images:
        budget = expect_parked.get(img, 0)
        if budget <= 0:
            continue
        lost = dict(labels.lost_labels.get(img, []))
        surplus = dict(labels.surplus_labels.get(img, []))
        used = 0
        # Each park provably creates exactly one (label, NEEDS_REVIEW) row, so
        # the report's parked count vouches for NR surpluses DIRECTLY — the
        # matching (label, orig_status) loss may already be explained elsewhere
        # (e.g. an absorbed pre-orphan). Cancel NR surplus up to budget, and
        # opportunistically cancel a same-label loss 1:1 for each. Non-NR
        # surplus (real duplication) and unexplained loss still fail.
        for key in list(surplus.keys()):
            lab, st = key
            if st != "NEEDS_REVIEW" or used >= budget:
                continue
            take = min(surplus[key], budget - used)
            surplus[key] -= take
            used += take
            rem = take
            for lkey in list(lost.keys()):
                if lkey[0] != lab or rem <= 0:
                    continue
                c = min(lost[lkey], rem)
                lost[lkey] -= c
                rem -= c
                if lost[lkey] <= 0:
                    del lost[lkey]
        reconciled[img] = used
        for target, src in (
            (labels.lost_labels, {k: v for k, v in lost.items() if v > 0}),
            (labels.surplus_labels, {k: v for k, v in surplus.items() if v > 0}),
        ):
            if src:
                target[img] = sorted(src.items())
            else:
                target.pop(img, None)
    return reconciled


def absorbed_pre_orphans(
    before: list[Row],
    after: list[Row],
    target_image_ids: Iterable[str] | None = None,
) -> dict[str, Counter]:
    """Per image: (label, status) counts of pre-orphan rows ABSORBED by a move.

    A pre-orphan (label row whose word never existed) can have the same SK as a
    legitimately moved label when re-OCR reassigns coinciding ids; the put then
    overwrites the dead row. The real label survives; only the dead row's
    contribution leaves the multiset. Detect exactly that: a BEFORE pre-orphan
    SK that exists in AFTER with a LIVE word. Its BEFORE (label, status) is an
    EXPECTED disappearance, not a loss.
    """
    targets = set(target_image_ids) if target_image_ids is not None else None
    before_words = {(r.pk, r.sk) for r in before if parse_word_sk(r.sk)}
    after_words = {(r.pk, r.sk) for r in after if parse_word_sk(r.sk)}
    after_label_keys = {(r.pk, r.sk) for r in after if parse_label_sk(r.sk)}
    out: dict[str, Counter] = {}
    for r in before:
        m = parse_label_sk(r.sk)
        if not m:
            continue
        img = r.image_id or image_id_from_pk(r.pk)
        if img is None or (targets is not None and img not in targets):
            continue
        word_sk = r.sk.rsplit("#LABEL#", 1)[0]
        if (r.pk, word_sk) in before_words:
            continue  # not a pre-orphan
        if (r.pk, r.sk) in after_label_keys and (r.pk, word_sk) in after_words:
            status = str(r.native.get("validation_status") or "").upper()
            out.setdefault(img, Counter())[(m[3], status)] += 1
    return out


def _cancel_expected_losses(labels: LabelReport, expected: dict[str, Counter]) -> int:
    """Remove expected (label, status) disappearances from lost_labels."""
    cancelled = 0
    for img, exp in expected.items():
        if img not in labels.lost_labels:
            continue
        lost = dict(labels.lost_labels[img])
        new_lost = {}
        for key, n in lost.items():
            take = min(n, exp.get(key, 0))
            cancelled += take
            if n - take > 0:
                new_lost[key] = n - take
        if new_lost:
            labels.lost_labels[img] = sorted(new_lost.items())
        else:
            del labels.lost_labels[img]
    return cancelled


def run_diff(
    before_path: Path,
    after_path: Path,
    target_image_ids: list[str],
    expect_parked: dict[str, int] | None = None,
) -> dict[str, Any]:
    before = load_rows(before_path)
    after = load_rows(after_path)
    row_diff = diff_rows(before, after)
    violations = blast_radius_violations(row_diff.changed_pks, target_image_ids)
    labels = check_label_preservation(before, after, target_image_ids)
    # Absorption FIRST (exact, key-deterministic), park reconciliation second
    # (budgeted): otherwise the parked NEEDS_REVIEW budget gets spent cancelling
    # losses that absorption would have explained exactly, leaving genuine
    # parked transitions stranded as false "loss".
    absorbed = _cancel_expected_losses(
        labels, absorbed_pre_orphans(before, after, target_image_ids)
    )
    reconciled = reconcile_parked(labels, expect_parked) if expect_parked else {}
    # Orphan DELTA: pre-existing dangling labels (word already missing BEFORE
    # the migration) must not fail the run — only orphans the migration CREATED.
    before_orphans = set(find_orphan_label_keys(before, target_image_ids))
    orphans = [
        o
        for o in find_orphan_label_keys(after, target_image_ids)
        if o not in before_orphans
    ]
    wiped = wiped_images(before, after, target_image_ids)
    unchanged = unchanged_targets(row_diff.changed_pks, target_image_ids)
    word_bypass = targets_without_word_changes(row_diff, target_image_ids)

    ok = (
        not violations
        and not labels.has_real_loss
        and not labels.has_surplus
        and not orphans
        and not wiped
        and not unchanged
        and not word_bypass
    )
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
            "images_checked": labels.images_checked,
            "labels_before": labels.labels_before,
            "labels_after": labels.labels_after,
            "images_with_lost_labels": len(labels.lost_labels),
            "lost": dict(labels.lost_labels),
            "images_with_surplus_labels": len(labels.surplus_labels),
            "surplus": dict(labels.surplus_labels),
            "images_with_churn_only": len(labels.churn_only),
            "reconciled_parked": sum(reconciled.values()),
            "images_reconciled": len(reconciled),
            "absorbed_pre_orphans": absorbed,
        },
        "invariants": {
            "orphan_labels": [
                f"{img}#{rid}#{lid}#{wid}#{label}"
                for (img, rid, lid, wid, label) in orphans
            ],
            "pre_existing_orphans": len(before_orphans),
            "wiped_images": wiped,
            "unchanged_targets": unchanged,
            "targets_without_word_changes": word_bypass,
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
    print("=== Label preservation (per image) ===")
    print(
        f"  images_checked={lb['images_checked']} "
        f"labels_before={lb['labels_before']} labels_after={lb['labels_after']}"
    )
    print(
        f"  images_with_lost_labels={lb['images_with_lost_labels']} "
        f"images_with_churn_only={lb['images_with_churn_only']} "
        f"reconciled_parked={lb.get('reconciled_parked', 0)}"
    )
    for rk, items in list(lb["lost"].items())[:20]:
        print(f"    LOST {rk}: {items}")
    for rk, items in list(lb.get("surplus", {}).items())[:20]:
        print(f"    SURPLUS {rk}: {items}")
    inv = report["invariants"]
    print("=== Invariants ===")
    print(
        f"  orphan_labels={len(inv['orphan_labels'])} "
        f"wiped_images={len(inv['wiped_images'])} "
        f"unchanged_targets={len(inv['unchanged_targets'])} "
        f"targets_without_word_changes={len(inv['targets_without_word_changes'])}"
    )
    for o in inv["orphan_labels"][:10]:
        print(f"    ORPHAN LABEL (no word): {o}")
    for w in inv["wiped_images"][:10]:
        print(f"    WIPED IMAGE (words->0): {w}")
    for u in inv["unchanged_targets"][:10]:
        print(f"    UNCHANGED TARGET (no writes — no-op/bypass?): {u}")
    for u in inv["targets_without_word_changes"][:10]:
        print(f"    NO WORD/LABEL CHANGE (mixed-endpoint bypass?): {u}")
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
    d.add_argument(
        "--expect-parked",
        type=Path,
        default=None,
        help="apply-report JSON; reconciles (label,S)->(label,NEEDS_REVIEW) "
        "transitions up to each image's reported parked count",
    )

    pull = sub.add_parser(
        "pull", help="scoped BEFORE snapshot: Query only the listed images from dev"
    )
    pull.add_argument("--env", default="dev")
    pull.add_argument("--cache-dir", type=Path, default=cache.DEFAULT_CACHE_DIR)
    pull.add_argument("--table-name", required=True)
    pull.add_argument(
        "--images", type=Path, required=True, help="image_ids to pull, one per line"
    )
    pull.add_argument("--region", default=None)
    pull.add_argument("--profile", default=None)
    pull.add_argument(
        "--exclude-types",
        default=None,
        help="comma-separated entity TYPEs to skip (disk-constrained hosts); "
        "never valid for rollback backups",
    )

    bk = sub.add_parser(
        "backup",
        help="wire-exact snapshot of target image partitions (rollback source)",
    )
    bk.add_argument("--endpoint-url", default="http://127.0.0.1:8000")
    bk.add_argument(
        "--allow-aws",
        action="store_true",
        help="required to back up from a non-local endpoint",
    )
    bk.add_argument("--table-name", required=True)
    bk.add_argument("--region", default=None)
    bk.add_argument("--images", type=Path, required=True)
    bk.add_argument("--out", type=Path, required=True)

    rs = sub.add_parser(
        "restore", help="roll target partitions back to a backup + verify byte-exact"
    )
    rs.add_argument("--endpoint-url", default="http://127.0.0.1:8000")
    rs.add_argument(
        "--allow-aws",
        action="store_true",
        help="required to restore into a non-local endpoint",
    )
    rs.add_argument("--table-name", required=True)
    rs.add_argument("--region", default=None)
    rs.add_argument("--backup", type=Path, required=True)
    rs.add_argument(
        "--images",
        type=Path,
        default=None,
        help="optional subset; default = every image in the backup",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    if args.command == "backup":
        client = _make_client(args.endpoint_url, args.region, args.allow_aws)
        result = backup_images(
            client, args.table_name, _read_image_ids(args.images), args.out
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "restore":
        client = _make_client(args.endpoint_url, args.region, args.allow_aws)
        ids = _read_image_ids(args.images) if args.images else None
        result = restore_images(client, args.table_name, args.backup, ids)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "pull":
        ids = _read_image_ids(args.images)
        excl = (
            {t.strip() for t in args.exclude_types.split(",") if t.strip()}
            if args.exclude_types
            else None
        )
        result = run_pull(
            args.env,
            args.cache_dir,
            args.table_name,
            ids,
            args.region,
            args.profile,
            exclude_types=excl,
        )
        comp = result["component"]
        print(
            json.dumps(
                {
                    "cache_root": result["cache_root"],
                    "images": len(ids),
                    "row_count": comp["row_count"],
                    "entity_counts": comp["entity_counts"],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "snapshot":
        result = snapshot_local_table(
            args.endpoint_url, args.table_name, args.out, args.region
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "diff":
        target_ids = _read_image_ids(args.images)
        expect_parked = None
        if args.expect_parked:
            apply_report = json.loads(args.expect_parked.read_text())
            expect_parked = {
                img: int(r.get("parked", 0))
                for img, r in apply_report.items()
                if isinstance(r, dict) and r.get("status") == "ok"
            }
        report = run_diff(args.before, args.after, target_ids, expect_parked)
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        _print_report(report)
        return 0 if report["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
