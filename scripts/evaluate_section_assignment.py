#!/usr/bin/env python3
"""Evaluate D2 offline against QA-VALID dev sections without any writes.

The target manifest is a local JSON array containing ``image_id``,
``receipt_id``, and ``merchant``. The manifest is never copied into the
repository, and reports expose only eight-character case prefixes.
"""

# Sibling package paths must be installed before runtime imports.
# pylint: disable=wrong-import-position

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _package))

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_upload.section_assignment import (
    assign_row_sections,
    load_prior_model,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument(
        "--table", default=os.environ.get("DYNAMODB_TABLE_NAME")
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _ground_truth(sections: list[Any]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for section in sections:
        if section.validation_status != ValidationStatus.VALID.value:
            continue
        for row_id in section.row_ids or []:
            section_type = str(section.section_type)
            previous = labels.get(row_id)
            if previous is not None and previous != section_type:
                raise ValueError(
                    f"Conflicting VALID sections for row {row_id}: "
                    f"{previous} and {section_type}"
                )
            labels[row_id] = section_type
    return labels


def _score_predictions(
    truth: dict[int, str], predicted: dict[int, str]
) -> dict[str, Any]:
    """Score every QA-labeled row, including rows without a prediction."""

    per_type_total: Counter[str] = Counter(truth.values())
    per_type_matched: Counter[str] = Counter()
    matched = 0
    unassigned = 0
    for row_id, section_type in truth.items():
        predicted_type = predicted.get(row_id)
        if predicted_type is None:
            unassigned += 1
        elif predicted_type == section_type:
            matched += 1
            per_type_matched[section_type] += 1
    return {
        "matched": matched,
        "scored": len(truth),
        "unassigned": unassigned,
        "per_type_total": per_type_total,
        "per_type_matched": per_type_matched,
    }


def evaluate(
    client: DynamoClient,
    targets: list[dict[str, Any]],
    model: dict[str, Any],
) -> dict[str, Any]:
    """Run the pure D2 decoder and aggregate QA agreement."""

    # Keep the receipt-level accounting together so denominators are visible.
    # pylint: disable=too-many-locals

    total = 0
    matched = 0
    unassigned = 0
    per_type_total: Counter[str] = Counter()
    per_type_matched: Counter[str] = Counter()
    cases = []
    for target in targets:
        image_id = str(target["image_id"])
        receipt_id = int(target["receipt_id"])
        rows = client.get_receipt_rows_from_receipt(image_id, receipt_id)
        lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        sections = client.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
        truth = _ground_truth(sections)
        assignments = assign_row_sections(
            rows, lines, model, target.get("merchant")
        )
        predicted = {
            assignment.row.row_id: assignment.section_type
            for assignment in assignments
        }
        score = _score_predictions(truth, predicted)
        case_total = score["scored"]
        case_matched = score["matched"]
        total += case_total
        matched += case_matched
        unassigned += score["unassigned"]
        per_type_total.update(score["per_type_total"])
        per_type_matched.update(score["per_type_matched"])
        cases.append(
            {
                "case": image_id[:8],
                "receipt_id": receipt_id,
                "matched": case_matched,
                "scored": case_total,
                "unassigned": score["unassigned"],
                "agreement": (
                    case_matched / case_total if case_total else None
                ),
            }
        )

    per_type = {
        section_type: {
            "matched": per_type_matched[section_type],
            "scored": section_total,
            "recall": per_type_matched[section_type] / section_total,
        }
        for section_type, section_total in sorted(per_type_total.items())
    }
    agreement = matched / total if total else 0.0
    items_recall = per_type.get("ITEMS", {}).get("recall", 0.0)
    return {
        "table": DEV_TABLE,
        "model_schema_version": model.get("schema_version"),
        "target_count": len(targets),
        "matched": matched,
        "scored": total,
        "unassigned": unassigned,
        "agreement": agreement,
        "per_type": per_type,
        "acceptance": {
            "items_recall_at_least_0_70": items_recall >= 0.70,
            "overall_agreement_at_least_0_80": agreement >= 0.80,
        },
        "cases": cases,
    }


def main() -> int:
    """Evaluate the target manifest and return the acceptance-gate status."""

    args = _arguments()
    if args.table != DEV_TABLE or args.table == PROD_TABLE:
        raise SystemExit(
            f"Refusing table {args.table!r}; expected exact dev table "
            f"{DEV_TABLE!r}"
        )
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    report = evaluate(DynamoClient(args.table), targets, load_prior_model())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(report["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
