"""
Unit tests for the currency subagent.

Tests the various detection functions for currency-related label issues.
"""

from datetime import UTC, datetime

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.currency_subagent import (
    CurrencyWord,
    LineItemRow,
    build_currency_evaluation_prompt,
    convert_to_evaluation_issues,
    identify_line_item_rows,
    parse_currency_evaluation_response,
)
from receipt_agent.agents.label_evaluator.state import VisualLine, WordContext

# Use a fixed valid UUID for testing
TEST_IMAGE_ID = "12345678-1234-4234-8234-123456789abc"


def _make_word(
    text: str,
    line_id: int = 1,
    word_id: int = 1,
    x: float = 0.5,
    y: float = 0.5,
) -> ReceiptWord:
    """Helper to create a ReceiptWord."""
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
    line_id: int = 1,
    word_id: int = 1,
    status: str = "VALID",
) -> ReceiptWordLabel:
    """Helper to create a ReceiptWordLabel."""
    return ReceiptWordLabel(
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status=status,
        timestamp_added=datetime.now(UTC),
        reasoning="Test label",
    )


def _make_word_context(
    text: str,
    label: str | None = None,
    line_id: int = 1,
    word_id: int = 1,
    x: float = 0.5,
    y: float = 0.5,
) -> WordContext:
    """Helper to create a WordContext with optional label."""
    word = _make_word(text, line_id, word_id, x, y)
    lbl = _make_label(label, line_id, word_id) if label else None
    return WordContext(
        word=word,
        current_label=lbl,
        label_history=[lbl] if lbl else [],
        normalized_x=x,
        normalized_y=y,
    )


def _make_visual_line(
    words: list[WordContext], line_index: int = 0
) -> VisualLine:
    """Helper to create a VisualLine."""
    y_center = (
        sum(w.normalized_y for w in words) / len(words) if words else 0.5
    )
    return VisualLine(line_index=line_index, words=words, y_center=y_center)


class TestIdentifyLineItemRows:
    """Tests for identify_line_item_rows function."""

    def test_identifies_row_with_product_name(self):
        """Should identify rows containing PRODUCT_NAME."""
        wc = _make_word_context("MILK 2%", "PRODUCT_NAME")
        line = _make_visual_line([wc])
        rows = identify_line_item_rows([line])
        assert len(rows) == 1
        assert "PRODUCT_NAME" in rows[0].labels

    def test_identifies_row_with_line_total(self):
        """Should identify rows containing LINE_TOTAL."""
        wc = _make_word_context("4.99", "LINE_TOTAL", x=0.8)
        line = _make_visual_line([wc])
        rows = identify_line_item_rows([line])
        assert len(rows) == 1
        assert "LINE_TOTAL" in rows[0].labels

    def test_skips_non_line_item_rows(self):
        """Should skip rows without line item labels."""
        wc = _make_word_context("Sprouts", "MERCHANT_NAME")
        line = _make_visual_line([wc])
        rows = identify_line_item_rows([line])
        assert len(rows) == 0

    def test_collects_currency_words(self):
        """Should collect currency words from identified rows."""
        wc1 = _make_word_context("MILK 2%", "PRODUCT_NAME", word_id=1, x=0.1)
        wc2 = _make_word_context("4.99", "LINE_TOTAL", word_id=2, x=0.8)
        line = _make_visual_line([wc1, wc2])
        rows = identify_line_item_rows([line])
        assert len(rows) == 1
        assert len(rows[0].currency_words) == 1
        assert rows[0].currency_words[0].word.text == "4.99"


@pytest.mark.skip(reason="find_misclassified_currency not implemented yet")
class TestFindMisclassifiedCurrency:
    """Tests for find_misclassified_currency function."""

    def test_detects_phone_number_as_currency(self):
        """Should detect phone numbers incorrectly labeled as currency."""
        pass

    def test_detects_date_as_currency(self):
        """Should detect dates incorrectly labeled as currency."""
        pass

    def test_detects_zip_code_as_currency(self):
        """Should detect ZIP codes incorrectly labeled as currency."""
        pass

    def test_detects_time_as_currency(self):
        """Should detect times incorrectly labeled as currency."""
        pass

    def test_ignores_valid_currency(self):
        """Should not flag valid currency values."""
        pass

    def test_ignores_unlabeled_words(self):
        """Should not flag unlabeled words."""
        pass


@pytest.mark.skip(reason="find_format_anomalies not implemented yet")
class TestFindFormatAnomalies:
    """Tests for find_format_anomalies function."""

    def test_detects_negative_line_total(self):
        """Should detect negative values for LINE_TOTAL."""
        pass

    def test_allows_negative_discount(self):
        """Should allow negative values for DISCOUNT."""
        pass

    def test_allows_negative_refund(self):
        """Should allow negative values for REFUND."""
        pass

    def test_detects_very_large_line_total(self):
        """Should detect unusually large LINE_TOTAL values."""
        pass

    def test_allows_large_grand_total(self):
        """Should allow large values for GRAND_TOTAL."""
        pass

    def test_detects_zero_grand_total(self):
        """Should flag zero GRAND_TOTAL as suspicious."""
        pass


