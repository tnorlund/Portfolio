"""Stage 3 — gated executor for the dedup merge plan (DRY-RUN by default).

Turns a merge plan (list of ``MergeResolution`` dicts from ``build_plan.py``)
into concrete DynamoDB operations:

  * **gap-fill labels** -> ``ReceiptWordLabel`` writes on the survivor, tagged
    ``label_consolidated_from`` with the source copy for provenance.
  * **redundant receipts** -> delete the Receipt and its children (labels, words,
    lines, letters, metadata). The parent **Image is never deleted** — within-image
    phantom groups keep the image and its survivor receipt.

``plan_operations`` is pure (no I/O) and unit-testable. ``execute`` performs
the
work but is **dry-run unless ``apply=True``** is passed explicitly, and dry-run
requires no AWS access at all.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from receipt_upload.dedup._ddb import (
    AWS_ERRORS,
    DYNAMO_ERRORS,
    paginate,
    raw_client,
)


@dataclass
class LabelAdd:
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    word_text: str
    from_member: str


@dataclass
class ReceiptDrop:
    image_id: str
    receipt_id: int


@dataclass
class ExecutionPlan:
    label_adds: List[LabelAdd] = field(default_factory=list)
    receipt_drops: List[ReceiptDrop] = field(default_factory=list)


def _parse_key(s: str) -> Tuple[str, int]:
    image_id, rid = s.rsplit("#", 1)
    return image_id, int(rid)


def _parse_locus(s: str) -> Tuple[int, int]:
    ln, wd = s.split(":")
    return int(ln), int(wd)


def _receipt_subtree_items(
    dynamo, image_id: str, receipt_id: int
) -> List[dict]:
    """Every raw DynamoDB item owned by one receipt.

    Most children share ``PK = IMAGE#{image_id}`` with an SK
    ``RECEIPT#{rid}#...``, but the rid is sometimes ZERO-PADDED
    (``RECEIPT#00001``) and sometimes NOT (``ReceiptChatGPTValidation`` uses
    ``RECEIPT#1#...``), so we scan the broad ``RECEIPT#`` prefix and
    hard-filter on the rid token (padded OR unpadded) — this both catches the
    unpadded records and prevents sibling bleed. Some receipt-scoped records
    also live in a DIFFERENT partition (``ReceiptField`` is ``PK=FIELD#...``);
    those are found via GSI1 (``GSI1PK=IMAGE#{id}``,
    ``GSI1SK=RECEIPT#{rid:05d}#FIELD#...``).
    """
    pk = f"IMAGE#{image_id}"
    padded, unpadded = f"{receipt_id:05d}", str(receipt_id)
    out: List[dict] = []
    # main table: all receipt-scoped items under IMAGE# (padded or unpadded
    # rid)
    for it in paginate(
        dynamo,
        TableName=dynamo.table_name,
        KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk)",
        ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
        ExpressionAttributeValues={":pk": {"S": pk}, ":sk": {"S": "RECEIPT#"}},
    ):
        parts = it["SK"]["S"].split("#")
        if (
            len(parts) >= 2
            and parts[0] == "RECEIPT"
            and parts[1] in (padded, unpadded)
        ):
            out.append(it)
    # FIELD# partition: ReceiptField records, located via GSI1 (keys
    # projected).
    try:
        out.extend(
            paginate(
                dynamo,
                TableName=dynamo.table_name,
                IndexName="GSI1",
                KeyConditionExpression=(
                    "#pk = :pk AND begins_with(#sk, :sk)"
                ),
                ExpressionAttributeNames={
                    "#pk": "GSI1PK",
                    "#sk": "GSI1SK",
                },
                ExpressionAttributeValues={
                    ":pk": {"S": pk},
                    ":sk": {"S": f"RECEIPT#{padded}#FIELD#"},
                },
            )
        )
    except AWS_ERRORS:
        pass  # no GSI1 / no field records
    return out


def plan_operations(resolutions: List[dict]) -> ExecutionPlan:
    """Pure: derive the concrete add/drop operations from a merge plan."""
    plan = ExecutionPlan()
    for r in resolutions:
        img, rid = _parse_key(r["survivor"])
        for gf in r.get("gap_fills", []):
            ln, wd = _parse_locus(gf["locus"])
            plan.label_adds.append(
                LabelAdd(
                    image_id=img,
                    receipt_id=rid,
                    line_id=ln,
                    word_id=wd,
                    label=gf["label"],
                    word_text=gf.get("word_text", ""),
                    from_member=gf["from_member"],
                )
            )
        for d in r.get("receipts_to_drop", []):
            di, drid = _parse_key(d)
            plan.receipt_drops.append(
                ReceiptDrop(image_id=di, receipt_id=drid)
            )
    return plan


def summarize(plan: ExecutionPlan) -> dict:
    return {
        "labels_to_add": len(plan.label_adds),
        "receipts_to_drop": len(plan.receipt_drops),
        "images_touched": len(
            {a.image_id for a in plan.label_adds}
            | {d.image_id for d in plan.receipt_drops}
        ),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute(
    plan: ExecutionPlan,
    dynamo=None,
    *,
    apply: bool = False,
    source: str = "dedup-merge",
    backup_path: Optional[str] = None,
) -> dict:
    """Apply the plan. DRY-RUN unless ``apply=True``.

    Dry-run performs NO reads or writes and needs no ``dynamo`` client.

    When ``apply=True`` and ``backup_path`` is given, a complete restore file
    is
    written BEFORE any mutation: the raw DynamoDB item of every entity about to be
    deleted plus the key of every label about to be added. ``rollback()``
    consumes that file to fully reverse the operation.
    """
    report = {
        "dry_run": not apply,
        "labels_added": 0,
        "receipts_deleted": 0,
        "children_deleted": 0,
        "backup_path": None,
        "errors": [],
    }

    report["skipped_drops"] = []

    if not apply:
        report["labels_added"] = len(plan.label_adds)
        report["receipts_deleted"] = len(plan.receipt_drops)
        return report

    if dynamo is None:
        raise ValueError("apply=True requires a dynamo client")
    # A real mutation must always be reversible — never delete without a
    # backup.
    if not backup_path:
        raise ValueError(
            "apply=True requires backup_path (deletions must be recoverable)"
        )

    # Build label entities up front (needed for both backup keys and the
    # writes).
    labels_to_add = [
        (
            a,
            ReceiptWordLabel(
                image_id=a.image_id,
                receipt_id=a.receipt_id,
                line_id=a.line_id,
                word_id=a.word_id,
                label=a.label,
                reasoning=f"dedup merge: VALID gap-fill migrated from {a.from_member}",
                timestamp_added=_now_iso(),
                validation_status=ValidationStatus.VALID.value,
                label_proposed_by=source,
                label_consolidated_from=a.from_member,
            ),
        )
        for a in plan.label_adds
    ]

    # Gather each dropped receipt's COMPLETE SK-subtree (Receipt + every child
    # type) BEFORE mutating, so the backup is complete and deletes are a pure
    # replay. The subtree query also catches letters/place/summary/validation/
    # analysis records that the old per-entity cascade missed.
    drop_subtrees = []  # (drop, [raw items])
    for d in plan.receipt_drops:
        items = _receipt_subtree_items(dynamo, d.image_id, d.receipt_id)
        drop_subtrees.append((d, items))

    # A gap-fill key may ALREADY hold a label (re-run, or a concurrent write
    # between plan + apply). Capture any pre-existing item so rollback restores
    # the original instead of deleting it (the update-fallback overwrites it).
    overwritten_labels = []
    for _a, label in labels_to_add:
        try:
            existing = raw_client(dynamo).get_item(
                TableName=dynamo.table_name, Key=label.key
            ).get("Item")
        except AWS_ERRORS:
            existing = None
        if existing:
            overwritten_labels.append(existing)

    # ---- BACKUP (before any mutation) ----
    backup = {
        "table": getattr(dynamo, "table_name", None),
        "created_at": _now_iso(),
        "deleted_items": [],
        "added_label_keys": [],
        "overwritten_labels": overwritten_labels,
    }
    for _d, items in drop_subtrees:
        backup["deleted_items"].extend(items)
    backup["added_label_keys"] = [lbl.key for _a, lbl in labels_to_add]
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f)
    report["backup_path"] = backup_path

    failed_sources = _write_gap_fills(dynamo, labels_to_add, report)
    _delete_drops(dynamo, drop_subtrees, failed_sources, report)
    return report


def _write_gap_fills(dynamo, labels_to_add, report) -> set:
    """Write each gap-fill label onto its survivor. Returns the set of source
    receipts whose label FAILED to write — those drops must be skipped so the
    VALID label is not lost (recoverable via re-run)."""
    failed_sources = set()
    for a, label in labels_to_add:
        try:
            dynamo.add_receipt_word_label(label)
            report["labels_added"] += 1
        except DYNAMO_ERRORS:
            try:
                dynamo.update_receipt_word_label(label)
                report["labels_added"] += 1
            except DYNAMO_ERRORS as e:  # surfaced, not raised
                report["errors"].append(
                    f"label {a.image_id}#{a.receipt_id} {a.label} "
                    f"(from {a.from_member}): {e}"
                )
                failed_sources.add(a.from_member)
    return failed_sources


def _delete_drops(dynamo, drop_subtrees, failed_sources, report) -> None:
    """Delete each dropped receipt's full subtree (never the parent Image)."""
    client = raw_client(dynamo)
    for d, items in drop_subtrees:
        drop_key = f"{d.image_id}#{d.receipt_id}"
        if drop_key in failed_sources:
            report["skipped_drops"].append(drop_key)
            report["errors"].append(
                f"skipped drop {drop_key}: a VALID gap-fill from it "
                f"failed to write"
            )
            continue
        had_receipt = any(
            it.get("TYPE", {}).get("S") == "RECEIPT" for it in items
        )
        try:
            for it in items:
                client.delete_item(
                    TableName=dynamo.table_name,
                    Key={"PK": it["PK"], "SK": it["SK"]},
                )
            report["children_deleted"] += max(
                0, len(items) - (1 if had_receipt else 0)
            )
            if had_receipt:
                report["receipts_deleted"] += 1
        except AWS_ERRORS as e:
            report["errors"].append(f"drop {drop_key}: {e}")


def rollback(backup_path: str, dynamo) -> dict:
    """Reverse an apply: delete added labels, then re-put every deleted item and
    restore any label that was OVERWRITTEN by a gap-fill update (so a
    pre-existing
    label is recovered instead of being left deleted)."""
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    table = getattr(dynamo, "table_name", None) or data.get("table")
    client = raw_client(dynamo)
    report = {
        "restored_items": 0,
        "removed_labels": 0,
        "restored_labels": 0,
        "errors": [],
    }

    # delete the labels we added first...
    for key in data.get("added_label_keys", []):
        try:
            client.delete_item(TableName=table, Key=key)
            report["removed_labels"] += 1
        except AWS_ERRORS as e:
            report["errors"].append(f"remove label: {e}")
    # ...then re-put deleted subtrees and any overwritten originals (after the
    # delete, so an overwritten label is restored to its pre-apply value).
    for item in data.get("deleted_items", []):
        try:
            client.put_item(TableName=table, Item=item)
            report["restored_items"] += 1
        except AWS_ERRORS as e:
            report["errors"].append(f"restore: {e}")
    for item in data.get("overwritten_labels", []):
        try:
            client.put_item(TableName=table, Item=item)
            report["restored_labels"] += 1
        except AWS_ERRORS as e:
            report["errors"].append(f"restore overwritten label: {e}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan", help="merge plan JSON from build_plan (dry-run/apply)"
    )
    ap.add_argument(
        "--env",
        choices=["dev", "prod"],
        help="required with --apply/--rollback",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="ACTUALLY mutate (default: dry-run)",
    )
    ap.add_argument(
        "--backup", help="restore-file path (default: auto next to --plan)"
    )
    ap.add_argument(
        "--rollback", help="reverse a prior apply using its restore file"
    )
    args = ap.parse_args()

    # Imported here (CLI entry only) to keep the importable module free of a
    # hard receipt_dynamo client dependency.
    # pylint: disable=import-outside-toplevel
    from receipt_dynamo import DynamoClient
    from receipt_upload.dedup.dossiers import ENV_TABLE

    if args.rollback:
        if not args.env:
            raise SystemExit("--rollback requires --env")
        dynamo = DynamoClient(ENV_TABLE[args.env])
        report = rollback(args.rollback, dynamo)
        print(f"ROLLED BACK: {json.dumps(report, indent=2)}")
        return

    if not args.plan:
        raise SystemExit("--plan is required")
    with open(args.plan, encoding="utf-8") as f:
        resolutions = json.load(f)
    plan = plan_operations(resolutions)
    s = summarize(plan)
    print(
        f"Plan: add {s['labels_to_add']} gap-fill labels, drop "
        f"{s['receipts_to_drop']} receipts across {s['images_touched']} images."
    )
    for a in plan.label_adds:
        print(
            f"  + {a.label:14} on {a.image_id[:8]}#{a.receipt_id} @ {a.line_id}:{a.word_id} "
            f"'{a.word_text[:18]}' (from {a.from_member[-6:]})"
        )
    for d in plan.receipt_drops:
        print(f"  - DROP receipt {d.image_id[:8]}#{d.receipt_id}")

    if not args.apply:
        print(
            "\nDRY-RUN — nothing mutated. Re-run with --env <env> --apply to execute."
        )
        return

    if not args.env:
        raise SystemExit("--apply requires --env")
    backup_path = (
        args.backup
        or f"{args.plan}.restore_{args.env}_{_now_iso().replace(':', '')}.json"
    )
    dynamo = DynamoClient(ENV_TABLE[args.env])
    report = execute(plan, dynamo, apply=True, backup_path=backup_path)
    print(f"\nAPPLIED: {json.dumps(report, indent=2)}")
    print(
        f"\nRollback with:\n  python -m receipt_upload.dedup.apply "
        f"--env {args.env} --rollback {backup_path}"
    )
    if report["errors"]:
        raise SystemExit(
            f"\nCOMPLETED WITH {len(report['errors'])} ERROR(S) "
            f"(incl. {len(report['skipped_drops'])} skipped drops) — review above."
        )


if __name__ == "__main__":
    main()
