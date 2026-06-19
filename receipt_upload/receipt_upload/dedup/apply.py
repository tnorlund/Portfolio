"""Stage 3 — gated executor for the dedup merge plan (DRY-RUN by default).

Turns a merge plan (list of ``MergeResolution`` dicts from ``build_plan.py``)
into concrete DynamoDB operations:

  * **gap-fill labels** -> ``ReceiptWordLabel`` writes on the survivor, tagged
    ``label_consolidated_from`` with the source copy for provenance.
  * **redundant receipts** -> delete the Receipt and its children (labels, words,
    lines, letters, metadata). The parent **Image is never deleted** — within-image
    phantom groups keep the image and its survivor receipt.

``plan_operations`` is pure (no I/O) and unit-testable. ``execute`` performs the
work but is **dry-run unless ``apply=True``** is passed explicitly, and dry-run
requires no AWS access at all.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple


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
            plan.receipt_drops.append(ReceiptDrop(image_id=di, receipt_id=drid))
    return plan


def summarize(plan: ExecutionPlan) -> dict:
    return {
        "labels_to_add": len(plan.label_adds),
        "receipts_to_drop": len(plan.receipt_drops),
        "images_touched": len({a.image_id for a in plan.label_adds}
                              | {d.image_id for d in plan.receipt_drops}),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute(plan: ExecutionPlan, dynamo=None, *, apply: bool = False,
            source: str = "dedup-merge", backup_path: Optional[str] = None) -> dict:
    """Apply the plan. DRY-RUN unless ``apply=True``.

    Dry-run performs NO reads or writes and needs no ``dynamo`` client.

    When ``apply=True`` and ``backup_path`` is given, a complete restore file is
    written BEFORE any mutation: the raw DynamoDB item of every entity about to be
    deleted plus the key of every label about to be added. ``rollback()`` consumes
    that file to fully reverse the operation.
    """
    report = {
        "dry_run": not apply,
        "labels_added": 0,
        "receipts_deleted": 0,
        "children_deleted": 0,
        "backup_path": None,
        "errors": [],
    }

    if not apply:
        report["labels_added"] = len(plan.label_adds)
        report["receipts_deleted"] = len(plan.receipt_drops)
        return report

    if dynamo is None:
        raise ValueError("apply=True requires a dynamo client")

    from receipt_dynamo.constants import ValidationStatus
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    # Build label entities up front (needed for both backup keys and the writes).
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

    # Gather every entity to delete (letters aren't on GSI4 -> scan once) BEFORE
    # mutating, so the backup is complete and the deletes are a pure replay.
    letters_by_receipt = {}
    if plan.receipt_drops:
        lek = None
        while True:
            letters, lek = dynamo.list_receipt_letters(last_evaluated_key=lek)
            for lt in letters:
                letters_by_receipt.setdefault((lt.image_id, lt.receipt_id), []).append(lt)
            if not lek:
                break

    drop_bundles = []  # (drop, receipt, labels, words, lines, letters, metadata)
    for d in plan.receipt_drops:
        det = dynamo.get_receipt_details(d.image_id, d.receipt_id)
        lts = letters_by_receipt.get((d.image_id, d.receipt_id), [])
        md = None
        try:
            md = dynamo.get_receipt_metadata(d.image_id, d.receipt_id)
        except Exception:
            md = None  # no metadata for this receipt
        drop_bundles.append((d, det, lts, md))

    # ---- BACKUP (before any mutation) ----
    if backup_path:
        backup = {
            "table": getattr(dynamo, "table_name", None),
            "created_at": _now_iso(),
            "deleted_items": [],
            "added_label_keys": [],
        }
        for (_d, det, lts, md) in drop_bundles:
            ents = [det.receipt, *det.labels, *det.words, *det.lines, *lts]
            if md:
                ents.append(md)
            backup["deleted_items"].extend(e.to_item() for e in ents)
        backup["added_label_keys"] = [lbl.key for _a, lbl in labels_to_add]
        with open(backup_path, "w") as f:
            json.dump(backup, f)
        report["backup_path"] = backup_path

    # ---- 1) gap-fill labels onto survivors ----
    for a, label in labels_to_add:
        try:
            dynamo.add_receipt_word_label(label)
            report["labels_added"] += 1
        except Exception:
            try:
                dynamo.update_receipt_word_label(label)
                report["labels_added"] += 1
            except Exception as e:  # pragma: no cover - surfaced, not raised
                report["errors"].append(f"label {a.image_id}#{a.receipt_id} {a.label}: {e}")

    # ---- 2) delete redundant receipts + children (never the parent Image) ----
    for d, det, lts, md in drop_bundles:
        try:
            n = 0
            if det.labels:
                dynamo.delete_receipt_word_labels(det.labels); n += len(det.labels)
            if det.words:
                dynamo.delete_receipt_words(det.words); n += len(det.words)
            if det.lines:
                dynamo.delete_receipt_lines(det.lines); n += len(det.lines)
            if lts:
                dynamo.delete_receipt_letters(lts); n += len(lts)
            if md:
                dynamo.delete_receipt_metadata(md); n += 1
            dynamo.delete_receipt(det.receipt)
            report["receipts_deleted"] += 1
            report["children_deleted"] += n
        except Exception as e:  # pragma: no cover
            report["errors"].append(f"drop {d.image_id}#{d.receipt_id}: {e}")

    return report


def rollback(backup_path: str, dynamo) -> dict:
    """Reverse an apply: re-put every deleted item, delete every added label."""
    with open(backup_path) as f:
        data = json.load(f)
    table = getattr(dynamo, "table_name", None) or data.get("table")
    report = {"restored_items": 0, "removed_labels": 0, "errors": []}
    for item in data.get("deleted_items", []):
        try:
            dynamo._client.put_item(TableName=table, Item=item)
            report["restored_items"] += 1
        except Exception as e:  # pragma: no cover
            report["errors"].append(f"restore: {e}")
    for key in data.get("added_label_keys", []):
        try:
            dynamo._client.delete_item(TableName=table, Key=key)
            report["removed_labels"] += 1
        except Exception as e:  # pragma: no cover
            report["errors"].append(f"remove label: {e}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", help="merge plan JSON from build_plan (dry-run/apply)")
    ap.add_argument("--env", choices=["dev", "prod"], help="required with --apply/--rollback")
    ap.add_argument("--apply", action="store_true", help="ACTUALLY mutate (default: dry-run)")
    ap.add_argument("--backup", help="restore-file path (default: auto next to --plan)")
    ap.add_argument("--rollback", help="reverse a prior apply using its restore file")
    args = ap.parse_args()

    if args.rollback:
        if not args.env:
            raise SystemExit("--rollback requires --env")
        from receipt_dynamo import DynamoClient
        from receipt_upload.dedup.dossiers import ENV_TABLE

        dynamo = DynamoClient(ENV_TABLE[args.env])
        report = rollback(args.rollback, dynamo)
        print(f"ROLLED BACK: {json.dumps(report, indent=2)}")
        return

    if not args.plan:
        raise SystemExit("--plan is required")
    resolutions = json.load(open(args.plan))
    plan = plan_operations(resolutions)
    s = summarize(plan)
    print(f"Plan: add {s['labels_to_add']} gap-fill labels, drop "
          f"{s['receipts_to_drop']} receipts across {s['images_touched']} images.")
    for a in plan.label_adds:
        print(f"  + {a.label:14} on {a.image_id[:8]}#{a.receipt_id} @ {a.line_id}:{a.word_id} "
              f"'{a.word_text[:18]}' (from {a.from_member[-6:]})")
    for d in plan.receipt_drops:
        print(f"  - DROP receipt {d.image_id[:8]}#{d.receipt_id}")

    if not args.apply:
        print("\nDRY-RUN — nothing mutated. Re-run with --env <env> --apply to execute.")
        return

    if not args.env:
        raise SystemExit("--apply requires --env")
    from receipt_dynamo import DynamoClient
    from receipt_upload.dedup.dossiers import ENV_TABLE

    backup_path = args.backup or f"{args.plan}.restore_{args.env}_{_now_iso().replace(':', '')}.json"
    dynamo = DynamoClient(ENV_TABLE[args.env])
    report = execute(plan, dynamo, apply=True, backup_path=backup_path)
    print(f"\nAPPLIED: {json.dumps(report, indent=2)}")
    print(f"\nRollback with:\n  python -m receipt_upload.dedup.apply "
          f"--env {args.env} --rollback {backup_path}")


if __name__ == "__main__":
    main()
