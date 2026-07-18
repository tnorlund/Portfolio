"""Synthetic tests for the truth-gated fresh-receipt shadow harness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import txinfo_fresh_shadow as shadow
from scripts import txinfo_shadow_candidate as worker

_COMMITTED_AT = "2026-07-17T13:26:31-07:00"
_IMAGE_ID = "00000000-0000-4000-8000-000000000001"


def _upload_manifest() -> dict[str, Any]:
    return {
        "producer": "claude_a",
        "cohort": "fresh_upload_v1",
        "receipts": [
            {
                "image_id": _IMAGE_ID,
                "receipt_id": 1,
                "merchant": None,
                "uploaded_at": "2026-07-17T14:00:00-07:00",
            }
        ],
    }


def _truth_lock(upload_sha256: str) -> dict[str, Any]:
    return {
        "producer": "claude_b",
        "locked": True,
        "locked_at": "2026-07-17T14:30:00-07:00",
        "upload_manifest_sha256": upload_sha256,
        "receipts": [
            {
                "image_id": _IMAGE_ID,
                "receipt_id": 1,
                "rows": [
                    {"row_id": 1, "section_type": "ITEMS"},
                    {"row_id": 2, "section_type": "TRANSACTION_INFO"},
                ],
            }
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_upload_manifest_requires_claude_a_and_post_freeze_receipts() -> None:
    manifest = _upload_manifest()

    receipts = (
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            manifest, corrected_committed_at=_COMMITTED_AT
        )
    )

    assert len(receipts) == 1
    manifest["producer"] = "someone_else"
    with pytest.raises(ValueError, match="authored by claude_a"):
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            manifest, corrected_committed_at=_COMMITTED_AT
        )


def test_upload_manifest_rejects_pre_freeze_receipt() -> None:
    manifest = _upload_manifest()
    manifest["receipts"][0]["uploaded_at"] = _COMMITTED_AT

    with pytest.raises(ValueError, match="is not fresh"):
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            manifest, corrected_committed_at=_COMMITTED_AT
        )


def test_truth_lock_must_be_claude_b_and_bind_exact_manifest() -> None:
    upload_sha256 = "a" * 64
    receipts = (
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            _upload_manifest(), corrected_committed_at=_COMMITTED_AT
        )
    )
    truth = _truth_lock(upload_sha256)

    normalized, exclusions = (
        shadow._validate_truth_lock(  # pylint: disable=protected-access
            truth,
            upload_sha256=upload_sha256,
            upload_receipts=receipts,
        )
    )

    assert normalized[(_IMAGE_ID, 1)][1]["section_type"] == "TRANSACTION_INFO"
    assert exclusions == {}
    truth["upload_manifest_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="exact upload manifest bytes"):
        shadow._validate_truth_lock(  # pylint: disable=protected-access
            truth,
            upload_sha256=upload_sha256,
            upload_receipts=receipts,
        )


def test_run_once_does_not_create_state_before_truth_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    _write_json(artifact_dir / shadow.UPLOAD_MANIFEST, _upload_manifest())
    monkeypatch.setattr(
        shadow,
        "_commit_metadata",
        lambda _repo, commit: {
            "commit": commit,
            "tree": "tree",
            "committed_at": _COMMITTED_AT,
        },
    )

    with pytest.raises(FileNotFoundError, match="waiting for Claude B"):
        shadow._run_once(  # pylint: disable=protected-access
            artifact_dir=artifact_dir,
            repo_root=tmp_path,
            python=Path("python"),
            worker=Path("worker"),
            table=shadow.DEV_TABLE,
        )

    assert not (artifact_dir / shadow.RUN_STATE).exists()


def test_existing_run_state_prevents_any_second_attempt(
    tmp_path: Path,
) -> None:
    (tmp_path / shadow.RUN_STATE).write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="run-once refusal"):
        shadow._assert_no_prior_run(
            tmp_path
        )  # pylint: disable=protected-access


def test_candidate_environment_drops_python_checkout_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/another/checkout")
    monkeypatch.setenv("PYTHONHOME", "/another/python")
    monkeypatch.setenv("VIRTUAL_ENV", "/another/venv")
    monkeypatch.setenv("AWS_PROFILE", "dev-readonly")

    environment = (
        shadow._candidate_environment()
    )  # pylint: disable=protected-access

    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert environment["AWS_PROFILE"] == "dev-readonly"
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_nested_claude_a_intake_manifest_is_supported() -> None:
    manifest = {
        "role": "claude_a_intake_audit",
        "protocol": "fresh_upload_v1",
        "t0_utc": "2026-07-17T21:07:13Z",
        "images": [
            {
                "image_id": _IMAGE_ID,
                "created_at": "2026-07-17T21:10:00Z",
                "receipt_ids": [1],
            },
            {
                "image_id": "failed-image",
                "created_at": "2026-07-17T21:11:00Z",
                "receipt_ids": [],
                "status": "FAILED",
            },
        ],
    }

    receipts = (
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            manifest, corrected_committed_at=_COMMITTED_AT
        )
    )

    assert [(item["image_id"], item["receipt_id"]) for item in receipts] == [
        (_IMAGE_ID, 1)
    ]


def test_ambiguous_truth_requires_note_and_other_remains_scored() -> None:
    upload_sha256 = "a" * 64
    receipts = (
        shadow._validate_upload_manifest(  # pylint: disable=protected-access
            _upload_manifest(), corrected_committed_at=_COMMITTED_AT
        )
    )
    truth = _truth_lock(upload_sha256)
    truth["receipts"][0]["rows"].extend(
        [
            {
                "row_id": 3,
                "section_type": "AMBIGUOUS",
                "note": "not resolvable from image and OCR",
            },
            {"row_id": 4, "section_type": "OTHER"},
        ]
    )

    normalized, _ = (
        shadow._validate_truth_lock(  # pylint: disable=protected-access
            truth,
            upload_sha256=upload_sha256,
            upload_receipts=receipts,
        )
    )

    assert [row["section_type"] for row in normalized[(_IMAGE_ID, 1)]] == [
        "ITEMS",
        "TRANSACTION_INFO",
        "AMBIGUOUS",
        "OTHER",
    ]


def test_fragmentation_counts_split_types_and_strict_islands() -> None:
    metrics = worker._fragmentation(  # pylint: disable=protected-access
        ["ITEMS", "ITEMS", "PAYMENT", "ITEMS", "SUMMARY"]
    )

    assert metrics == {
        "row_count": 5,
        "run_count": 4,
        "single_row_run_count": 3,
        "strict_island_count": 1,
        "split_section_type_count": 1,
        "split_section_types": ["ITEMS"],
        "extra_repeated_runs": 1,
        "type_contiguous": False,
    }


def test_score_includes_unassigned_rows_and_dense_confusion_matrix() -> None:
    score = worker._score(  # pylint: disable=protected-access
        [
            {"truth": "ITEMS", "predicted": "ITEMS"},
            {"truth": "ITEMS", "predicted": None},
            {"truth": "TRANSACTION_INFO", "predicted": "PAYMENT"},
        ],
        ["ITEMS", "PAYMENT", "TRANSACTION_INFO"],
    )

    assert score["matched"] == 1
    assert score["scored"] == 3
    assert score["unassigned"] == 1
    matrix = score["confusion_matrix"]
    assert matrix["truth_labels"] == ["ITEMS", "PAYMENT", "TRANSACTION_INFO"]
    assert matrix["predicted_labels"][-1] == "__UNASSIGNED__"
    assert len(matrix["matrix"]) == 3
    assert all(len(row) == 4 for row in matrix["matrix"])


def test_wilson_interval_is_bounded_and_preserves_denominator() -> None:
    interval = worker._wilson_interval(
        18, 22
    )  # pylint: disable=protected-access

    assert interval["successes"] == 18
    assert interval["total"] == 22
    assert interval["estimate"] == pytest.approx(18 / 22)
    assert interval["ci95"][0] == pytest.approx(0.6148, abs=0.0001)
    assert interval["ci95"][1] == pytest.approx(0.9269, abs=0.0001)


def _candidate_result(section_types: list[str]) -> dict[str, Any]:
    rows = [
        {
            "truth": section_type,
            "predicted": section_type,
        }
        for section_type in section_types
    ]
    metrics = worker._score(  # pylint: disable=protected-access
        rows, ["ITEMS", "TRANSACTION_INFO"]
    )
    predictions = [
        {"row_id": index, "section_type": section_type, "confidence": 0.9}
        for index, section_type in enumerate(section_types, start=1)
    ]
    fragment = worker._fragmentation(
        section_types
    )  # pylint: disable=protected-access
    return {
        "evaluation": {
            "input_snapshot_sha256": "snapshot",
            "metrics": metrics,
            "fragmentation": {
                **{
                    key: value
                    for key, value in fragment.items()
                    if key != "split_section_types"
                },
                "receipt_count": 1,
                "receipts_with_split_section_types": int(
                    not fragment["type_contiguous"]
                ),
                "type_contiguous_receipts": int(fragment["type_contiguous"]),
                "mean_runs_per_receipt": float(fragment["run_count"]),
            },
            "receipts": [
                {
                    "case": "case00000001",
                    "scored": len(rows),
                    "unassigned": 0,
                    "agreement": 1.0,
                    "fragmentation": fragment,
                    "predictions": predictions,
                }
            ],
        }
    }


def test_comparison_preregisters_strict_promotion_evidence() -> None:
    section_types = ["ITEMS"] * 80 + ["TRANSACTION_INFO"] * 60
    frozen = _candidate_result(section_types)
    corrected = _candidate_result(section_types)

    comparison = shadow._compare_results(  # pylint: disable=protected-access
        frozen, corrected
    )

    assert comparison["preregistered_promotion_checks"] == {
        "input_snapshots_identical": True,
        "all_point_gates_pass": True,
        "promotion_grade_txinfo_sample": True,
        "point_gates": {
            "overall_agreement": True,
            "items_recall": True,
            "txinfo_recall": True,
            "txinfo_precision": True,
        },
    }
    assert comparison["supplemental_report_only"] == {
        "confidence_intervals_are_gates": False,
        "unassigned_rows_are_a_gate": False,
        "sequence_coherence_is_a_gate": False,
        "coherence_comparison": {
            "strict_islands": True,
            "receipts_with_split_types": True,
        },
    }
    assert (
        comparison["recommendation"]["corrected"]
        == "ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW_NOT_AUTHORIZED"
    )


def test_manifest_hash_is_file_bytes_not_reformatted_json(
    tmp_path: Path,
) -> None:
    compact = tmp_path / "manifest.json"
    pretty = tmp_path / "manifest-pretty.json"
    compact.write_text('{"producer":"claude_a"}\n', encoding="utf-8")
    pretty.write_text('{\n  "producer": "claude_a"\n}\n', encoding="utf-8")

    compact_hash = hashlib.sha256(compact.read_bytes()).hexdigest()
    pretty_hash = hashlib.sha256(pretty.read_bytes()).hexdigest()

    assert compact_hash != pretty_hash
    assert (
        shadow._sha256_file(compact) == compact_hash
    )  # pylint: disable=protected-access


def test_locked_preregistration_hash_and_candidate_ids_are_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preregistration = {
        "candidates": {
            "frozen_v4_baseline": {
                "git_commit": shadow.FROZEN_V4_COMMIT,
                "priors_sha256": shadow.PRIORS_SHA256,
            },
            "sequence_invariant_correction": {
                "git_commit": shadow.CORRECTED_COMMIT,
                "priors_sha256": shadow.PRIORS_SHA256,
            },
        }
    }
    preregistration_path = tmp_path / shadow.PREREGISTRATION
    _write_json(preregistration_path, preregistration)
    actual_hash = hashlib.sha256(preregistration_path.read_bytes()).hexdigest()
    (tmp_path / shadow.PREREGISTRATION_HASH).write_text(
        f"{actual_hash}  {preregistration_path}\n", encoding="utf-8"
    )
    monkeypatch.setattr(shadow, "PREREGISTRATION_SHA256", actual_hash)

    assert (
        shadow._validate_preregistration(
            tmp_path
        )  # pylint: disable=protected-access
        == actual_hash
    )
