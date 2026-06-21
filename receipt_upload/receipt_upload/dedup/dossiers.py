"""Load receipts/words/labels from DynamoDB and build merge dossiers.

Usage: python -m receipt_upload.dedup.dossiers --env dev [--json dossiers.json]

This is the I/O wrapper around the pure :mod:`receipt_upload.dedup.context`
builder. Output is the LLM-ready context for stage 2 (the resolver).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from receipt_dynamo import DynamoClient

from receipt_upload.dedup.context import LabelObs, build_merge_dossiers

ENV_TABLE = {"dev": "ReceiptsTable-dc5be22", "prod": "ReceiptsTable-d7ff76a"}


def load_inputs(table: str):
    dc = DynamoClient(table)
    receipts = dc.list_receipts()[0]
    words = dc.list_receipt_words()[0]
    labels = dc.list_receipt_word_labels()[0]

    words_by_receipt = defaultdict(dict)
    text_at = {}
    for w in words:
        k = (w.image_id, w.receipt_id)
        t = getattr(w, "text", "") or ""
        words_by_receipt[k][(w.line_id, w.word_id)] = t
        text_at[(w.image_id, w.receipt_id, w.line_id, w.word_id)] = t

    labels_by_receipt = defaultdict(list)
    for lb in labels:
        k = (lb.image_id, lb.receipt_id)
        labels_by_receipt[k].append(
            LabelObs(
                label=lb.label,
                line_id=lb.line_id,
                word_id=lb.word_id,
                word_text=text_at.get(
                    (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id), ""
                ),
                validation_status=getattr(lb, "validation_status", None),
            )
        )
    return receipts, words_by_receipt, labels_by_receipt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=list(ENV_TABLE), default="dev")
    ap.add_argument("--json", help="write all dossiers to this path")
    args = ap.parse_args()

    receipts, words_by_receipt, labels_by_receipt = load_inputs(
        ENV_TABLE[args.env]
    )
    dossiers = build_merge_dossiers(
        receipts, words_by_receipt, labels_by_receipt
    )

    within = [d for d in dossiers if d.scope == "within_image"]
    cross = [d for d in dossiers if d.scope == "cross_image"]
    n_conflict = sum(len(d.conflicts) for d in dossiers)
    n_lost = sum(len(d.labels_only_on_nonsurvivor) for d in dossiers)
    n_junk = sum(len(d.junk_flags) for d in dossiers)
    n_clean = sum(
        1 for d in dossiers if d.deterministic_action == "drop_redundant"
    )

    print(f"[{args.env}] {len(receipts)} receipts")
    print(
        f"  merge groups (receipt-sha): {len(dossiers)} "
        f"(within-image {len(within)}, cross-image {len(cross)})"
    )
    print(
        f"  clean drop_redundant: {n_clean} | needs consolidation: "
        f"{len(dossiers) - n_clean}"
    )
    print(
        f"  total conflicts: {n_conflict} | labels-only-on-nonsurvivor: {n_lost} "
        f"| groups with junk labels: {n_junk}"
    )
    print("\n  sample groups:")
    for d in dossiers[:6]:
        ov = d.label_overlap_pct
        print(
            f"    {d.group_id} [{d.scope}] {len(d.members)} members | "
            f"survivor {d.survivor_suggested} | overlap {ov}% | "
            f"{len(d.conflicts)} conflicts | {len(d.labels_only_on_nonsurvivor)} "
            f"would-be-lost | action {d.deterministic_action}"
        )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                [d.to_llm_context() for d in dossiers],
                f,
                indent=2,
                default=str,
            )
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    main()
