"""Unit tests for financial subagent strict structured output behavior."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from receipt_agent.agents.label_evaluator import financial_subagent
from receipt_agent.agents.label_evaluator.financial_subagent import (
    FinancialValue,
    _split_at_price_pattern,
    check_subtotal_math,
    evaluate_financial_math,
    filter_junk_values,
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


# =============================================================================
# _split_at_price_pattern tests
# =============================================================================


def _fv(label: str, value: float, line_index: int = 0, text: str = "") -> FinancialValue:
    """Shortcut to build a FinancialValue without needing a real WordContext."""
    return FinancialValue(
        word_context=None,
        label=label,
        numeric_value=value,
        line_index=line_index,
        word_text=text or f"{value:.2f}",
    )


class TestSplitAtPricePattern:
    """Tests for _split_at_price_pattern()."""

    def test_valid_1digit_qty(self):
        # "2019.51" → qty=2, price=19.51 (raw value 2019.51 > 500)
        result = _split_at_price_pattern("2019.51")
        assert result == (2, 19.51)

    def test_valid_2digit_qty(self):
        # "1219.99" → qty=12, price=19.99 (raw value 1219.99 > 500)
        result = _split_at_price_pattern("1219.99")
        assert result == (12, 19.99)

    def test_1digit_takes_priority(self):
        # "3150.00" → 1-digit: qty=3, price=150.00 (preferred over 2-digit)
        result = _split_at_price_pattern("3150.00")
        assert result == (3, 150.00)

    def test_below_threshold_noop(self):
        # "499.99" is below $500, should not trigger
        result = _split_at_price_pattern("499.99")
        assert result is None

    def test_non_decimal_noop(self):
        # "2019" has no decimal → not a price pattern
        result = _split_at_price_pattern("2019")
        assert result is None

    def test_single_penny_noop(self):
        # "500.1" has only one decimal digit → not \d+\.\d{2}
        result = _split_at_price_pattern("500.1")
        assert result is None

    def test_qty_one_rejected(self):
        # "1500.00" → qty=1, but qty must be ≥ 2
        result = _split_at_price_pattern("1500.00")
        assert result is None

    def test_currency_symbol_stripped(self):
        result = _split_at_price_pattern("$2019.51")
        assert result == (2, 19.51)


# =============================================================================
# filter_junk_values — LINE_TOTAL > SUBTOTAL guard
# =============================================================================


class TestFilterJunkValuesLineTotal:
    """Tests for filter_junk_values() LINE_TOTAL > SUBTOTAL guard."""

    def test_phantom_line_totals_dropped(self):
        """LINE_TOTALs exceeding SUBTOTAL should be removed."""
        values = {
            "SUBTOTAL": [_fv("SUBTOTAL", 173.47)],
            "LINE_TOTAL": [
                _fv("LINE_TOTAL", 8.97, line_index=0, text="8.97"),
                _fv("LINE_TOTAL", 400.95, line_index=1, text="400.95"),  # phantom
                _fv("LINE_TOTAL", 12.50, line_index=2, text="12.50"),
            ],
        }
        result = filter_junk_values(values)
        lt_values = [fv.numeric_value for fv in result["LINE_TOTAL"]]
        assert 400.95 not in lt_values
        assert 8.97 in lt_values
        assert 12.50 in lt_values

    def test_no_subtotal_passthrough(self):
        """Without SUBTOTAL, all LINE_TOTALs should pass through."""
        values = {
            "LINE_TOTAL": [
                _fv("LINE_TOTAL", 8.97, line_index=0),
                _fv("LINE_TOTAL", 400.95, line_index=1),
            ],
        }
        result = filter_junk_values(values)
        assert len(result["LINE_TOTAL"]) == 2

    def test_equal_line_total_passes(self):
        """LINE_TOTAL equal to SUBTOTAL should not be dropped."""
        values = {
            "SUBTOTAL": [_fv("SUBTOTAL", 50.00)],
            "LINE_TOTAL": [
                _fv("LINE_TOTAL", 50.00, line_index=0),
            ],
        }
        result = filter_junk_values(values)
        assert len(result["LINE_TOTAL"]) == 1

    def test_zero_subtotal_no_guard(self):
        """Zero SUBTOTAL should not trigger the guard (guarded by > 0)."""
        values = {
            "SUBTOTAL": [_fv("SUBTOTAL", 0.00)],
            "GRAND_TOTAL": [_fv("GRAND_TOTAL", 10.00)],
            "LINE_TOTAL": [
                _fv("LINE_TOTAL", 400.95, line_index=0),
            ],
        }
        result = filter_junk_values(values)
        # Zero SUBTOTAL is dropped by existing filter 2, but LINE_TOTAL
        # should not be affected by the guard since subtotal was 0.
        assert len(result["LINE_TOTAL"]) == 1


# =============================================================================
# filter_junk_values — N@price UNIT_PRICE correction
# =============================================================================


class TestFilterJunkValuesNAtPrice:
    """Tests for filter_junk_values() N@price UNIT_PRICE correction."""

    def test_corrects_unit_price(self):
        """2019.51 should be corrected to unit_price=19.51."""
        values = {
            "UNIT_PRICE": [
                _fv("UNIT_PRICE", 2019.51, line_index=5, text="2019.51"),
            ],
        }
        result = filter_junk_values(values)
        assert len(result["UNIT_PRICE"]) == 1
        assert result["UNIT_PRICE"][0].numeric_value == 19.51

    def test_injects_quantity(self):
        """N@price correction should inject a synthetic QUANTITY."""
        values = {
            "UNIT_PRICE": [
                _fv("UNIT_PRICE", 2019.51, line_index=5, text="2019.51"),
            ],
        }
        result = filter_junk_values(values)
        assert "QUANTITY" in result
        qty_on_line5 = [
            fv for fv in result["QUANTITY"] if fv.line_index == 5
        ]
        assert len(qty_on_line5) == 1
        assert qty_on_line5[0].numeric_value == 2.0

    def test_no_duplicate_quantity(self):
        """Should not inject QUANTITY if one already exists on that line."""
        values = {
            "UNIT_PRICE": [
                _fv("UNIT_PRICE", 2019.51, line_index=5, text="2019.51"),
            ],
            "QUANTITY": [
                _fv("QUANTITY", 2.0, line_index=5, text="2"),
            ],
        }
        result = filter_junk_values(values)
        qty_on_line5 = [
            fv for fv in result["QUANTITY"] if fv.line_index == 5
        ]
        assert len(qty_on_line5) == 1

    def test_normal_unit_price_unchanged(self):
        """Normal UNIT_PRICE values (below threshold) should pass through."""
        values = {
            "UNIT_PRICE": [
                _fv("UNIT_PRICE", 19.51, line_index=5, text="19.51"),
            ],
        }
        result = filter_junk_values(values)
        assert result["UNIT_PRICE"][0].numeric_value == 19.51


# =============================================================================
# Integration: check_subtotal_math after filtering
# =============================================================================


class TestCheckSubtotalMathWithFiltering:
    """check_subtotal_math() should pass after phantom LINE_TOTALs are filtered."""

    def test_subtotal_matches_after_filtering(self):
        """With phantom LINE_TOTAL removed, subtotal math should balance."""
        values = {
            "SUBTOTAL": [_fv("SUBTOTAL", 21.47)],
            "LINE_TOTAL": [
                _fv("LINE_TOTAL", 8.97, line_index=0),
                _fv("LINE_TOTAL", 400.95, line_index=1),  # phantom
                _fv("LINE_TOTAL", 12.50, line_index=2),
            ],
        }
        # Before filtering — math does not balance
        issue_before = check_subtotal_math(values)
        assert issue_before is not None
        assert issue_before.issue_type == "SUBTOTAL_MISMATCH"

        # After filtering — phantom removed, math balances
        filtered = filter_junk_values(values)
        issue_after = check_subtotal_math(filtered)
        assert issue_after is None
