#!/usr/bin/env python3
"""Evaluate one immutable section candidate in an isolated Python process.

This worker is launched with ``python -I`` by ``txinfo_fresh_shadow.py``.  It
loads all local receipt packages from one archived candidate tree, verifies
their module origins, performs read-only DynamoDB fetches, and writes a
pseudonymized result.  It has no persistence code path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
from collections import Counter
from itertools import groupby
from pathlib import Path
from typing import Any, Mapping, Sequence

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
_PROJECT_PACKAGES = ("receipt_chroma", "receipt_dynamo", "receipt_upload")
_UNASSIGNED = "__UNASSIGNED__"
_WILSON_Z = 1.959963984540054


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--candidate-name", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--expected-priors-sha256", required=True)
    parser.add_argument(
        "--mode", choices=("verify", "evaluate"), required=True
    )
    parser.add_argument("--job", type=Path)
    parser.add_argument("--table")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _contains_project_package(path: Path) -> bool:
    """Return whether a sys.path entry can resolve a local receipt package."""

    return any(
        (path / package / package).is_dir() or (path / package).is_dir()
        for package in _PROJECT_PACKAGES
    )


def _configure_candidate_imports(
    candidate_root: Path,
) -> tuple[Any, type[Any], dict[str, str], int]:
    """Load candidate modules while excluding every other checkout path."""

    candidate_root = candidate_root.resolve()
    package_paths = [candidate_root / package for package in _PROJECT_PACKAGES]
    missing = [str(path) for path in package_paths if not path.is_dir()]
    if missing:
        raise ValueError(
            f"candidate archive is missing package roots: {missing}"
        )

    retained: list[str] = []
    removed = 0
    for item in sys.path:
        if not item:
            continue
        path = Path(item).resolve()
        if _contains_project_package(path) and not _is_within(
            path, candidate_root
        ):
            removed += 1
            continue
        retained.append(str(path))
    sys.path[:] = [str(path) for path in package_paths] + retained

    assignment = importlib.import_module("receipt_upload.section_assignment")
    dynamo_module = importlib.import_module("receipt_dynamo")
    importlib.import_module("receipt_chroma")

    provenance: dict[str, str] = {}
    for module_name in (
        "receipt_chroma",
        "receipt_dynamo",
        "receipt_upload",
        "receipt_upload.section_assignment",
    ):
        module = sys.modules[module_name]
        module_file = Path(str(module.__file__)).resolve()
        if not _is_within(module_file, candidate_root):
            raise RuntimeError(
                f"candidate import leak: {module_name} resolved to {module_file}"
            )
        provenance[module_name] = str(module_file.relative_to(candidate_root))

    _assert_no_project_module_leaks(candidate_root)
    return assignment, dynamo_module.DynamoClient, provenance, removed


def _assert_no_project_module_leaks(candidate_root: Path) -> None:
    leaked: dict[str, str] = {}
    for name, module in sorted(sys.modules.items()):
        if not name.startswith(_PROJECT_PACKAGES):
            continue
        module_file_value = getattr(module, "__file__", None)
        if module_file_value is None:
            continue
        module_file = Path(str(module_file_value)).resolve()
        if not _is_within(module_file, candidate_root):
            leaked[name] = str(module_file)
    if leaked:
        raise RuntimeError(f"candidate project-module leak: {leaked}")


def _wilson_interval(successes: int, total: int) -> dict[str, Any]:
    if total == 0:
        return {
            "successes": successes,
            "total": total,
            "estimate": None,
            "ci95": None,
        }
    estimate = successes / total
    z_squared = _WILSON_Z**2
    denominator = 1 + z_squared / total
    center = (estimate + z_squared / (2 * total)) / denominator
    margin = (
        _WILSON_Z
        * math.sqrt(
            estimate * (1 - estimate) / total + z_squared / (4 * total**2)
        )
        / denominator
    )
    return {
        "successes": successes,
        "total": total,
        "estimate": estimate,
        "ci95": [max(0.0, center - margin), min(1.0, center + margin)],
    }


def _fragmentation(section_types: Sequence[str]) -> dict[str, Any]:
    runs = [
        (section_type, len(list(items)))
        for section_type, items in groupby(section_types)
    ]
    run_counts = Counter(section_type for section_type, _ in runs)
    split_types = sorted(
        section_type for section_type, count in run_counts.items() if count > 1
    )
    strict_islands = sum(
        int(
            0 < index < len(runs) - 1
            and duration == 1
            and runs[index - 1][0] == runs[index + 1][0]
        )
        for index, (_, duration) in enumerate(runs)
    )
    return {
        "row_count": len(section_types),
        "run_count": len(runs),
        "single_row_run_count": sum(duration == 1 for _, duration in runs),
        "strict_island_count": strict_islands,
        "split_section_type_count": len(split_types),
        "split_section_types": split_types,
        "extra_repeated_runs": sum(count - 1 for count in run_counts.values()),
        "type_contiguous": not split_types,
    }


def _score(
    receipt_rows: Sequence[Mapping[str, Any]],
    vocabulary: Sequence[str],
) -> dict[str, Any]:
    truth_totals: Counter[str] = Counter()
    predicted_totals: Counter[str] = Counter()
    matched_totals: Counter[str] = Counter()
    confusion: Counter[tuple[str, str]] = Counter()
    txinfo_false_positives: Counter[tuple[str, str]] = Counter()
    matched = 0
    for row in receipt_rows:
        truth = str(row["truth"])
        predicted = str(row.get("predicted") or _UNASSIGNED)
        truth_totals[truth] += 1
        confusion[(truth, predicted)] += 1
        if predicted != _UNASSIGNED:
            predicted_totals[predicted] += 1
        if predicted == "TRANSACTION_INFO" and truth != "TRANSACTION_INFO":
            txinfo_false_positives[
                (truth, str(row.get("merchant") or "UNKNOWN"))
            ] += 1
        if predicted == truth:
            matched += 1
            matched_totals[truth] += 1

    labels = sorted(
        set(vocabulary) | set(truth_totals) | set(predicted_totals)
    )
    predicted_labels = labels + [_UNASSIGNED]
    per_type = {}
    for label in labels:
        type_matched = matched_totals[label]
        truth_total = truth_totals[label]
        predicted_total = predicted_totals[label]
        per_type[label] = {
            "matched": type_matched,
            "truth_rows": truth_total,
            "predicted_on_scored_rows": predicted_total,
            "recall": _wilson_interval(type_matched, truth_total),
            "precision": _wilson_interval(type_matched, predicted_total),
        }

    scored = len(receipt_rows)
    items = per_type.get("ITEMS", {})
    txinfo = per_type.get("TRANSACTION_INFO", {})
    return {
        "matched": matched,
        "scored": scored,
        "unassigned": confusion.total() - sum(predicted_totals.values()),
        "overall_agreement": _wilson_interval(matched, scored),
        "items_recall": items.get("recall", _wilson_interval(0, 0)),
        "txinfo_recall": txinfo.get("recall", _wilson_interval(0, 0)),
        "txinfo_precision": txinfo.get("precision", _wilson_interval(0, 0)),
        "per_type": per_type,
        "confusion_matrix": {
            "truth_labels": labels,
            "predicted_labels": predicted_labels,
            "matrix": [
                [
                    confusion[(truth, predicted)]
                    for predicted in predicted_labels
                ]
                for truth in labels
            ],
        },
        "txinfo_false_positives_by_truth_and_merchant": [
            {"truth": truth, "merchant": merchant, "count": count}
            for (truth, merchant), count in sorted(
                txinfo_false_positives.items()
            )
        ],
    }


def _input_snapshot(
    rows: Sequence[Any], lines: Sequence[Any]
) -> dict[str, Any]:
    return {
        "rows": [
            {
                "row_id": int(row.row_id),
                "line_ids": [int(value) for value in row.line_ids],
                "x_min": float(row.x_min),
                "x_max": float(row.x_max),
                "y_min": float(row.y_min),
                "y_max": float(row.y_max),
                "amount_text": row.amount_text,
            }
            for row in sorted(rows, key=lambda item: int(item.row_id))
        ],
        "lines": [
            {"line_id": int(line.line_id), "text": str(line.text)}
            for line in sorted(lines, key=lambda item: int(item.line_id))
        ],
    }


def _case_id(image_id: str, receipt_id: int, salt: str) -> str:
    return _sha256_bytes(f"{salt}:{image_id}:{receipt_id}".encode("utf-8"))[
        :12
    ]


def _aggregate_fragmentation(
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    fields = (
        "row_count",
        "run_count",
        "single_row_run_count",
        "strict_island_count",
        "split_section_type_count",
        "extra_repeated_runs",
    )
    totals = {
        field: sum(
            int(receipt["fragmentation"][field]) for receipt in receipts
        )
        for field in fields
    }
    receipt_count = len(receipts)
    return {
        **totals,
        "receipt_count": receipt_count,
        "receipts_with_split_section_types": sum(
            not bool(receipt["fragmentation"]["type_contiguous"])
            for receipt in receipts
        ),
        "type_contiguous_receipts": sum(
            bool(receipt["fragmentation"]["type_contiguous"])
            for receipt in receipts
        ),
        "mean_runs_per_receipt": (
            totals["run_count"] / receipt_count if receipt_count else 0.0
        ),
    }


def _evaluate(
    *,
    assignment: Any,
    client_type: type[Any],
    table: str,
    job: Mapping[str, Any],
    model: Mapping[str, Any],
) -> dict[str, Any]:
    if job.get("truth_lock_validated") is not True:
        raise ValueError(
            "worker refuses evaluation without validated truth lock"
        )
    client = client_type(table)
    receipt_results = []
    all_scored_rows: list[dict[str, Any]] = []
    snapshot_records = []
    case_salt = str(job["upload_manifest_sha256"])
    for receipt in job["receipts"]:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        merchant = receipt.get("merchant")
        rows = client.get_receipt_rows_from_receipt(image_id, receipt_id)
        lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        assignments = assignment.assign_row_sections(
            rows, lines, model, merchant
        )
        predicted = {
            int(item.row.row_id): str(item.section_type)
            for item in assignments
        }
        truth = {
            int(item["row_id"]): str(item["section_type"])
            for item in receipt["truth_rows"]
        }
        scored_rows = [
            {
                "row_id": row_id,
                "truth": section_type,
                "predicted": predicted.get(row_id),
                "merchant": merchant,
            }
            for row_id, section_type in sorted(truth.items())
        ]
        all_scored_rows.extend(scored_rows)
        snapshot = _input_snapshot(rows, lines)
        snapshot_sha = _sha256_bytes(_canonical_json(snapshot))
        case = _case_id(image_id, receipt_id, case_salt)
        snapshot_records.append({"case": case, "sha256": snapshot_sha})
        fragment = _fragmentation(
            [str(item.section_type) for item in assignments]
        )
        receipt_score = _score(scored_rows, model["global"]["sections"])
        receipt_results.append(
            {
                "case": case,
                "input_snapshot_sha256": snapshot_sha,
                "matched": receipt_score["matched"],
                "scored": receipt_score["scored"],
                "unassigned": receipt_score["unassigned"],
                "agreement": receipt_score["overall_agreement"]["estimate"],
                "fragmentation": fragment,
                "predictions": [
                    {
                        "row_id": int(item.row.row_id),
                        "section_type": str(item.section_type),
                        "confidence": float(item.confidence),
                    }
                    for item in assignments
                ],
            }
        )

    return {
        "input_snapshot_sha256": _sha256_bytes(
            _canonical_json(
                sorted(snapshot_records, key=lambda item: item["case"])
            )
        ),
        "metrics": _score(all_scored_rows, model["global"]["sections"]),
        "fragmentation": _aggregate_fragmentation(receipt_results),
        "receipts": receipt_results,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = _arguments()
    candidate_root = args.candidate_root.resolve()
    assignment, client_type, provenance, removed_paths = (
        _configure_candidate_imports(candidate_root)
    )
    priors_path = (
        candidate_root
        / "receipt_upload"
        / "receipt_upload"
        / "assets"
        / "section_order_priors_v2.json"
    )
    priors_sha256 = _sha256_file(priors_path)
    if priors_sha256 != args.expected_priors_sha256:
        raise ValueError(
            f"priors hash mismatch: {priors_sha256} != {args.expected_priors_sha256}"
        )
    model = json.loads(priors_path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": args.mode,
        "candidate": {
            "name": args.candidate_name,
            "commit": args.candidate_commit,
            "priors_sha256": priors_sha256,
            "model_schema_version": model.get("schema_version"),
        },
        "isolation": {
            "python_isolated_flag": bool(sys.flags.isolated),
            "python_no_user_site": bool(sys.flags.no_user_site),
            "python_dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
            "removed_foreign_project_paths": removed_paths,
            "module_paths": provenance,
            "all_project_modules_from_candidate": True,
        },
    }
    if not sys.flags.isolated:
        raise RuntimeError("candidate worker must be launched with python -I")

    if args.mode == "evaluate":
        if args.job is None:
            raise ValueError("--job is required for evaluation")
        if args.table != DEV_TABLE or args.table == PROD_TABLE:
            raise ValueError(
                f"refusing table {args.table!r}; expected exact dev table {DEV_TABLE!r}"
            )
        job = json.loads(args.job.read_text(encoding="utf-8"))
        report["evaluation"] = _evaluate(
            assignment=assignment,
            client_type=client_type,
            table=args.table,
            job=job,
            model=model,
        )
    elif args.job is not None or args.table is not None:
        raise ValueError("verify mode does not accept a job or table")

    _assert_no_project_module_leaks(candidate_root)
    _write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
