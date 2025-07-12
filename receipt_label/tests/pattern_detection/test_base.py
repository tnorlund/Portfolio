"""Tests for base pattern detection classes."""

import pytest
from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)

from receipt_dynamo.entities import ReceiptWord


class TestPatternMatch:
    """Test PatternMatch data class."""

    @pytest.fixture
    def sample_word(self):
        """Create a sample receipt word."""
        return ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=1,
            word_id=1,
            text="$5.99",
            bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
            top_left={"x": 0, "y": 0},
            top_right={"x": 50, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 50, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )

    @pytest.mark.unit
    def test_pattern_match_creation(self, sample_word):
        """Test creating a valid PatternMatch."""
        match = PatternMatch(
            word=sample_word,
            pattern_type=PatternType.CURRENCY,
            confidence=0.95,
            matched_text="$5.99",
            extracted_value=5.99,
            metadata={"currency_symbol": "$"},
        )

        assert match.word == sample_word
        assert match.pattern_type == PatternType.CURRENCY
        assert match.confidence == 0.95
        assert match.matched_text == "$5.99"
        assert match.extracted_value == 5.99
        assert match.metadata["currency_symbol"] == "$"

    @pytest.mark.unit
    def test_pattern_match_confidence_validation(self, sample_word):
        """Test that confidence is validated to be between 0 and 1."""
        # Test invalid confidence values
        with pytest.raises(
            ValueError, match="Confidence must be between 0 and 1"
        ):
            PatternMatch(
                word=sample_word,
                pattern_type=PatternType.CURRENCY,
                confidence=1.5,  # Too high
                matched_text="$5.99",
                extracted_value=5.99,
                metadata={},
            )

        with pytest.raises(
            ValueError, match="Confidence must be between 0 and 1"
        ):
            PatternMatch(
                word=sample_word,
                pattern_type=PatternType.CURRENCY,
                confidence=-0.1,  # Too low
                matched_text="$5.99",
                extracted_value=5.99,
                metadata={},
            )

    @pytest.mark.unit
    def test_pattern_match_edge_confidence(self, sample_word):
        """Test edge cases for confidence values."""
        # Test boundary values
        match_low = PatternMatch(
            word=sample_word,
            pattern_type=PatternType.CURRENCY,
            confidence=0.0,  # Minimum valid
            matched_text="$5.99",
            extracted_value=5.99,
            metadata={},
        )
        assert match_low.confidence == 0.0

        match_high = PatternMatch(
            word=sample_word,
            pattern_type=PatternType.CURRENCY,
            confidence=1.0,  # Maximum valid
            matched_text="$5.99",
            extracted_value=5.99,
            metadata={},
        )
        assert match_high.confidence == 1.0


