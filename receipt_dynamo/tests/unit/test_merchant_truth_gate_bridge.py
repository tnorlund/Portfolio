"""Contract tests for the eval->seal gate bridge (contract §7.5 / §7.6)."""

from __future__ import annotations

from typing import Any

import pytest

from receipt_dynamo.data.merchant_truth_gate_bridge import (
    GateBlockedError,
    GateBridgeError,
    bridge_eval_to_gate_results,
)

pytestmark = pytest.mark.unit


def checks(overall: str, **metrics: dict[str, Any]) -> dict[str, Any]:
    """Build an eval-shaped ``checks`` dict with an overall + per-metric."""
    doc: dict[str, Any] = dict(metrics)
    doc["overall"] = overall
    return doc


def test_pass_seals_with_no_gaps() -> None:
    result = bridge_eval_to_gate_results(
        checks(
            "PASS",
            columns={"verdict": "PASS"},
            tokens={"verdict": "PASS"},
        )
    )

    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result["overall"] == "PASS"
    assert result["gaps"] == []
    assert {m["metric"] for m in result["per_metric"]} == {
        "columns",
        "tokens",
    }


def test_pass_with_gaps_seals_and_records_gaps_verbatim() -> None:
    result = bridge_eval_to_gate_results(
        checks(
            "PASS_WITH_GAPS",
            columns={"verdict": "PASS"},
            logo={"verdict": "UNTESTED", "note": "no storefront ink in real"},
            separators={"verdict": "PASS_WITH_GAPS", "score": 0.71},
        )
    )

    # Seals with a passing signal while carrying the gap list.
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result["overall"] == "PASS_WITH_GAPS"

    gaps = {gap["metric"]: gap for gap in result["gaps"]}
    assert set(gaps) == {"logo", "separators"}
    # detail is the metric entry verbatim minus its verdict -- never summarized.
    assert gaps["logo"]["verdict"] == "UNTESTED"
    assert gaps["logo"]["detail"] == {"note": "no storefront ink in real"}
    assert gaps["separators"]["detail"] == {"score": 0.71}


def test_gaps_are_exactly_the_non_pass_subset() -> None:
    result = bridge_eval_to_gate_results(
        checks(
            "PASS_WITH_GAPS",
            columns={"verdict": "PASS"},
            style={"verdict": "PASS_WITH_GAPS"},
            tokens={"verdict": "PASS"},
            separators={"verdict": "SKIPPED"},
            graphics={"verdict": "PASS"},
            logo={"verdict": "UNTESTED"},
            arithmetic={"verdict": "PASS"},
        )
    )

    gap_metrics = {gap["metric"] for gap in result["gaps"]}
    pass_metrics = {
        m["metric"] for m in result["per_metric"] if m["verdict"] == "PASS"
    }
    # gaps == non-PASS subset, and it is disjoint from the PASS metrics.
    assert gap_metrics == {"style", "separators", "logo"}
    assert gap_metrics.isdisjoint(pass_metrics)
    assert len(result["per_metric"]) == 7


def test_fail_blocks_seal_and_carries_gaps_on_exception() -> None:
    with pytest.raises(GateBlockedError) as excinfo:
        bridge_eval_to_gate_results(
            checks(
                "FAIL",
                columns={"verdict": "FAIL", "detail": "cols drift"},
                tokens={"verdict": "PASS"},
            )
        )

    blocked = excinfo.value.gate_results
    assert blocked["status"] == "FAIL"
    assert blocked["passed"] is False
    assert {gap["metric"] for gap in blocked["gaps"]} == {"columns"}


def test_pass_with_gaps_but_empty_gaps_is_a_bridge_error() -> None:
    with pytest.raises(GateBridgeError, match="empty gap list"):
        bridge_eval_to_gate_results(
            checks(
                "PASS_WITH_GAPS",
                columns={"verdict": "PASS"},
                tokens={"verdict": "PASS"},
            )
        )


def test_pass_with_non_pass_metric_is_a_bridge_error() -> None:
    with pytest.raises(GateBridgeError, match="plain pass"):
        bridge_eval_to_gate_results(
            checks(
                "PASS",
                columns={"verdict": "PASS"},
                logo={"verdict": "UNTESTED"},
            )
        )


@pytest.mark.parametrize(
    "overall",
    ["", "pass", "GREEN", "WARN", None],
)
def test_unknown_overall_is_rejected(overall: Any) -> None:
    with pytest.raises(GateBridgeError, match="unknown eval overall"):
        bridge_eval_to_gate_results(
            checks(overall, columns={"verdict": "PASS"})
        )


def test_non_mapping_input_is_rejected() -> None:
    with pytest.raises(GateBridgeError, match="must be a mapping"):
        bridge_eval_to_gate_results(["not", "a", "dict"])  # type: ignore[arg-type]
