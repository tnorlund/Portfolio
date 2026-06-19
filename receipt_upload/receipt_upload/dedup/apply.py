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
            source: str = "dedup-merge") -> dict:
    """Apply the plan. DRY-RUN unless ``apply=True``.

    Dry-run performs NO reads or writes and needs no ``dynamo`` client; it just
    reports what would happen.
    """
    report = {
        "dry_run": not apply,
        "labels_added": 0,
        "receipts_deleted": 0,
        "children_deleted": 0,
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

    # 1) gap-fill labels onto survivors (VALID; provenance recorded)
    for a in plan.label_adds:
        label = ReceiptWordLabel(
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
        )
        try:
            dynamo.add_receipt_word_label(label)
            report["labels_added"] += 1
        except Exception:
            try:
                dynamo.update_receipt_word_label(label)
                report["labels_added"] += 1
            except Exception as e:  # pragma: no cover - surfaced, not raised
                report["errors"].append(f"label {a.image_id}#{a.receipt_id} {a.label}: {e}")

    # 2) delete redundant receipts + children. Letters aren't on GSI4, so scan once.
    letters_by_receipt = {}
    if plan.receipt_drops:
        lek = None
        while True:
            letters, lek = dynamo.list_receipt_letters(last_evaluated_key=lek)
            for lt in letters:
                letters_by_receipt.setdefault((lt.image_id, lt.receipt_id), []).append(lt)
            if not lek:
                break

    for d in plan.receipt_drops:
        try:
            det = dynamo.get_receipt_details(d.image_id, d.receipt_id)
            n = 0
            if det.labels:
                dynamo.delete_receipt_word_labels(det.labels); n += len(det.labels)
            if det.words:
                dynamo.delete_receipt_words(det.words); n += len(det.words)
            if det.lines:
                dynamo.delete_receipt_lines(det.lines); n += len(det.lines)
            lts = letters_by_receipt.get((d.image_id, d.receipt_id), [])
            if lts:
                dynamo.delete_receipt_letters(lts); n += len(lts)
            try:
                md = dynamo.get_receipt_metadata(d.image_id, d.receipt_id)
                if md:
                    dynamo.delete_receipt_metadata(md); n += 1
            except Exception:
                pass  # no metadata for this receipt
            dynamo.delete_receipt(det.receipt)  # never delete the parent Image
            report["receipts_deleted"] += 1
            report["children_deleted"] += n
        except Exception as e:  # pragma: no cover
            report["errors"].append(f"drop {d.image_id}#{d.receipt_id}: {e}")

    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="merge plan JSON from build_plan")
    ap.add_argument("--env", choices=["dev", "prod"], help="required only with --apply")
    ap.add_argument("--apply", action="store_true", help="ACTUALLY mutate (default: dry-run)")
    args = ap.parse_args()

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
    from receipt_upload.dedup.dossiers import ENV_TABLE
    from receipt_dynamo import DynamoClient

    dynamo = DynamoClient(ENV_TABLE[args.env])
    report = execute(plan, dynamo, apply=True)
    print(f"\nAPPLIED: {json.dumps(report, indent=2)}")


if __name__ == "__main__":
    main()
