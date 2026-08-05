"""The label evaluator must never mint a label outside CORE_LABELS.

``_create_evaluation_label`` used to fall back to the sentinel ``"UNKNOWN"``
when an issue had neither a suggested nor a current label, and to carry
``issue.current_label`` forward verbatim. Both put a non-CORE_LABELS string
into a DynamoDB sort key. Since #1291 they also make
``add_receipt_word_labels`` reject the whole transaction, so a single bad
label silently discarded every other decision for the receipt.

The row is now skipped instead.
"""

import pytest
from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_agent.agents.label_evaluator.graph import (
    _create_evaluation_label,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    ReviewResult,
)

IMAGE_ID = "344f4a1b-1476-442e-bb01-7eed30934285"


def _word():
    return ReceiptWord(
        receipt_id=1,
        image_id=IMAGE_ID,
        line_id=10,
        word_id=1,
        text="12.34",
        bounding_box={
            "x": 0.1,
            "y": 0.1,
            "width": 0.1,
            "height": 0.02,
        },
        top_right={"x": 0.2, "y": 0.12},
        top_left={"x": 0.1, "y": 0.12},
        bottom_right={"x": 0.2, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.1},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )


def _issue(current_label=None, suggested_label=None):
    return EvaluationIssue(
        issue_type="position_anomaly",
        word=_word(),
        current_label=current_label,
        suggested_status="NEEDS_REVIEW",
        reasoning="test",
        suggested_label=suggested_label,
    )


@pytest.mark.unit
def test_issue_without_any_label_is_skipped_not_labelled_unknown():
    assert _create_evaluation_label(_issue(), None) is None


@pytest.mark.unit
def test_core_label_is_kept():
    label = _create_evaluation_label(_issue(current_label="GRAND_TOTAL"), None)
    assert label is not None
    assert label.label == "GRAND_TOTAL"


@pytest.mark.unit
def test_known_alias_is_normalized():
    label = _create_evaluation_label(_issue(suggested_label="ADDRESS"), None)
    assert label is not None
    assert label.label == "ADDRESS_LINE"


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed",
    [
        "LINE_TOTAL (SHOULD BE 69.60)",
        "7829.53",
        "UNKNOWN",
    ],
)
def test_legacy_malformed_current_label_is_not_carried_forward(malformed):
    """A malformed label read off a stored row must not be re-minted."""
    assert (
        _create_evaluation_label(_issue(current_label=malformed), None) is None
    )


@pytest.mark.unit
def test_llm_review_path_also_refuses_a_non_core_label():
    issue = _issue(current_label="LINE_TOTAL (SHOULD BE 69.60)")
    review = ReviewResult(
        issue=issue,
        decision="INVALID",
        reasoning="whatever",
        suggested_label=None,
    )
    assert _create_evaluation_label(issue, review) is None


@pytest.mark.unit
def test_llm_review_path_keeps_a_valid_suggestion():
    issue = _issue(current_label="LINE_TOTAL")
    review = ReviewResult(
        issue=issue,
        decision="INVALID",
        reasoning="it is a unit price",
        suggested_label="UNIT_PRICE",
    )
    label = _create_evaluation_label(issue, review)
    assert label is not None
    assert label.label == "UNIT_PRICE"
    assert label.validation_status == "INVALID"
