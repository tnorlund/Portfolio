"""A green fake harness must never masquerade as a real quality result."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from receipt_nutrition.evaluation import ContractFixture, evaluate_contract


def test_independent_arithmetic_cases(fixture_path: Path) -> None:
    result = evaluate_contract(fixture_path)
    assert result["cases_total"] == 35
    assert result["cases_passed"] == 35, result["failures"]
    assert result["contract_passed"] is True
    assert result["real_model_quality"] == "NOT RUN"
    assert result["real_catalog_quality"] == "NOT RUN"
    assert not result["automatic_acceptance_enabled"]


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing_product", "too_few", "wrong_tier", "real_evidence"],
)
def test_fixture_validation(document: dict[str, Any], mutation: str) -> None:
    if mutation == "duplicate":
        document["cases"].append(document["cases"][0])
    elif mutation == "missing_product":
        document["cases"][0]["product_id"] = "absent"
    elif mutation == "too_few":
        document["cases"] = document["cases"][:2]
    elif mutation == "wrong_tier":
        document["evidence_tier"] = "source-backed"
    else:
        document["products"][0]["evidence"][0].update(
            source="manual", verification="user"
        )
    with pytest.raises(ValidationError):
        ContractFixture.model_validate(document)


def test_incorrect_expectation_fails(
    document: dict[str, Any], tmp_path: Path
) -> None:
    document["cases"][0]["expected"]["energy"] = "999"
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(document))
    result = evaluate_contract(path)
    assert result["contract_passed"] is False
    assert result["cases_passed"] == 34
    assert result["failures"][0]["case_id"] == "one-package"


@pytest.mark.parametrize("mode", ["offline", "live"])
def test_unimplemented_evaluation_exits_nonzero(mode: str) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/nutrition_harness/evaluate.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--mode", mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "NOT RUN"
    assert not payload["automatic_acceptance_enabled"]


def test_expectations_accept_exact_output_precision() -> None:
    from receipt_nutrition.evaluation import ExpectedCost

    expected = ExpectedCost(
        quantity_status="known", servings="0.13308088303125"
    )
    assert str(expected.servings) == "0.13308088303125"
