#!/usr/bin/env python3
"""Run a truth-gated, read-only comparison of two frozen section decoders.

The command has three modes:

``preflight``
    Inspect input availability and immutable git identities.  It never imports
    a candidate, reads receipt rows, or produces predictions.
``verify-candidates``
    Archive each exact commit and verify isolated package imports and priors.
    It never reads the cohort or calls the decoder.
``run-once``
    Require Claude A's upload manifest and Claude B's hash-bound truth lock,
    create an exclusive run journal, and evaluate each candidate exactly once.

All evaluation output is pseudonymized.  The only DynamoDB operations are the
worker's row and line reads from the exact development table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

FROZEN_V4_COMMIT = "4190a878966784f0ccd2301669912e41d948dddc"
CORRECTED_COMMIT = "099d1197aea778f32f547cac2fc42ad5f33c600e"
PRIORS_SHA256 = "a10752fd037d9d1951b5195f06253e50b7116a3c39c7001b128802023ef613c4"
DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
DEFAULT_ARTIFACT_DIR = Path(
    "/Users/tnorlund/Portfolio_artifacts/txinfo-shadow-2026-07-17/fresh_upload_v1"
)

UPLOAD_MANIFEST = "UPLOAD_MANIFEST.json"
TRUTH_LOCKED = "TRUTH_LOCKED.json"
PREREGISTRATION = "PREREGISTRATION.json"
PREREGISTRATION_HASH = "PREREGISTRATION.sha256"
PREREGISTRATION_SHA256 = (
    "6957a10e004a9217425d266dd6e6f4e47d112f89b7f820df7665f49bf41d415d"
)
RUN_STATE = "RUN_STATE.json"
FROZEN_RESULT = "FROZEN_V4_RESULT.json"
CORRECTED_RESULT = "CORRECTED_RESULT.json"
COMPARISON = "COMPARISON.json"
RECOMMENDATION = "PROMOTION_RECOMMENDATION.md"
HASHES = "SHA256SUMS.json"

_CANDIDATES = (
    ("frozen_v4", FROZEN_V4_COMMIT, FROZEN_RESULT),
    ("corrected", CORRECTED_COMMIT, CORRECTED_RESULT),
)
_OUTPUT_NAMES = {
    RUN_STATE,
    FROZEN_RESULT,
    CORRECTED_RESULT,
    COMPARISON,
    RECOMMENDATION,
    HASHES,
}
_POINT_THRESHOLDS = {
    "overall_agreement": 0.80,
    "items_recall": 0.70,
    "txinfo_recall": 0.70,
    "txinfo_precision": 0.70,
}
_MINIMUM_TXINFO_TRUTH_ROWS = 60
_TRUTH_LABELS = {
    "ADDRESS",
    "AMBIGUOUS",
    "BARCODE",
    "FOOTER",
    "ITEMS",
    "OTHER",
    "PAYMENT",
    "SECTION_HEADER",
    "STOREFRONT",
    "SUMMARY",
    "SURVEY",
    "TOTAL_LINE",
    "TRANSACTION_INFO",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "verify-candidates", "run-once"))
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--table", default=DEV_TABLE)
    return parser.parse_args()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid JSON from {path.name}: {error}") from error


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def _write_text_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _create_run_state(path: Path, value: Mapping[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(rendered)


def _git(repo_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_metadata(repo_root: Path, commit: str) -> dict[str, str]:
    resolved = _git(repo_root, "rev-parse", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ValueError(f"immutable commit mismatch: {resolved} != {commit}")
    return {
        "commit": resolved,
        "tree": _git(repo_root, "rev-parse", f"{commit}^{{tree}}"),
        "committed_at": _git(repo_root, "show", "-s", "--format=%cI", commit),
    }


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not valid ISO-8601: {value!r}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _receipt_key(item: Mapping[str, Any]) -> tuple[str, int]:
    image_id = item.get("image_id")
    receipt_id = item.get("receipt_id")
    if not isinstance(image_id, str) or not image_id:
        raise ValueError("every receipt requires a non-empty image_id")
    if isinstance(receipt_id, bool) or not isinstance(receipt_id, int):
        raise ValueError("every receipt requires an integer receipt_id")
    return image_id, receipt_id


def _validate_preregistration(artifact_dir: Path) -> str:
    preregistration_path = artifact_dir / PREREGISTRATION
    declared_hash_path = artifact_dir / PREREGISTRATION_HASH
    if not preregistration_path.is_file() or not declared_hash_path.is_file():
        raise FileNotFoundError("locked preregistration and hash are required")
    actual_hash = _sha256_file(preregistration_path)
    declared_parts = declared_hash_path.read_text(encoding="utf-8").split()
    if not declared_parts or declared_parts[0] != actual_hash:
        raise ValueError("PREREGISTRATION.sha256 does not match exact JSON bytes")
    if actual_hash != PREREGISTRATION_SHA256:
        raise ValueError(
            f"unexpected preregistration hash: {actual_hash} != {PREREGISTRATION_SHA256}"
        )
    preregistration = _read_json(preregistration_path)
    candidates = preregistration.get("candidates", {})
    frozen = candidates.get("frozen_v4_baseline", {})
    corrected = candidates.get("sequence_invariant_correction", {})
    if frozen.get("git_commit") != FROZEN_V4_COMMIT:
        raise ValueError("preregistration frozen-v4 commit mismatch")
    if corrected.get("git_commit") != CORRECTED_COMMIT:
        raise ValueError("preregistration corrected commit mismatch")
    if frozen.get("priors_sha256") != PRIORS_SHA256:
        raise ValueError("preregistration frozen-v4 priors mismatch")
    if corrected.get("priors_sha256") != PRIORS_SHA256:
        raise ValueError("preregistration corrected priors mismatch")
    return actual_hash


def _manifest_receipts(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = value.get("receipts")
    if isinstance(direct, list):
        return [
            dict(item) if isinstance(item, dict) else {"receipt_id": item}
            for item in direct
        ]
    images = value.get("images")
    if not isinstance(images, list):
        raise ValueError("upload manifest requires receipts or images")
    receipts: list[dict[str, Any]] = []
    for image_index, image in enumerate(images):
        if not isinstance(image, dict):
            raise ValueError(f"upload image {image_index} must be an object")
        image_id = image.get("image_id")
        entries = image.get("receipts", image.get("receipt_ids", []))
        if not isinstance(entries, list):
            raise ValueError(f"upload image {image_index} receipts must be a list")
        for entry in entries:
            receipt = dict(entry) if isinstance(entry, dict) else {"receipt_id": entry}
            receipt.setdefault("image_id", image_id)
            receipt.setdefault(
                "uploaded_at",
                image.get("uploaded_at", image.get("created_at")),
            )
            receipt.setdefault(
                "merchant",
                image.get("merchant", image.get("merchant_name")),
            )
            receipts.append(receipt)
    return receipts


def _validate_upload_manifest(
    value: Any, *, corrected_committed_at: str
) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("UPLOAD_MANIFEST.json must be a JSON object")
    if (
        value.get("producer") != "claude_a"
        and value.get("role") != "claude_a_intake_audit"
    ):
        raise ValueError("UPLOAD_MANIFEST.json must be authored by claude_a")
    cohort = value.get("cohort", value.get("protocol"))
    if cohort != "fresh_upload_v1":
        raise ValueError("upload manifest cohort must be fresh_upload_v1")
    receipts = _manifest_receipts(value)
    if not receipts:
        raise ValueError("upload manifest receipts must be a non-empty list")
    freeze_time = _parse_time(corrected_committed_at, "corrected commit time")
    baseline_value = value.get("t0_utc", value.get("baseline_at"))
    baseline_time = (
        _parse_time(baseline_value, "t0_utc") if baseline_value is not None else None
    )
    if baseline_time is not None and baseline_time <= freeze_time:
        raise ValueError("upload intake baseline must follow the corrected commit")
    normalized: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for index, item in enumerate(receipts):
        key = _receipt_key(item)
        if key in keys:
            raise ValueError(f"duplicate upload receipt at index {index}")
        keys.add(key)
        uploaded_value = item.get("uploaded_at", item.get("created_at"))
        uploaded_at = (
            _parse_time(uploaded_value, f"receipts[{index}].uploaded_at")
            if uploaded_value is not None
            else baseline_time
        )
        if uploaded_at is None:
            raise ValueError(f"receipt {index} needs uploaded_at or a manifest t0_utc")
        if uploaded_at <= freeze_time:
            raise ValueError(
                f"receipt {index} is not fresh: uploaded_at must follow {corrected_committed_at}"
            )
        merchant = item.get("merchant", item.get("merchant_name"))
        if merchant is not None and not isinstance(merchant, str):
            raise ValueError(f"receipts[{index}].merchant must be a string or null")
        normalized.append(
            {
                "image_id": key[0],
                "receipt_id": key[1],
                "merchant": merchant,
                "uploaded_at": uploaded_at.isoformat(),
            }
        )
    return normalized


def _validate_truth_lock(
    value: Any,
    *,
    upload_sha256: str,
    upload_receipts: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, int], list[dict[str, Any]]],
    dict[tuple[str, int], str],
]:
    if not isinstance(value, dict):
        raise ValueError("TRUTH_LOCKED.json must be a JSON object")
    author = str(value.get("producer", value.get("author", "")))
    if "claude_b" not in author:
        raise ValueError("TRUTH_LOCKED.json must be authored by claude_b")
    if value.get("locked") is not True:
        raise ValueError("TRUTH_LOCKED.json must contain locked=true")
    if value.get("upload_manifest_sha256") != upload_sha256:
        raise ValueError("truth lock is not bound to the exact upload manifest bytes")
    _parse_time(value.get("locked_at"), "locked_at")
    receipts = value.get("receipts")
    if not isinstance(receipts, list):
        raise ValueError("truth lock receipts must be a list")
    expected_keys = {_receipt_key(item) for item in upload_receipts}
    truth_by_receipt: dict[tuple[str, int], list[dict[str, Any]]] = {}
    exclusions: dict[tuple[str, int], str] = {}
    exclusion_items = value.get("exclusions", [])
    if not isinstance(exclusion_items, list):
        raise ValueError("truth lock exclusions must be a list")
    for index, exclusion in enumerate(exclusion_items):
        if not isinstance(exclusion, dict):
            raise ValueError(f"truth exclusion {index} must be an object")
        key = _receipt_key(exclusion)
        reason = exclusion.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("every truth exclusion requires a written reason")
        if key in exclusions:
            raise ValueError(f"duplicate truth exclusion at index {index}")
        exclusions[key] = reason.strip()
    total_scored_rows = 0
    for index, item in enumerate(receipts):
        if not isinstance(item, dict):
            raise ValueError(f"truth receipt {index} must be an object")
        key = _receipt_key(item)
        if key in truth_by_receipt:
            raise ValueError(f"duplicate truth receipt at index {index}")
        rows = item.get("rows", item.get("truth_rows"))
        if not isinstance(rows, list):
            raise ValueError(f"truth receipt {index} rows must be a list")
        normalized_rows = []
        row_ids: set[int] = set()
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(
                    f"truth receipt {index} row {row_index} must be an object"
                )
            row_id = row.get("row_id")
            section_type = row.get("section_type", row.get("label"))
            if isinstance(row_id, bool) or not isinstance(row_id, int):
                raise ValueError("truth row_id must be an integer")
            if row_id in row_ids:
                raise ValueError(f"duplicate truth row_id {row_id} in receipt {index}")
            if section_type not in _TRUTH_LABELS:
                raise ValueError(
                    f"truth section_type is not preregistered: {section_type!r}"
                )
            if section_type == "AMBIGUOUS":
                note = row.get("note")
                if not isinstance(note, str) or not note.strip():
                    raise ValueError("AMBIGUOUS truth rows require a note")
            row_ids.add(row_id)
            normalized_rows.append(
                {
                    "row_id": row_id,
                    "section_type": section_type,
                    **(
                        {"note": str(row["note"]).strip()}
                        if section_type == "AMBIGUOUS"
                        else {}
                    ),
                }
            )
        total_scored_rows += sum(
            row["section_type"] != "AMBIGUOUS" for row in normalized_rows
        )
        truth_by_receipt[key] = normalized_rows
    if set(truth_by_receipt) & set(exclusions):
        raise ValueError("a receipt cannot have truth rows and be excluded")
    covered_keys = set(truth_by_receipt) | set(exclusions)
    if covered_keys != expected_keys:
        missing = len(expected_keys - covered_keys)
        extra = len(covered_keys - expected_keys)
        raise ValueError(
            f"truth lock cohort differs from upload manifest: missing={missing}, extra={extra}"
        )
    if total_scored_rows == 0:
        raise ValueError("truth lock contains no scored rows")
    return truth_by_receipt, exclusions


def _validate_inputs(
    artifact_dir: Path, repo_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    upload_path = artifact_dir / UPLOAD_MANIFEST
    truth_path = artifact_dir / TRUTH_LOCKED
    if not upload_path.is_file():
        raise FileNotFoundError(f"waiting for Claude A: {upload_path}")
    if not truth_path.is_file():
        raise FileNotFoundError(f"waiting for Claude B: {truth_path}")
    preregistration_sha256 = _validate_preregistration(artifact_dir)
    upload_sha256 = _sha256_file(upload_path)
    truth_sha256 = _sha256_file(truth_path)
    corrected_metadata = _commit_metadata(repo_root, CORRECTED_COMMIT)
    upload_receipts = _validate_upload_manifest(
        _read_json(upload_path),
        corrected_committed_at=corrected_metadata["committed_at"],
    )
    truth_by_receipt, exclusions = _validate_truth_lock(
        _read_json(truth_path),
        upload_sha256=upload_sha256,
        upload_receipts=upload_receipts,
    )
    job_receipts: list[dict[str, Any]] = []
    for receipt in upload_receipts:
        key = _receipt_key(receipt)
        if key in exclusions:
            continue
        locked_rows = truth_by_receipt[key]
        job_receipts.append(
            {
                "image_id": key[0],
                "receipt_id": key[1],
                "merchant": receipt.get("merchant"),
                "truth_rows": [
                    row for row in locked_rows if row["section_type"] != "AMBIGUOUS"
                ],
            }
        )
    labeled_row_count = sum(len(rows) for rows in truth_by_receipt.values())
    ambiguous_row_count = sum(
        row["section_type"] == "AMBIGUOUS"
        for rows in truth_by_receipt.values()
        for row in rows
    )
    return (
        {
            "preregistration_sha256": preregistration_sha256,
            "upload_manifest_sha256": upload_sha256,
            "truth_locked_sha256": truth_sha256,
            "receipt_count": len(upload_receipts),
            "evaluated_receipt_count": len(job_receipts),
            "excluded_receipt_count": len(exclusions),
            "labeled_row_count": labeled_row_count,
            "ambiguous_row_count": ambiguous_row_count,
            "ambiguous_row_proportion": (
                ambiguous_row_count / labeled_row_count if labeled_row_count else 0.0
            ),
            "truth_row_count": sum(len(item["truth_rows"]) for item in job_receipts),
        },
        job_receipts,
    )


def _assert_no_prior_run(artifact_dir: Path) -> None:
    present = sorted(name for name in _OUTPUT_NAMES if (artifact_dir / name).exists())
    if present:
        raise FileExistsError(
            f"run-once refusal: shadow outputs already exist: {present}"
        )


@contextmanager
def _archived_candidate(repo_root: Path, commit: str, parent: Path) -> Iterator[Path]:
    candidate_root = parent / commit[:12]
    candidate_root.mkdir()
    archive_path = parent / f"{commit[:12]}.tar"
    with archive_path.open("wb") as archive:
        subprocess.run(
            ["git", "-C", str(repo_root), "archive", "--format=tar", commit],
            check=True,
            stdout=archive,
        )
    with tarfile.open(archive_path, mode="r") as archive:
        archive.extractall(candidate_root, filter="data")
    try:
        yield candidate_root
    finally:
        archive_path.unlink(missing_ok=True)


def _candidate_environment() -> dict[str, str]:
    allowed_names = {
        "AWS_ACCESS_KEY_ID",
        "AWS_CA_BUNDLE",
        "AWS_CONFIG_FILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_ROLE_ARN",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "HOME",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
    environment = {
        name: value for name, value in os.environ.items() if name in allowed_names
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run_worker(
    *,
    python: Path,
    worker: Path,
    candidate_root: Path,
    name: str,
    commit: str,
    mode: str,
    output: Path,
    job: Path | None = None,
    table: str | None = None,
) -> None:
    command = [
        str(python),
        "-I",
        "-B",
        str(worker),
        "--candidate-root",
        str(candidate_root),
        "--candidate-name",
        name,
        "--candidate-commit",
        commit,
        "--expected-priors-sha256",
        PRIORS_SHA256,
        "--mode",
        mode,
        "--output",
        str(output),
    ]
    if job is not None:
        command.extend(("--job", str(job)))
    if table is not None:
        command.extend(("--table", table))
    subprocess.run(
        command,
        check=True,
        cwd=candidate_root,
        env=_candidate_environment(),
        capture_output=True,
        text=True,
    )


def _verify_candidates(repo_root: Path, python: Path, worker: Path) -> dict[str, Any]:
    verification: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="txinfo-shadow-verify-") as value:
        temporary = Path(value)
        for name, commit, _ in _CANDIDATES:
            metadata = _commit_metadata(repo_root, commit)
            with _archived_candidate(repo_root, commit, temporary) as root:
                output = temporary / f"{name}.json"
                _run_worker(
                    python=python,
                    worker=worker,
                    candidate_root=root,
                    name=name,
                    commit=commit,
                    mode="verify",
                    output=output,
                )
                verification[name] = {
                    **metadata,
                    **_read_json(output),
                }
    return verification


def _metric_estimate(result: Mapping[str, Any], name: str) -> float | None:
    value = result["evaluation"]["metrics"][name]["estimate"]
    return float(value) if value is not None else None


def _metric_passes(result: Mapping[str, Any], name: str, threshold: float) -> bool:
    estimate = _metric_estimate(result, name)
    return estimate is not None and estimate >= threshold


def _metric_delta(
    frozen: Mapping[str, Any], corrected: Mapping[str, Any], name: str
) -> float | None:
    frozen_value = _metric_estimate(frozen, name)
    corrected_value = _metric_estimate(corrected, name)
    if frozen_value is None or corrected_value is None:
        return None
    return corrected_value - frozen_value


def _compare_results(
    frozen: Mapping[str, Any], corrected: Mapping[str, Any]
) -> dict[str, Any]:
    frozen_eval = frozen["evaluation"]
    corrected_eval = corrected["evaluation"]
    snapshots_match = (
        frozen_eval["input_snapshot_sha256"] == corrected_eval["input_snapshot_sha256"]
    )
    frozen_receipts = {item["case"]: item for item in frozen_eval["receipts"]}
    corrected_receipts = {item["case"]: item for item in corrected_eval["receipts"]}
    if set(frozen_receipts) != set(corrected_receipts):
        raise ValueError("candidate result receipt sets differ")
    per_receipt = []
    for case in sorted(frozen_receipts):
        first = frozen_receipts[case]
        second = corrected_receipts[case]
        first_predictions = {
            int(item["row_id"]): str(item["section_type"])
            for item in first["predictions"]
        }
        second_predictions = {
            int(item["row_id"]): str(item["section_type"])
            for item in second["predictions"]
        }
        changed = sum(
            first_predictions.get(row_id) != second_predictions.get(row_id)
            for row_id in set(first_predictions) | set(second_predictions)
        )
        per_receipt.append(
            {
                "case": case,
                "scored": second["scored"],
                "frozen_agreement": first["agreement"],
                "corrected_agreement": second["agreement"],
                "agreement_delta": (
                    None
                    if first["agreement"] is None or second["agreement"] is None
                    else float(second["agreement"]) - float(first["agreement"])
                ),
                "changed_assignment_rows": changed,
                "fragmentation_delta": {
                    field: int(second["fragmentation"][field])
                    - int(first["fragmentation"][field])
                    for field in (
                        "run_count",
                        "single_row_run_count",
                        "strict_island_count",
                        "split_section_type_count",
                        "extra_repeated_runs",
                    )
                },
            }
        )

    corrected_metrics = corrected_eval["metrics"]
    point_gates = {
        name: _metric_passes(corrected, name, threshold)
        for name, threshold in _POINT_THRESHOLDS.items()
    }
    txinfo_truth_rows = int(corrected_metrics["txinfo_recall"]["total"])
    frozen_fragment = frozen_eval["fragmentation"]
    corrected_fragment = corrected_eval["fragmentation"]
    coherence_comparison = {
        "strict_islands": int(corrected_fragment["strict_island_count"])
        <= int(frozen_fragment["strict_island_count"]),
        "receipts_with_split_types": int(
            corrected_fragment["receipts_with_split_section_types"]
        )
        <= int(frozen_fragment["receipts_with_split_section_types"]),
    }
    promotion_checks = {
        "input_snapshots_identical": snapshots_match,
        "all_point_gates_pass": all(point_gates.values()),
        "promotion_grade_txinfo_sample": txinfo_truth_rows
        >= _MINIMUM_TXINFO_TRUTH_ROWS,
    }
    if not snapshots_match:
        corrected_recommendation = "INVALID_COMPARISON_INPUT_SNAPSHOTS_DIFFER"
    elif txinfo_truth_rows < _MINIMUM_TXINFO_TRUTH_ROWS:
        corrected_recommendation = "KEEP_DRAFT_SMOKE_EVIDENCE_ONLY"
    elif not all(point_gates.values()):
        corrected_recommendation = "KEEP_DRAFT_PREREGISTERED_GATE_FAILURE"
    else:
        corrected_recommendation = (
            "ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW_NOT_AUTHORIZED"
        )
    return {
        "schema_version": 1,
        "candidate_commits": {
            "frozen_v4": FROZEN_V4_COMMIT,
            "corrected": CORRECTED_COMMIT,
        },
        "input_snapshots_identical": snapshots_match,
        "metrics": {
            "frozen_v4": frozen_eval["metrics"],
            "corrected": corrected_eval["metrics"],
            "corrected_minus_frozen": {
                name: _metric_delta(frozen, corrected, name)
                for name in _POINT_THRESHOLDS
            },
        },
        "fragmentation": {
            "frozen_v4": frozen_fragment,
            "corrected": corrected_fragment,
            "corrected_minus_frozen": {
                field: int(corrected_fragment[field]) - int(frozen_fragment[field])
                for field in (
                    "run_count",
                    "single_row_run_count",
                    "strict_island_count",
                    "split_section_type_count",
                    "extra_repeated_runs",
                    "receipts_with_split_section_types",
                )
            },
        },
        "per_receipt_deltas": per_receipt,
        "preregistered_promotion_checks": {
            **promotion_checks,
            "point_gates": point_gates,
        },
        "supplemental_report_only": {
            "confidence_intervals_are_gates": False,
            "unassigned_rows_are_a_gate": False,
            "sequence_coherence_is_a_gate": False,
            "coherence_comparison": coherence_comparison,
        },
        "recommendation": {
            "frozen_v4": "DO_NOT_PROMOTE_ARCHITECTURAL_INVARIANT_VIOLATION",
            "corrected": corrected_recommendation,
            "merge_or_deploy_performed": False,
        },
    }


def _format_metric(metric: Mapping[str, Any]) -> str:
    estimate = metric.get("estimate")
    interval = metric.get("ci95")
    if estimate is None or interval is None:
        return "n/a"
    return f"{float(estimate):.4f} [{float(interval[0]):.4f}, {float(interval[1]):.4f}]"


def _confusion_markdown(title: str, matrix: Mapping[str, Any]) -> list[str]:
    truth_labels = list(matrix["truth_labels"])
    predicted_labels = list(matrix["predicted_labels"])
    lines = [f"### {title}", ""]
    lines.append("| Truth \\ Predicted | " + " | ".join(predicted_labels) + " |")
    lines.append("|---|" + "---:|" * len(predicted_labels))
    for truth, values in zip(truth_labels, matrix["matrix"], strict=True):
        lines.append(
            f"| {truth} | " + " | ".join(str(int(value)) for value in values) + " |"
        )
    lines.append("")
    return lines


def _recommendation_markdown(
    comparison: Mapping[str, Any], input_hashes: Mapping[str, Any]
) -> str:
    metrics = comparison["metrics"]
    fragment = comparison["fragmentation"]
    recommendation = comparison["recommendation"]
    lines = [
        "# TRANSACTION_INFO fresh-receipt shadow recommendation",
        "",
        f"Generated: {_now()}",
        "",
        "## Decision",
        "",
        f"- Frozen v4: **{recommendation['frozen_v4']}**.",
        f"- Corrected candidate: **{recommendation['corrected']}**.",
        "- No merge or deployment was performed.",
        "",
        "## Locked inputs",
        "",
        f"- Preregistration SHA-256: `{input_hashes['preregistration_sha256']}`",
        f"- Upload manifest SHA-256: `{input_hashes['upload_manifest_sha256']}`",
        f"- Truth lock SHA-256: `{input_hashes['truth_locked_sha256']}`",
        f"- Manifest receipts: {input_hashes['receipt_count']}",
        f"- Evaluated receipts: {input_hashes['evaluated_receipt_count']}",
        f"- Pre-run excluded receipts: {input_hashes['excluded_receipt_count']}",
        f"- Locked labeled rows: {input_hashes['labeled_row_count']}",
        f"- AMBIGUOUS rows excluded from scoring: {input_hashes['ambiguous_row_count']} "
        f"({float(input_hashes['ambiguous_row_proportion']):.2%})",
        f"- Scored truth rows: {input_hashes['truth_row_count']}",
        "",
        "## Performance (estimate [Wilson 95% CI])",
        "",
        "| Metric | Frozen v4 | Corrected | Corrected - frozen |",
        "|---|---:|---:|---:|",
    ]
    display_names = {
        "overall_agreement": "Overall agreement",
        "items_recall": "ITEMS recall",
        "txinfo_recall": "TXINFO recall",
        "txinfo_precision": "TXINFO precision",
    }
    for name in _POINT_THRESHOLDS:
        delta = metrics["corrected_minus_frozen"][name]
        lines.append(
            f"| {display_names[name]} | {_format_metric(metrics['frozen_v4'][name])} "
            f"| {_format_metric(metrics['corrected'][name])} "
            f"| {'n/a' if delta is None else f'{float(delta):+.4f}'} |"
        )
    lines.extend(
        [
            f"| Unassigned rows | {metrics['frozen_v4']['unassigned']} "
            f"| {metrics['corrected']['unassigned']} | "
            f"{int(metrics['corrected']['unassigned']) - int(metrics['frozen_v4']['unassigned']):+d} |",
            "",
            "Wilson confidence intervals are supplemental and are not gates. "
            "TXINFO results with fewer than 60 true rows are smoke evidence only.",
            "",
            "## Sequence fragmentation and coherence",
            "",
            "Fragmentation uses all predicted rows. `strict_island_count` counts a "
            "single-row B run surrounded by the same A type; split types count a "
            "section type assigned in more than one run. These are diagnostics, "
            "not path-posteriors.",
            "",
            "| Measure | Frozen v4 | Corrected | Delta |",
            "|---|---:|---:|---:|",
        ]
    )
    for field in (
        "run_count",
        "single_row_run_count",
        "strict_island_count",
        "split_section_type_count",
        "extra_repeated_runs",
        "receipts_with_split_section_types",
    ):
        lines.append(
            f"| {field} | {fragment['frozen_v4'][field]} | "
            f"{fragment['corrected'][field]} | "
            f"{int(fragment['corrected_minus_frozen'][field]):+d} |"
        )
    lines.extend(
        [
            "",
            "## Per-receipt deltas",
            "",
            "| Case | Scored | Agreement delta | Changed rows | Run delta | "
            "Strict-island delta | Split-type delta |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in comparison["per_receipt_deltas"]:
        delta = item["agreement_delta"]
        fragment_delta = item["fragmentation_delta"]
        lines.append(
            f"| {item['case']} | {item['scored']} | "
            f"{'n/a' if delta is None else f'{float(delta):+.4f}'} | "
            f"{item['changed_assignment_rows']} | "
            f"{int(fragment_delta['run_count']):+d} | "
            f"{int(fragment_delta['strict_island_count']):+d} | "
            f"{int(fragment_delta['split_section_type_count']):+d} |"
        )
    lines.extend(
        [
            "",
            "## Preregistered promotion checks",
            "",
        ]
    )
    for name, passed in comparison["preregistered_promotion_checks"].items():
        if isinstance(passed, dict):
            for child, child_passed in passed.items():
                lines.append(
                    f"- {'PASS' if child_passed else 'FAIL'}: `{name}.{child}`"
                )
        else:
            lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## TXINFO false positives by truth and merchant", ""])
    for candidate_name, title in (
        ("frozen_v4", "Frozen v4"),
        ("corrected", "Corrected candidate"),
    ):
        lines.extend(
            [f"### {title}", "", "| Truth | Merchant | Count |", "|---|---|---:|"]
        )
        false_positives = metrics[candidate_name][
            "txinfo_false_positives_by_truth_and_merchant"
        ]
        if false_positives:
            for item in false_positives:
                lines.append(
                    f"| {item['truth']} | {item['merchant']} | {item['count']} |"
                )
        else:
            lines.append("| — | — | 0 |")
        lines.append("")
    lines.extend(["", "## Full confusion matrices", ""])
    lines.extend(
        _confusion_markdown(
            "Frozen v4",
            metrics["frozen_v4"]["confusion_matrix"],
        )
    )
    lines.extend(
        _confusion_markdown(
            "Corrected candidate",
            metrics["corrected"]["confusion_matrix"],
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def _run_once(
    *,
    artifact_dir: Path,
    repo_root: Path,
    python: Path,
    worker: Path,
    table: str,
) -> dict[str, Any]:
    if table != DEV_TABLE or table == PROD_TABLE:
        raise ValueError(f"refusing table {table!r}; expected {DEV_TABLE!r}")
    if not artifact_dir.is_dir():
        raise FileNotFoundError(f"artifact directory does not exist: {artifact_dir}")
    _assert_no_prior_run(artifact_dir)
    input_hashes, receipts = _validate_inputs(artifact_dir, repo_root)
    candidate_metadata = {
        name: _commit_metadata(repo_root, commit) for name, commit, _ in _CANDIDATES
    }
    state: dict[str, Any] = {
        "schema_version": 1,
        "status": "started",
        "started_at": _now(),
        "inputs": input_hashes,
        "candidates": {
            name: {**candidate_metadata[name], "attempts": 0, "status": "pending"}
            for name, _, _ in _CANDIDATES
        },
    }
    state_path = artifact_dir / RUN_STATE
    _create_run_state(state_path, state)

    job = {
        "schema_version": 1,
        "truth_lock_validated": True,
        "upload_manifest_sha256": input_hashes["upload_manifest_sha256"],
        "truth_locked_sha256": input_hashes["truth_locked_sha256"],
        "receipts": receipts,
    }
    results: dict[str, dict[str, Any]] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="txinfo-shadow-run-") as value:
            temporary = Path(value)
            job_path = temporary / "locked_job.json"
            _write_json_atomic(job_path, job)
            for name, commit, result_name in _CANDIDATES:
                state["candidates"][name]["attempts"] = 1
                state["candidates"][name]["status"] = "running"
                _write_json_atomic(state_path, state)
                with _archived_candidate(repo_root, commit, temporary) as root:
                    temporary_output = temporary / f"{name}-result.json"
                    _run_worker(
                        python=python,
                        worker=worker,
                        candidate_root=root,
                        name=name,
                        commit=commit,
                        mode="evaluate",
                        output=temporary_output,
                        job=job_path,
                        table=table,
                    )
                    result = _read_json(temporary_output)
                    output_path = artifact_dir / result_name
                    shutil.copyfile(temporary_output, output_path)
                    results[name] = result
                    state["candidates"][name]["status"] = "complete"
                    state["candidates"][name]["result_sha256"] = _sha256_file(
                        output_path
                    )
                    _write_json_atomic(state_path, state)

        comparison = _compare_results(results["frozen_v4"], results["corrected"])
        comparison["input_hashes"] = input_hashes
        comparison["generated_at"] = _now()
        comparison_path = artifact_dir / COMPARISON
        _write_json_atomic(comparison_path, comparison)
        recommendation_path = artifact_dir / RECOMMENDATION
        _write_text_atomic(
            recommendation_path,
            _recommendation_markdown(comparison, input_hashes),
        )
        hashes: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": _now(),
            "inputs": {
                PREREGISTRATION: input_hashes["preregistration_sha256"],
                UPLOAD_MANIFEST: input_hashes["upload_manifest_sha256"],
                TRUTH_LOCKED: input_hashes["truth_locked_sha256"],
            },
            "candidate_commits": {
                name: metadata["commit"]
                for name, metadata in candidate_metadata.items()
            },
            "candidate_trees": {
                name: metadata["tree"] for name, metadata in candidate_metadata.items()
            },
            "priors_sha256": PRIORS_SHA256,
            "outputs": {
                name: _sha256_file(artifact_dir / name)
                for name in (
                    FROZEN_RESULT,
                    CORRECTED_RESULT,
                    COMPARISON,
                    RECOMMENDATION,
                )
            },
        }
        _write_json_atomic(artifact_dir / HASHES, hashes)
        state["status"] = "complete"
        state["completed_at"] = _now()
        state["comparison_sha256"] = hashes["outputs"][COMPARISON]
        state["recommendation_sha256"] = hashes["outputs"][RECOMMENDATION]
        _write_json_atomic(state_path, state)
        return comparison
    except Exception as error:
        state["status"] = "failed_no_rerun"
        state["failed_at"] = _now()
        state["error_type"] = type(error).__name__
        state["error"] = str(error)
        _write_json_atomic(state_path, state)
        raise


def _preflight(artifact_dir: Path, repo_root: Path) -> dict[str, Any]:
    output_presence = {
        name: (artifact_dir / name).exists() for name in sorted(_OUTPUT_NAMES)
    }
    preregistration_sha256 = None
    if (artifact_dir / PREREGISTRATION).is_file() and (
        artifact_dir / PREREGISTRATION_HASH
    ).is_file():
        preregistration_sha256 = _validate_preregistration(artifact_dir)
    return {
        "schema_version": 1,
        "artifact_dir_exists": artifact_dir.is_dir(),
        "preregistration_present": (artifact_dir / PREREGISTRATION).is_file(),
        "preregistration_hash_present": (artifact_dir / PREREGISTRATION_HASH).is_file(),
        "preregistration_sha256": preregistration_sha256,
        "upload_manifest_present": (artifact_dir / UPLOAD_MANIFEST).is_file(),
        "truth_locked_present": (artifact_dir / TRUTH_LOCKED).is_file(),
        "outputs": output_presence,
        "candidates": {
            name: _commit_metadata(repo_root, commit) for name, commit, _ in _CANDIDATES
        },
        "evaluation_started": any(output_presence.values()),
    }


def main() -> int:
    args = _arguments()
    repo_root = args.repo_root.resolve()
    artifact_dir = args.artifact_dir.resolve()
    worker = Path(__file__).resolve().with_name("txinfo_shadow_candidate.py")
    if args.mode == "preflight":
        print(json.dumps(_preflight(artifact_dir, repo_root), indent=2, sort_keys=True))
        return 0
    if args.mode == "verify-candidates":
        result = _verify_candidates(repo_root, args.python.resolve(), worker)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    comparison = _run_once(
        artifact_dir=artifact_dir,
        repo_root=repo_root,
        python=args.python.resolve(),
        worker=worker,
        table=args.table,
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "recommendation": comparison["recommendation"],
                "outputs": sorted(_OUTPUT_NAMES),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