class TestPatternDetector:
    """Test PatternDetector abstract base class."""

    class ConcreteDetector(PatternDetector):
        """Concrete implementation for testing."""

        def _initialize_patterns(self) -> None:
            """Initialize test patterns."""
            self._compiled_patterns = {"test": "pattern"}

        async def detect(self, words):
            """Dummy detect implementation."""
            return []

        def get_supported_patterns(self):
            """Return supported patterns."""
            return [PatternType.CURRENCY]

    @pytest.fixture
    def detector(self):
        """Create a concrete detector instance."""
        return self.ConcreteDetector()

    @pytest.fixture
    def sample_words(self):
        """Create sample receipt words."""
        words = []
        for i in range(5):
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=i // 2,  # Group into lines
                word_id=i,
                text=f"word{i}",
                bounding_box={
                    "x": i * 60,
                    "y": (i // 2) * 25,
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": i * 60, "y": (i // 2) * 25},
                top_right={"x": i * 60 + 50, "y": (i // 2) * 25},
                bottom_left={"x": i * 60, "y": (i // 2) * 25 + 20},
                bottom_right={"x": i * 60 + 50, "y": (i // 2) * 25 + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)
        return words

    @pytest.mark.unit
    def test_detector_initialization(self, detector):
        """Test that detector initializes patterns on creation."""
        assert hasattr(detector, "_compiled_patterns")
        assert detector._compiled_patterns == {"test": "pattern"}

    @pytest.mark.unit
    def test_abstract_methods_required(self):
        """Test that abstract methods must be implemented."""

        class IncompleteDetector(PatternDetector):
            """Incomplete implementation missing required methods."""

            pass

        # Should not be able to instantiate without implementing abstract methods
        with pytest.raises(TypeError):
            IncompleteDetector()

    @pytest.mark.unit
    def test_calculate_position_context(self, detector, sample_words):
        """Test position context calculation."""
        word = sample_words[2]  # Middle word
        context = detector._calculate_position_context(word, sample_words)

        assert "relative_y_position" in context
        assert "is_bottom_20_percent" in context
        assert "same_line_text" in context
        assert "line_word_count" in context
        assert "line_position" in context

        # Check specific values
        assert 0.0 <= context["relative_y_position"] <= 1.0
        assert isinstance(context["is_bottom_20_percent"], bool)
        assert context["line_word_count"] == 2  # Two words on same line
        assert context["line_position"] in [0, 1]  # Position in line

    @pytest.mark.unit
    def test_calculate_position_context_empty_words(
        self, detector, sample_words
    ):
        """Test position context with empty word list."""
        word = sample_words[0]
        context = detector._calculate_position_context(word, [])

        assert context == {}  # Should return empty dict

    @pytest.mark.unit
    def test_calculate_position_context_single_word(self, detector):
        """Test position context with single word."""
        word = ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=1,
            word_id=1,
            text="only",
            bounding_box={"x": 0, "y": 50, "width": 50, "height": 20},
            top_left={"x": 0, "y": 50},
            top_right={"x": 50, "y": 50},
            bottom_left={"x": 0, "y": 70},
            bottom_right={"x": 50, "y": 70},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )

        context = detector._calculate_position_context(word, [word])

        assert (
            context["relative_y_position"] == 0.5
        )  # Only word, middle position
        assert context["line_word_count"] == 1
        assert context["same_line_text"] == ""  # No other words
        assert context["line_position"] == 0

    @pytest.mark.unit
    def test_find_nearby_words(self, detector, sample_words):
        """Test finding nearby words."""
        word = sample_words[2]  # Middle word
        nearby = detector._find_nearby_words(
            word, sample_words, max_distance=100
        )

        # Should find other words sorted by distance
        assert len(nearby) > 0
        assert all(
            w[0].word_id != word.word_id for w in nearby
        )  # Excludes self

        # Check sorting by distance
        distances = [dist for _, dist in nearby]
        assert distances == sorted(distances)

    @pytest.mark.unit
    def test_find_nearby_words_max_distance(self, detector, sample_words):
        """Test max distance filtering."""
        word = sample_words[0]

        # Very small distance - should find few or no words
        nearby_close = detector._find_nearby_words(
            word, sample_words, max_distance=10
        )

        # Large distance - should find more words
        nearby_far = detector._find_nearby_words(
            word, sample_words, max_distance=200
        )

        assert len(nearby_far) >= len(nearby_close)

    @pytest.mark.unit
    def test_find_nearby_words_empty_list(self, detector, sample_words):
        """Test finding nearby words with empty word list."""
        word = sample_words[0]
        nearby = detector._find_nearby_words(word, [], max_distance=100)

        assert nearby == []

    @pytest.mark.unit
    def test_position_context_bottom_detection(self, detector):
        """Test bottom 20% detection in position context."""
        # Create words spread vertically
        words = []
        for i in range(10):
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=i,
                word_id=i,
                text=f"line{i}",
                bounding_box={"x": 0, "y": i * 20, "width": 50, "height": 15},
                top_left={"x": 0, "y": i * 20},
                top_right={"x": 50, "y": i * 20},
                bottom_left={"x": 0, "y": i * 20 + 15},
                bottom_right={"x": 50, "y": i * 20 + 15},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)

        # Test word at top
        top_context = detector._calculate_position_context(words[0], words)
        assert not top_context["is_bottom_20_percent"]

        # Test word at bottom
        bottom_context = detector._calculate_position_context(words[9], words)
        assert bottom_context["is_bottom_20_percent"]

    @pytest.mark.unit
    def test_line_position_ordering(self, detector):
        """Test that line position is correctly ordered by x-coordinate."""
        # Create words on same line with different x positions
        words = []
        x_positions = [100, 0, 50, 150]  # Out of order
        for i, x in enumerate(x_positions):
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=1,
                word_id=i,
                text=f"word{i}",
                bounding_box={"x": x, "y": 50, "width": 40, "height": 20},
                top_left={"x": x, "y": 50},
                top_right={"x": x + 40, "y": 50},
                bottom_left={"x": x, "y": 70},
                bottom_right={"x": x + 40, "y": 70},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)

        # Check each word's line position
        for i, word in enumerate(words):
            context = detector._calculate_position_context(word, words)
            x_pos = x_positions[i]
            expected_position = sorted(x_positions).index(x_pos)
            assert context["line_position"] == expected_position
