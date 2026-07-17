from __future__ import annotations

import pytest

from scripts.txinfo_recall_execute import (
    DEV_TABLE,
    PROD_TABLE,
    canonical_hash,
    transaction_item,
    validate_plan,
)


def section(section_type: str, line_ids: list[int], status: str = "PENDING") -> dict:
    return {
        "PK": "IMAGE#img",
        "SK": f"RECEIPT#00001#SECTION#{section_type}",
        "TYPE": "RECEIPT_SECTION",
        "section_type": section_type,
        "line_ids": line_ids,
        "validation_status": status,
        "model_source": "source",
    }


def valid_plan() -> dict:
    current = section("PAYMENT", [1, 2])
    target = section("PAYMENT", [1])
    tx_target = section("TRANSACTION_INFO", [2])
    per_receipt = {
        "img::1": {
            "image_id": "img",
            "receipt_id": 1,
            "operations": [
                {
                    "op": "modify",
                    "section_type": "PAYMENT",
                    "current_item": current,
                    "current_fingerprint": canonical_hash(current),
                    "target_item": target,
                },
                {
                    "op": "create",
                    "section_type": "TRANSACTION_INFO",
                    "current_item": None,
                    "current_fingerprint": None,
                    "target_item": tx_target,
                },
            ],
        }
    }
    plan = {
        "anomalies": [],
        "per_receipt": per_receipt,
        "summary": {
            "table": DEV_TABLE,
            "anomalies": 0,
            "blocked_receipts": 0,
            "holdout_overlap": 0,
        },
    }
    plan["summary"]["plan_sha256"] = canonical_hash(
        {"anomalies": [], "per_receipt": per_receipt}
    )
    return plan


def test_validate_plan_accepts_exact_dev_sha() -> None:
    plan = valid_plan()
    sha = plan["summary"]["plan_sha256"]
    assert validate_plan(plan, DEV_TABLE, sha) == sha


def test_validate_plan_refuses_production() -> None:
    plan = valid_plan()
    with pytest.raises(ValueError, match="dev-only"):
        validate_plan(plan, PROD_TABLE, None)


def test_validate_plan_requires_pending_txinfo() -> None:
    plan = valid_plan()
    plan["per_receipt"]["img::1"]["operations"][1]["target_item"][
        "validation_status"
    ] = "VALID"
    plan["summary"]["plan_sha256"] = canonical_hash(
        {"anomalies": [], "per_receipt": plan["per_receipt"]}
    )
    with pytest.raises(ValueError, match="target status must be PENDING"):
        validate_plan(plan, DEV_TABLE, None)


def test_validate_plan_can_explicitly_allow_valid_txinfo() -> None:
    plan = valid_plan()
    plan["per_receipt"]["img::1"]["operations"][1]["target_item"][
        "validation_status"
    ] = "VALID"
    plan["summary"]["plan_sha256"] = canonical_hash(
        {"anomalies": [], "per_receipt": plan["per_receipt"]}
    )
    assert (
        validate_plan(
            plan,
            DEV_TABLE,
            None,
            txinfo_target_status="VALID",
        )
        == plan["summary"]["plan_sha256"]
    )


def test_create_transaction_is_guarded_by_absence() -> None:
    operation = valid_plan()["per_receipt"]["img::1"]["operations"][1]
    request = transaction_item(DEV_TABLE, operation)["Put"]
    assert "attribute_not_exists" in request["ConditionExpression"]


def test_modify_transaction_conditions_on_current_fields() -> None:
    operation = valid_plan()["per_receipt"]["img::1"]["operations"][0]
    request = transaction_item(DEV_TABLE, operation)["Put"]
    assert request["Item"]["line_ids"] == {"L": [{"N": "1"}]}
    assert "line_ids" in request["ExpressionAttributeNames"].values()
    assert "validation_status" in request["ExpressionAttributeNames"].values()
