"""Conflict-safe cross-environment ReceiptWordLabel union.

Words are matched by their EXACT key ``(image_id, receipt_id, line_id,
word_id)``. For receipts that live in both tables (shared image UUIDs) the
keys are identical, so no fuzzy text matching is needed. A VALID label is
copied from the source env to the target env only when:

  * the source word has EXACTLY ONE VALID label (ambiguous multi-VALID source
    words are left for the single-VALID resolution pass), and
  * the target word currently has ZERO VALID labels (never override, never
    create a second VALID label), and
  * the target word does not already carry that label.

This keeps the operation purely additive and preserves the "one VALID label per
word" invariant. Words that exist in only one table are NOT handled here (they
move with the record migration).
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from receipt_upload.dedup._ddb import DYNAMO_ERRORS, raw_client
from receipt_upload.dedup.context import is_valid_status
from receipt_upload.dedup.dossiers import ENV_TABLE

WordKey = Tuple[str, int, int, int]


@dataclass
class LabelAdd:
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    from_env: str
    source_proposed_by: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_labels_by_word(table: str):
    """Return ``(dynamo, {word_key: [ReceiptWordLabel, ...]})``."""
    dynamo = DynamoClient(table)
    byword: Dict[WordKey, List] = defaultdict(list)
    for lb in dynamo.list_receipt_word_labels()[0]:
        byword[
            (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id)
        ].append(lb)
    return dynamo, byword


def find_multivalid(byword: Dict[WordKey, List]) -> List[Tuple[WordKey, list]]:
    """Words carrying more than one VALID label (need single-VALID resolution).

    Returns ``[(word_key, [label_str, ...]), ...]``.
    """
    out = []
    for key, lbs in byword.items():
        valid = sorted(
            {
                lb.label
                for lb in lbs
                if is_valid_status(getattr(lb, "validation_status", None))
            }
        )
        if len(valid) > 1:
            out.append((key, valid))
    return out


def plan_union(
    src_byword: Dict[WordKey, List],
    dst_byword: Dict[WordKey, List],
    *,
    from_env: str,
    dst_word_keys: set | None = None,
) -> List[LabelAdd]:
    """Labels to add to the target env from the source env (conflict-safe).

    ``dst_word_keys`` is the set of word keys that EXIST in the target. When
    given, a target word that exists but has zero labels is still a fill
    candidate (exactly the zero-VALID words the union should fill); only words
    truly absent from the target are deferred to the record migration. When
    None, falls back to the labeled-word set (legacy, under-fills).
    """
    adds: List[LabelAdd] = []
    for key, src_lbs in src_byword.items():
        if dst_word_keys is not None:
            if key not in dst_word_keys:
                continue  # truly absent in target -> record migration
            dst_lbs = dst_byword.get(key, [])
        elif key not in dst_byword:
            continue  # legacy: no word set -> only labeled words considered
        else:
            dst_lbs = dst_byword[key]
        src_valid = [
            lb
            for lb in src_lbs
            if is_valid_status(getattr(lb, "validation_status", None))
        ]
        if len(src_valid) != 1:
            continue  # 0 or ambiguous (>1) VALID in source -> skip
        sl = src_valid[0]
        if any(
            is_valid_status(getattr(lb, "validation_status", None))
            for lb in dst_lbs
        ):
            continue  # target already has a VALID label -> never add a 2nd
        if any(lb.label == sl.label for lb in dst_lbs):
            continue  # target already carries this exact label
        adds.append(
            LabelAdd(
                image_id=key[0],
                receipt_id=key[1],
                line_id=key[2],
                word_id=key[3],
                label=sl.label,
                from_env=from_env,
                source_proposed_by=getattr(sl, "label_proposed_by", "") or "",
            )
        )
    return adds


def apply_union(
    adds: List[LabelAdd],
    dynamo,
    *,
    apply: bool = False,
    backup_path: str | None = None,
) -> dict:
    """DRY-RUN unless ``apply=True``. Writes a backup of added keys first."""
    report = {
        "dry_run": not apply,
        "labels_added": 0,
        "backup_path": None,
        "errors": [],
    }
    if not apply:
        report["labels_added"] = len(adds)
        return report
    if not backup_path:
        raise ValueError("apply=True requires backup_path")

    entities = [
        ReceiptWordLabel(
            image_id=a.image_id,
            receipt_id=a.receipt_id,
            line_id=a.line_id,
            word_id=a.word_id,
            label=a.label,
            reasoning=(
                f"label-sync union: VALID label propagated from {a.from_env} "
                f"(source proposer: {a.source_proposed_by})"
            ),
            timestamp_added=_now_iso(),
            validation_status=ValidationStatus.VALID.value,
            label_proposed_by="label-sync-union",
        )
        for a in adds
    ]
    backup = {
        "table": getattr(dynamo, "table_name", None),
        "created_at": _now_iso(),
        "added_label_keys": [e.key for e in entities],
    }
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f)
    report["backup_path"] = backup_path

    for e in entities:
        try:
            dynamo.add_receipt_word_label(e)
            report["labels_added"] += 1
        except DYNAMO_ERRORS as exc:  # surfaced, not raised
            report["errors"].append(
                f"{e.image_id}#{e.receipt_id} {e.line_id}:{e.word_id} "
                f"{e.label}: {exc}"
            )
    return report


def rollback(backup_path: str, dynamo) -> dict:
    """Delete every label this union added (reverse of apply)."""
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    table = getattr(dynamo, "table_name", None) or data.get("table")
    client = raw_client(dynamo)
    report = {"removed": 0, "errors": []}
    for key in data.get("added_label_keys", []):
        try:
            client.delete_item(TableName=table, Key=key)
            report["removed"] += 1
        except DYNAMO_ERRORS as exc:
            report["errors"].append(str(exc))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--direction",
        choices=["dev-to-prod", "prod-to-dev", "both"],
        default="both",
    )
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--backup-dir", help="restore-file dir (required with --apply)"
    )
    ap.add_argument(
        "--rollback", help="backup file of a prior union to reverse"
    )
    args = ap.parse_args()

    dev_dynamo, dev = load_labels_by_word(ENV_TABLE["dev"])
    prod_dynamo, prod = load_labels_by_word(ENV_TABLE["prod"])

    if args.rollback:
        # Select the client by the TABLE recorded in the backup (the env the
        # additions were written to), not a filename substring — the union
        # target is the dst, so union_prod_to_dev wrote to dev, not prod.
        with open(args.rollback, encoding="utf-8") as f:
            bk_table = json.load(f).get("table")
        target = next(
            (d for d in (dev_dynamo, prod_dynamo)
             if getattr(d, "table_name", None) == bk_table),
            None,
        )
        if target is None:
            raise SystemExit(f"backup table {bk_table!r} is not dev or prod")
        rep = rollback(args.rollback, target)
        print(f"ROLLED BACK ({bk_table}): {json.dumps(rep, indent=2)}")
        return

    print(
        f"multi-VALID words — dev: {len(find_multivalid(dev))} | "
        f"prod: {len(find_multivalid(prod))}"
    )

    # word-existence sets so existing-but-unlabeled target words are fillable
    def word_keys(dynamo):
        return {
            (w.image_id, w.receipt_id, w.line_id, w.word_id)
            for w in dynamo.list_receipt_words()[0]
        }

    dev_words, prod_words = word_keys(dev_dynamo), word_keys(prod_dynamo)

    # (source_name, target_name, target_dynamo, adds)
    jobs = []
    if args.direction in ("dev-to-prod", "both"):
        jobs.append((
            "dev", "prod", prod_dynamo,
            plan_union(dev, prod, from_env="dev", dst_word_keys=prod_words),
        ))
    if args.direction in ("prod-to-dev", "both"):
        jobs.append((
            "prod", "dev", dev_dynamo,
            plan_union(prod, dev, from_env="prod", dst_word_keys=dev_words),
        ))

    for src, dst, _dyn, adds in jobs:
        by_label: Dict[str, int] = defaultdict(int)
        for a in adds:
            by_label[a.label] += 1
        print(f"\n{src} -> {dst}: {len(adds)} VALID labels to add")
        for lab, n in sorted(by_label.items(), key=lambda x: -x[1])[:10]:
            print(f"    {n:4}  {lab}")

    if not args.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply --backup-dir.")
        return

    if not args.backup_dir:
        raise SystemExit("--apply requires --backup-dir")
    os.makedirs(args.backup_dir, exist_ok=True)
    for src, dst, dyn, adds in jobs:
        bp = os.path.join(
            args.backup_dir, f"union_{src}_to_{dst}.json"
        )
        rep = apply_union(adds, dyn, apply=True, backup_path=bp)
        print(f"\nAPPLIED {src}->{dst}: {json.dumps(rep, indent=2)}")


if __name__ == "__main__":
    main()
