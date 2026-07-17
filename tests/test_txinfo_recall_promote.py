from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from txinfo_recall_execute import DEV_TABLE  # noqa: E402
from txinfo_recall_promote import build_plan, validate_evidence  # noqa: E402


def pending_section() -> dict:
    return {
        "PK": "IMAGE#img",
        "SK": "RECEIPT#00001#SECTION#TRANSACTION_INFO",
        "TYPE": "RECEIPT_SECTION",
        "section_type": "TRANSACTION_INFO",
        "line_ids": [1, 2],
        "validation_status": "PENDING",
        "model_source": "verified",
    }


def test_validate_evidence_requires_both_gates() -> None:
    summary = {
        "structural_anomalies": 0,
        "plan_target_mismatches": 0,
        "promotion_ready_structurally": True,
    }
    adjudication = {
        "promotion_assessment": {
            "ready": True,
            "structural_anomalies": 0,
            "plan_target_mismatches": 0,
        }
    }
    validate_evidence(summary, adjudication)
    adjudication["promotion_assessment"]["ready"] = False
    with pytest.raises(ValueError, match="not promotion-ready"):
        validate_evidence(summary, adjudication)


def test_build_plan_only_changes_validation_status() -> None:
    section = pending_section()
    plan = build_plan(
        [section], table=DEV_TABLE, region="us-east-1", evidence_sha256="evidence"
    )
    operation = plan["per_receipt"]["img::1"]["operations"][0]
    current = operation["current_item"]
    target = operation["target_item"]
    assert current["validation_status"] == "PENDING"
    assert target["validation_status"] == "VALID"
    assert {**target, "validation_status": "PENDING"} == current


def test_build_plan_blocks_non_pending_sections() -> None:
    section = pending_section()
    section["validation_status"] = "VALID"
    plan = build_plan(
        [section], table=DEV_TABLE, region="us-east-1", evidence_sha256="evidence"
    )
    assert plan["summary"]["blocked_receipts"] == 1
    assert not plan["per_receipt"]