@pytest.mark.skip(reason="find_missing_currency_labels not implemented yet")
class TestFindMissingCurrencyLabels:
    """Tests for find_missing_currency_labels function."""

    def test_detects_unlabeled_currency_on_line_item_row(self):
        """Should detect unlabeled currency values on line item rows."""
        pass

    def test_ignores_already_labeled_currency(self):
        """Should not flag already labeled currency values."""
        pass

    def test_ignores_non_currency_text(self):
        """Should not flag non-currency text."""
        pass


@pytest.mark.skip(reason="find_line_item_math_errors not implemented yet")
class TestFindLineItemMathErrors:
    """Tests for find_line_item_math_errors function."""

    def test_detects_math_error(self):
        """Should detect when QUANTITY × UNIT_PRICE ≠ LINE_TOTAL."""
        pass

    def test_allows_correct_math(self):
        """Should not flag correct math."""
        pass

    def test_allows_rounding_tolerance(self):
        """Should allow small rounding differences."""
        pass


@pytest.mark.skip(reason="find_position_anomalies not implemented yet")
class TestFindPositionAnomalies:
    """Tests for find_position_anomalies function."""

    def test_detects_line_total_on_left(self):
        """Should detect LINE_TOTAL appearing on the left instead of right."""
        pass

    def test_allows_line_total_on_right(self):
        """Should not flag LINE_TOTAL on the right."""
        pass

    def test_no_patterns_no_issues(self):
        """Should not flag anything when no patterns provided."""
        pass


@pytest.mark.skip(
    reason="CurrencyIssue type not implemented - convert_to_evaluation_issues takes different input"
)
class TestConvertToEvaluationIssues:
    """Tests for convert_to_evaluation_issues function."""

    def test_converts_currency_issues(self):
        """Should convert currency decision dicts to EvaluationIssue."""
        pass


class TestParseCurrencyEvaluationResponse:
    """Tests for parse_currency_evaluation_response function."""

    def test_parses_valid_response(self):
        """Should parse valid JSON response."""
        response = """```json
[
    {"index": 0, "decision": "INVALID", "reasoning": "Phone number", "suggested_label": "PHONE_NUMBER", "confidence": "high"},
    {"index": 1, "decision": "VALID", "reasoning": "Correct", "confidence": "medium"}
]
```"""
        reviews = parse_currency_evaluation_response(response, 2)
        assert len(reviews) == 2
        assert reviews[0]["decision"] == "INVALID"
        assert reviews[0]["suggested_label"] == "PHONE_NUMBER"
        assert reviews[1]["decision"] == "VALID"

    def test_handles_missing_indices(self):
        """Should provide fallback for missing indices."""
        response = '[{"index": 0, "decision": "VALID", "reasoning": "OK"}]'
        reviews = parse_currency_evaluation_response(response, 2)
        assert len(reviews) == 2
        assert reviews[0]["decision"] == "VALID"
        assert reviews[1]["decision"] == "NEEDS_REVIEW"  # Fallback

    def test_handles_invalid_json(self):
        """Should return fallbacks for invalid JSON."""
        response = "This is not JSON"
        reviews = parse_currency_evaluation_response(response, 2)
        assert len(reviews) == 2
        assert all(r["decision"] == "NEEDS_REVIEW" for r in reviews)


class TestBuildCurrencyEvaluationPrompt:
    """Tests for build_currency_evaluation_prompt function."""

    def test_builds_prompt_with_currency_words(self):
        """Should build prompt with currency words context."""
        wc = _make_word_context("4.99", "LINE_TOTAL", x=0.8)
        line = _make_visual_line([wc])
        currency_word = CurrencyWord(
            word_context=wc,
            current_label="LINE_TOTAL",
            line_index=0,
            position_zone="right",
            looks_like_currency=True,
            non_currency_pattern=None,
        )
        prompt = build_currency_evaluation_prompt(
            visual_lines=[line],
            currency_words=[currency_word],
            patterns=None,
            merchant_name="Test Merchant",
        )
        assert "Test Merchant" in prompt
        assert "4.99" in prompt

    def test_includes_pattern_context(self):
        """Should include pattern context when available."""
        wc = _make_word_context("4.99", "LINE_TOTAL", x=0.8)
        line = _make_visual_line([wc])
        currency_word = CurrencyWord(
            word_context=wc,
            current_label="LINE_TOTAL",
            line_index=0,
            position_zone="right",
            looks_like_currency=True,
            non_currency_pattern=None,
        )
        patterns = {
            "item_structure": "single-line",
            "label_positions": {"LINE_TOTAL": "right"},
        }
        prompt = build_currency_evaluation_prompt(
            visual_lines=[line],
            currency_words=[currency_word],
            patterns=patterns,
            merchant_name="Test",
        )
        assert "single-line" in prompt
