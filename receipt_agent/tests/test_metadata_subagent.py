"""
Unit tests for the metadata subagent.

Tests the auto-resolve logic, pattern detection, and skip logic.
"""

from datetime import UTC, datetime

import pytest
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.metadata_subagent import (
    MetadataWord,
    auto_resolve_metadata_words,
    detect_pattern_type,
    should_skip_for_metadata_evaluation,
)
from receipt_agent.agents.label_evaluator.state import VisualLine, WordContext

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


def _make_metadata_word(
    text: str,
    current_label: str | None = None,
    detected_type: str | None = None,
    place_match: str | None = None,
    line_id: int = 1,
    word_id: int = 1,
) -> MetadataWord:
    """Helper to create a MetadataWord directly."""
    wc = _make_word_context(text, current_label, line_id, word_id)
    return MetadataWord(
        word_context=wc,
        current_label=current_label,
        line_index=0,
        detected_type=detected_type,
        place_match=place_match,
    )


# =============================================================================
# Tests for auto_resolve_metadata_words
# =============================================================================


class TestAutoResolveMetadataWords:
    """Tests for the auto-resolve logic that skips LLM for high-confidence words."""

    def test_place_match_confirms_merchant_name(self):
        """Word labeled MERCHANT_NAME matching Google Places → auto-VALID."""
        mw = _make_metadata_word(
            "Sprouts", current_label="MERCHANT_NAME", place_match="MERCHANT_NAME"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert len(unresolved) == 0
        assert resolved[0][1]["decision"] == "VALID"
        assert "Google Places" in resolved[0][1]["reasoning"]

    def test_place_match_confirms_address(self):
        """Word labeled ADDRESS_LINE matching Google Places → auto-VALID."""
        mw = _make_metadata_word(
            "123", current_label="ADDRESS_LINE", place_match="ADDRESS_LINE"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"

    def test_place_match_confirms_phone(self):
        """Word labeled PHONE_NUMBER matching Google Places → auto-VALID."""
        mw = _make_metadata_word(
            "(555)123-4567",
            current_label="PHONE_NUMBER",
            place_match="PHONE_NUMBER",
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"

    def test_place_match_confirms_website(self):
        """Word labeled WEBSITE matching Google Places → auto-VALID."""
        mw = _make_metadata_word(
            "sprouts.com",
            current_label="WEBSITE",
            place_match="WEBSITE",
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"

    def test_detected_type_confirms_date(self):
        """Word labeled DATE matching date regex → auto-VALID."""
        mw = _make_metadata_word(
            "12/25/2024", current_label="DATE", detected_type="DATE"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"
        assert "format pattern" in resolved[0][1]["reasoning"]

    def test_detected_type_confirms_time(self):
        """Word labeled TIME matching time regex → auto-VALID."""
        mw = _make_metadata_word(
            "14:30", current_label="TIME", detected_type="TIME"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"

    def test_detected_type_confirms_payment(self):
        """Word labeled PAYMENT_METHOD matching payment regex → auto-VALID."""
        mw = _make_metadata_word(
            "VISA", current_label="PAYMENT_METHOD", detected_type="PAYMENT_METHOD"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert resolved[0][1]["decision"] == "VALID"

    def test_unlabeled_word_goes_to_llm(self):
        """Word with no current label → always unresolved (LLM must suggest)."""
        mw = _make_metadata_word(
            "Sprouts", current_label=None, place_match="MERCHANT_NAME"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_no_confirming_signal_goes_to_llm(self):
        """Word with label but no place_match or detected_type → unresolved."""
        mw = _make_metadata_word(
            "Mon-Fri 9-5", current_label="STORE_HOURS"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_label_conflicts_with_place_goes_to_llm(self):
        """Word labeled PHONE_NUMBER but place says MERCHANT_NAME → unresolved."""
        mw = _make_metadata_word(
            "555-1234",
            current_label="PHONE_NUMBER",
            place_match="MERCHANT_NAME",
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_label_conflicts_with_detected_type_goes_to_llm(self):
        """Word labeled TIME but regex says DATE → unresolved."""
        mw = _make_metadata_word(
            "12/25/2024",
            current_label="TIME",
            detected_type="DATE",
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_coupon_always_goes_to_llm(self):
        """COUPON label with no signals → unresolved (rare label)."""
        mw = _make_metadata_word(
            "SAVE20", current_label="COUPON"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_loyalty_id_always_goes_to_llm(self):
        """LOYALTY_ID label with no signals → unresolved (rare label)."""
        mw = _make_metadata_word(
            "MEMBER123", current_label="LOYALTY_ID"
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 0
        assert len(unresolved) == 1

    def test_mixed_batch(self):
        """Mix of auto-resolvable and unresolved words."""
        words = [
            # Auto-VALID: merchant matches place
            _make_metadata_word(
                "Sprouts", current_label="MERCHANT_NAME",
                place_match="MERCHANT_NAME", word_id=1,
            ),
            # Auto-VALID: date matches regex
            _make_metadata_word(
                "12/25/2024", current_label="DATE",
                detected_type="DATE", word_id=2,
            ),
            # Unresolved: store hours, no signal
            _make_metadata_word(
                "Mon-Fri", current_label="STORE_HOURS", word_id=3,
            ),
            # Unresolved: unlabeled
            _make_metadata_word(
                "www.sprouts.com", detected_type="WEBSITE",
                place_match="WEBSITE", word_id=4,
            ),
        ]
        resolved, unresolved = auto_resolve_metadata_words(words)
        assert len(resolved) == 2
        assert len(unresolved) == 2
        # Verify resolved are the right ones
        resolved_texts = {r[0].word_context.word.text for r in resolved}
        assert resolved_texts == {"Sprouts", "12/25/2024"}
        # Verify unresolved are the right ones
        unresolved_texts = {u.word_context.word.text for u in unresolved}
        assert unresolved_texts == {"Mon-Fri", "www.sprouts.com"}

    def test_empty_input(self):
        """Empty list → no resolved, no unresolved."""
        resolved, unresolved = auto_resolve_metadata_words([])
        assert len(resolved) == 0
        assert len(unresolved) == 0

    def test_all_auto_resolved(self):
        """All words have confirming signals → all resolved, none unresolved."""
        words = [
            _make_metadata_word(
                "Sprouts", current_label="MERCHANT_NAME",
                place_match="MERCHANT_NAME", word_id=1,
            ),
            _make_metadata_word(
                "14:30", current_label="TIME",
                detected_type="TIME", word_id=2,
            ),
        ]
        resolved, unresolved = auto_resolve_metadata_words(words)
        assert len(resolved) == 2
        assert len(unresolved) == 0

    def test_all_unresolved(self):
        """No words have confirming signals → none resolved, all unresolved."""
        words = [
            _make_metadata_word(
                "Mon-Fri", current_label="STORE_HOURS", word_id=1,
            ),
            _make_metadata_word(
                "SAVE20", current_label="COUPON", word_id=2,
            ),
        ]
        resolved, unresolved = auto_resolve_metadata_words(words)
        assert len(resolved) == 0
        assert len(unresolved) == 2

    def test_place_match_takes_priority_over_detected_type(self):
        """When both place_match and detected_type match, place_match is used."""
        mw = _make_metadata_word(
            "(555)123-4567",
            current_label="PHONE_NUMBER",
            place_match="PHONE_NUMBER",
            detected_type="PHONE_NUMBER",
        )
        resolved, unresolved = auto_resolve_metadata_words([mw])
        assert len(resolved) == 1
        assert "Google Places" in resolved[0][1]["reasoning"]

    def test_resolved_decision_format(self):
        """Auto-resolved decisions have the expected format."""
        mw = _make_metadata_word(
            "Sprouts", current_label="MERCHANT_NAME",
            place_match="MERCHANT_NAME",
        )
        resolved, _ = auto_resolve_metadata_words([mw])
        decision = resolved[0][1]
        assert decision["decision"] == "VALID"
        assert decision["suggested_label"] is None
        assert decision["confidence"] == "high"
        assert isinstance(decision["reasoning"], str)


# =============================================================================
# Tests for detect_pattern_type
# =============================================================================


class TestDetectPatternType:
    """Tests for regex-based metadata pattern detection."""

    def test_phone_number(self):
        assert detect_pattern_type("(555)123-4567") == "PHONE_NUMBER"
        assert detect_pattern_type("555-123-4567") == "PHONE_NUMBER"
        assert detect_pattern_type("5551234567") == "PHONE_NUMBER"

    def test_date_slash(self):
        assert detect_pattern_type("12/25/2024") == "DATE"
        assert detect_pattern_type("1/5/24") == "DATE"

    def test_date_dash(self):
        assert detect_pattern_type("2024-12-25") == "DATE"

    def test_date_named_month(self):
        assert detect_pattern_type("Dec 25, 2024") == "DATE"
        assert detect_pattern_type("Jan 1 2025") == "DATE"

    def test_time(self):
        assert detect_pattern_type("14:30") == "TIME"
        assert detect_pattern_type("2:30 PM") == "TIME"
        assert detect_pattern_type("12:00:00") == "TIME"

    def test_website(self):
        assert detect_pattern_type("www.sprouts.com") == "WEBSITE"
        assert detect_pattern_type("example.org") == "WEBSITE"

    def test_payment_method(self):
        assert detect_pattern_type("VISA") == "PAYMENT_METHOD"
        assert detect_pattern_type("MASTERCARD") == "PAYMENT_METHOD"
        assert detect_pattern_type("CASH") == "PAYMENT_METHOD"

    def test_no_match(self):
        assert detect_pattern_type("Sprouts") is None
        assert detect_pattern_type("MILK") is None
        assert detect_pattern_type("$12.99") is None


# =============================================================================
# Tests for should_skip_for_metadata_evaluation
# =============================================================================


class TestShouldSkipForMetadataEvaluation:
    """Tests for the pre-filter that removes obvious non-metadata tokens."""

    def test_single_char_skipped(self):
        assert should_skip_for_metadata_evaluation("T") is True
        assert should_skip_for_metadata_evaluation("A") is True

    def test_punctuation_skipped(self):
        assert should_skip_for_metadata_evaluation("-") is True
        assert should_skip_for_metadata_evaluation("...") is True

    def test_stop_words_skipped(self):
        assert should_skip_for_metadata_evaluation("the") is True
        assert should_skip_for_metadata_evaluation("and") is True
        assert should_skip_for_metadata_evaluation("FOR") is True

    def test_two_char_not_skipped(self):
        """Two-char tokens preserved for state abbreviations (CA, TX)."""
        assert should_skip_for_metadata_evaluation("CA") is False
        assert should_skip_for_metadata_evaluation("TX") is False

    def test_normal_words_not_skipped(self):
        assert should_skip_for_metadata_evaluation("Sprouts") is False
        assert should_skip_for_metadata_evaluation("12/25/2024") is False
