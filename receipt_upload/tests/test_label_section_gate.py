"""Unit tests for the pure section-to-word-label compatibility gate."""

from __future__ import annotations

from typing import Any

import pytest

from receipt_upload.label_section_gate import (
    VERDICT_ABSTAIN,
    VERDICT_LOW_PRIOR,
    VERDICT_OK,
    GateResult,
    evaluate_label_section,
    load_priors,
)


@pytest.fixture
def synthetic_priors() -> dict[str, Any]:
    """Return a compact synthetic prior artifact for deterministic tests."""
    return {
        "schema_version": 1,
        "sections": [
            "ADDRESS",
            "ITEMS",
            "PAYMENT",
            "SUMMARY",
            "TOTAL_LINE",
            "TRANSACTION_INFO",
        ],
        "labels": {
            "ADDRESS_LINE": {
                "total": 100,
                "sectioned": 90,
                "unsectioned": 10,
                "sections": {
                    "ADDRESS": {"count": 83, "p": 0.923},
                    "SUMMARY": {"count": 7, "p": 0.077},
                },
            },
            "PRODUCT_NAME": {
                "total": 100,
                "sectioned": 100,
                "unsectioned": 0,
                "sections": {
                    "ADDRESS": {"count": 2, "p": 0.02},
                    "ITEMS": {"count": 98, "p": 0.98},
                },
            },
            "DATE": {
                "total": 100,
                "sectioned": 58,
                "unsectioned": 42,
                "sections": {
                    "TRANSACTION_INFO": {"count": 58, "p": 0.8},
                },
            },
            "TAX": {
                "total": 100,
                "sectioned": 80,
                "unsectioned": 20,
                "sections": {
                    "SUMMARY": {"count": 4, "p": 0.05},
                },
            },
        },
    }


def test_high_prior_section_is_ok(
    synthetic_priors: dict[str, Any],
) -> None:
    """Accept a label paired with its high-prior section."""
    result = evaluate_label_section(
        "ADDRESS_LINE",
        ["ADDRESS"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_OK
    assert result.prior == pytest.approx(0.923)
    assert result.reason == "prior-at-or-above-threshold"


def test_absent_canonical_section_prior_is_low(
    synthetic_priors: dict[str, Any],
) -> None:
    """Assign a zero prior when a canonical section cell is absent."""
    result = evaluate_label_section(
        "ADDRESS_LINE",
        ["PAYMENT"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_LOW_PRIOR
    assert result.prior == 0.0
    assert result.reason == "prior-below-threshold"


@pytest.mark.parametrize(
    "section_types",
    [
        None,
        [],
        ["HEADER"],
    ],
)
def test_unsectioned_inputs_abstain(
    synthetic_priors: dict[str, Any],
    section_types: list[str] | None,
) -> None:
    """Abstain for missing, empty, or legacy-only line sections."""
    result = evaluate_label_section(
        "ADDRESS_LINE",
        section_types,
        synthetic_priors,
    )

    assert result.verdict == VERDICT_ABSTAIN
    assert result.prior is None
    assert result.reason == "unsectioned-line"


def test_unknown_label_abstains(
    synthetic_priors: dict[str, Any],
) -> None:
    """Abstain when the artifact does not contain the supplied label."""
    result = evaluate_label_section(
        "UNKNOWN_LABEL",
        ["ADDRESS"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_ABSTAIN
    assert result.prior is None
    assert result.reason == "unknown-label"


def test_insufficient_support_abstains(
    synthetic_priors: dict[str, Any],
) -> None:
    """Abstain when sectioned support is below the default floor of 59."""
    result = evaluate_label_section(
        "DATE",
        ["TRANSACTION_INFO"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_ABSTAIN
    assert result.prior is None
    assert result.reason == "insufficient-support"


def test_multiple_sections_use_maximum_prior(
    synthetic_priors: dict[str, Any],
) -> None:
    """Accept a multi-section line when any canonical section has high prior."""
    result = evaluate_label_section(
        "PRODUCT_NAME",
        ["ADDRESS", "ITEMS"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_OK
    assert result.prior == pytest.approx(0.98)


def test_prior_equal_to_threshold_is_ok(
    synthetic_priors: dict[str, Any],
) -> None:
    """Accept a prior exactly equal to the threshold."""
    result = evaluate_label_section(
        "TAX",
        ["SUMMARY"],
        synthetic_priors,
    )

    assert result.verdict == VERDICT_OK
    assert result.prior == pytest.approx(0.05)


def test_custom_threshold_is_honored(
    synthetic_priors: dict[str, Any],
) -> None:
    """Use a caller-supplied threshold instead of the default."""
    result = evaluate_label_section(
        "ADDRESS_LINE",
        ["ADDRESS"],
        synthetic_priors,
        threshold=0.95,
    )

    assert result.verdict == VERDICT_LOW_PRIOR
    assert result.threshold == pytest.approx(0.95)


def test_custom_minimum_support_is_honored(
    synthetic_priors: dict[str, Any],
) -> None:
    """Use a caller-supplied support floor instead of the default."""
    result = evaluate_label_section(
        "DATE",
        ["TRANSACTION_INFO"],
        synthetic_priors,
        threshold=0.7,
        min_support=1,
    )

    assert result.verdict == VERDICT_OK
    assert result.prior == pytest.approx(0.8)


def test_gate_result_carries_prior_and_threshold(
    synthetic_priors: dict[str, Any],
) -> None:
    """Expose the evaluated prior and threshold in the immutable result."""
    result = evaluate_label_section(
        "PRODUCT_NAME",
        ["ADDRESS"],
        synthetic_priors,
        threshold=0.03,
    )

    assert isinstance(result, GateResult)
    assert result.verdict == VERDICT_LOW_PRIOR
    assert result.prior == pytest.approx(0.02)
    assert result.threshold == pytest.approx(0.03)


def test_packaged_prior_asset_has_loose_invariants() -> None:
    """Integration-lite: verify the packaged artifact is readable and sane."""
    artifact = load_priors()

    assert isinstance(artifact, dict)
    assert "labels" in artifact
    address_line = artifact["labels"]["ADDRESS_LINE"]
    assert address_line["sections"]["ADDRESS"]["p"] > 0.5

    for entry in artifact["labels"].values():
        for section in entry.get("sections", {}).values():
            assert 0.0 <= section["p"] <= 1.0
