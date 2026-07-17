#!/usr/bin/env python3
"""Calibrate a section emission bias on a frozen non-test QA cohort."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_chroma", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _package))

from receipt_dynamo import DynamoClient
from evaluate_section_assignment import DEV_TABLE, PROD_TABLE, evaluate


class CachedClient:
    """Serve one calibration sweep from a single read-only data snapshot."""

    def __init__(self, source: DynamoClient, targets: list[dict[str, Any]]):
        self._receipts = {}
        for target in targets:
            key = (str(target["image_id"]), int(target["receipt_id"]))
            self._receipts[key] = {
                "rows": source.get_receipt_rows_from_receipt(*key),
                "lines": source.list_receipt_lines_from_receipt(*key),
                "sections": source.get_receipt_sections_from_receipt(*key),
            }

    def get_receipt_rows_from_receipt(self, image_id: str, receipt_id: int):
        return self._receipts[(image_id, receipt_id)]["rows"]

    def list_receipt_lines_from_receipt(self, image_id: str, receipt_id: int):
        return self._receipts[(image_id, receipt_id)]["lines"]

    def get_receipt_sections_from_receipt(self, image_id: str, receipt_id: int):
        return self._receipts[(image_id, receipt_id)]["sections"]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument(
        "--bias",
        action="append",
        type=float,
        dest="biases",
        help="TRANSACTION_INFO emission bias; may be repeated",
    )
    return parser.parse_args()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    args = _arguments()
    if args.table != DEV_TABLE or args.table == PROD_TABLE:
        raise SystemExit(f"refusing non-dev table {args.table!r}")
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    base_model = json.loads(args.model.read_text(encoding="utf-8"))
    client = CachedClient(DynamoClient(args.table), targets)
    biases = args.biases or [0.0, -0.5, -1.0, -1.5, -2.0, -2.5, -3.0]
    trials = []
    for bias in biases:
        model = copy.deepcopy(base_model)
        model["emission_biases"] = {"TRANSACTION_INFO": bias}
        report = evaluate(client, targets, model)
        trials.append(
            {
                "txinfo_bias": bias,
                "agreement": report["agreement"],
                "items_recall": report["per_type"].get("ITEMS", {}).get(
                    "recall", 0.0
                ),
                "txinfo_precision": report["per_type"]
                .get("TRANSACTION_INFO", {})
                .get("precision", 0.0),
                "txinfo_recall": report["per_type"]
                .get("TRANSACTION_INFO", {})
                .get("recall", 0.0),
                "acceptance": report["acceptance"],
            }
        )
    eligible = [
        trial for trial in trials if all(trial["acceptance"].values())
    ]
    selected = (
        min(eligible, key=lambda trial: abs(trial["txinfo_bias"]))
        if eligible
        else None
    )
    result = {
        "role": "calibration_not_final_test",
        "target_manifest_sha256": _file_sha256(args.targets),
        "base_model_sha256": _file_sha256(args.model),
        "target_count": len(targets),
        "trials": trials,
        "selected": selected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if selected is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
