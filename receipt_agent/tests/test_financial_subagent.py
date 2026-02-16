"""Unit tests for financial subagent strict structured output behavior."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from receipt_agent.agents.label_evaluator import financial_subagent
from receipt_agent.agents.label_evaluator.financial_subagent import (
    evaluate_financial_math,
)
from receipt_agent.agents.label_evaluator.state import VisualLine, WordContext
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

TEST_IMAGE_ID = "12345678-1234-4234-8234-123456789abc"


def _make_word(
    text: str,
    line_id: int,
    word_id: int,
    x: float = 0.8,
    y: float = 0.5,
) -> ReceiptWord:
    """Create a minimal ReceiptWord for financial tests."""
    return ReceiptWord(
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        top_left={"x": x, "y": y},
        top_right={"x": x + 0.1, "y": y},
        bottom_left={"x": x, "y": y - 0.05},
        bottom_right={"x": x + 0.1, "y": y - 0.05},
        bounding_box={"x": x, "y": y, "width": 0.1, "height": 0.05},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


def _make_label(
    label: str,
    line_id: int,
    word_id: int,
) -> ReceiptWordLabel:
    """Create a minimal ReceiptWordLabel."""
    return ReceiptWordLabel(
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status="VALID",
        timestamp_added=datetime.now(UTC),
        reasoning="test",
    )


def _make_word_context(
    text: str,
    label: str,
    line_id: int,
    word_id: int,
    x: float = 0.8,
    y: float = 0.5,
) -> WordContext:
    """Create a WordContext with a current label."""
    word = _make_word(text=text, line_id=line_id, word_id=word_id, x=x, y=y)
    current_label = _make_label(label=label, line_id=line_id, word_id=word_id)
    return WordContext(
        word=word,
        current_label=current_label,
        label_history=[current_label],
        normalized_x=x,
        normalized_y=y,
    )


def test_strict_mode_skips_text_parsing_fallback(monkeypatch):
    """Strict mode should not use text parsing when structure fails."""
    monkeypatch.setenv("LLM_STRICT_STRUCTURED_OUTPUT", "true")
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT_RETRIES", "1")

    def _no_text_fallback(*args, **kwargs):
        raise AssertionError("text parsing fallback should not be called")

    monkeypatch.setattr(
        financial_subagent,
        "parse_financial_evaluation_response",
        _no_text_fallback,
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.side_effect = RuntimeError("schema failure")
    mock_llm.with_structured_output.return_value = mock_structured

    subtotal_wc = _make_word_context(
        text="10.00",
        label="SUBTOTAL",
        line_id=1,
        word_id=1,
        y=0.9,
    )
    tax_wc = _make_word_context(
        text="1.00",
        label="TAX",
        line_id=2,
        word_id=1,
        y=0.8,
    )
    grand_total_wc = _make_word_context(
        text="20.00",
        label="GRAND_TOTAL",
        line_id=3,
        word_id=1,
        y=0.7,
    )

    visual_lines = [
        VisualLine(line_index=0, words=[subtotal_wc], y_center=0.9),
        VisualLine(line_index=1, words=[tax_wc], y_center=0.8),
        VisualLine(line_index=2, words=[grand_total_wc], y_center=0.7),
    ]

    results = evaluate_financial_math(
        visual_lines=visual_lines,
        llm=mock_llm,
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        merchant_name="Test Merchant",
    )

    assert results
    assert all(r["llm_review"]["decision"] == "NEEDS_REVIEW" for r in results)
    assert any(
        "Strict structured output failed" in r["llm_review"]["reasoning"]
        for r in results
    )
