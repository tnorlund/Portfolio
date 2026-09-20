"""Conflict checks retain text matching, ordering, and review limits."""

import pytest
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.issue_detection import (
    evaluate_word_contexts,
)
from receipt_agent.agents.label_evaluator.state import (
    MerchantPatterns,
    VisualLine,
    WordContext,
)

IMAGE_ID = "12345678-1234-4234-8234-123456789abc"


def _context(
    word_id: int, text: str, label: str | None, y: float = 0.5
) -> WordContext:
    word = ReceiptWord(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=1,
        word_id=word_id,
        text=text,
        bounding_box={"x": 0.1, "y": y, "width": 0.1, "height": 0.02},
        top_left={"x": 0.1, "y": y + 0.02},
        top_right={"x": 0.2, "y": y + 0.02},
        bottom_left={"x": 0.1, "y": y},
        bottom_right={"x": 0.2, "y": y},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )
    current_label = (
        ReceiptWordLabel(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=word_id,
            label=label,
            validation_status="VALID",
            reasoning="Test label",
            timestamp_added="2026-09-10T00:00:00+00:00",
        )
        if label is not None
        else None
    )
    return WordContext(word=word, current_label=current_label, normalized_y=y)


def test_conflicts_use_first_labeled_match_in_receipt_order() -> None:
    contexts = [
        _context(1, "APPLE", None),
        _context(2, "Apple", "MERCHANT_NAME"),
        _context(3, "unrelated", "PRODUCT_NAME"),
        _context(4, "apple", "PRODUCT_NAME"),
        _context(5, "APPLE", "ADDRESS_LINE"),
    ]
    issues = evaluate_word_contexts(contexts, None)
    assert [issue.word.word_id for issue in issues] == [2, 4, 5]
    assert issues[0].reasoning == (
        "'Apple' labeled MERCHANT_NAME at y=0.50, but same text labeled "
        "PRODUCT_NAME at y=0.50 - inconsistent labeling"
    )
    assert all(issue.issue_type == "text_label_conflict" for issue in issues)


def test_text_matching_uses_lower_without_extra_normalization() -> None:
    contexts = [
        _context(1, "straße", "MERCHANT_NAME"),
        _context(2, "STRASSE", "PRODUCT_NAME"),
        _context(3, "Apple", "MERCHANT_NAME"),
        _context(4, " apple ", "PRODUCT_NAME"),
    ]
    assert not evaluate_word_contexts(contexts, None)


@pytest.mark.parametrize("grand_total_y, expected_count", [(0.2, 0), (0.8, 2)])
def test_learned_pairs_keep_spatial_order_checks(
    grand_total_y: float, expected_count: int
) -> None:
    contexts = [
        _context(1, "10.00", "SUBTOTAL", y=0.4),
        _context(2, "10.00", "GRAND_TOTAL", y=grand_total_y),
    ]
    pair = ("GRAND_TOTAL", "SUBTOTAL")
    patterns = MerchantPatterns(
        merchant_name="Test Merchant",
        receipt_count=5,
        value_pairs={pair: 5},
        value_pair_positions={pair: (0.2, 0.4)},
    )
    issues = evaluate_word_contexts(contexts, patterns)
    assert len(issues) == expected_count
    assert all("Spatial ordering" in issue.reasoning for issue in issues)


def test_unlabeled_words_still_receive_same_line_cluster_checks() -> None:
    contexts = [
        _context(1, "123", "ADDRESS_LINE"),
        _context(2, "Main", "ADDRESS_LINE"),
        _context(3, "90210", None),
    ]
    lines = [VisualLine(line_index=0, words=contexts, y_center=0.5)]
    issues = evaluate_word_contexts(contexts, None, lines)
    assert len(issues) == 1
    assert issues[0].word is contexts[2].word
    assert issues[0].issue_type == "missing_label_cluster"
    assert issues[0].suggested_label == "ADDRESS_LINE"


def test_conflict_cap_retains_first_twenty_issues() -> None:
    contexts = [
        _context(
            index + 1,
            "Coffee",
            "PRODUCT_NAME" if index % 2 else "MERCHANT_NAME",
        )
        for index in range(25)
    ]
    issues = evaluate_word_contexts(contexts, None)
    assert [issue.word.word_id for issue in issues] == list(range(1, 21))
