"""Base classes and interfaces for pattern detection."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Tuple

from receipt_dynamo.entities import ReceiptWord


class PatternType(Enum):
    """Types of patterns that can be detected in receipts."""

    # Currency patterns
    CURRENCY = auto()
    GRAND_TOTAL = auto()
    SUBTOTAL = auto()
    TAX = auto()
    DISCOUNT = auto()
    UNIT_PRICE = auto()
    LINE_TOTAL = auto()

    # Date/Time patterns
    DATE = auto()
    TIME = auto()
    DATETIME = auto()

    # Contact patterns
    PHONE_NUMBER = auto()
    EMAIL = auto()
    WEBSITE = auto()

    # Quantity patterns
    QUANTITY = auto()
    QUANTITY_AT = auto()  # e.g., "2 @ $5.99"
    QUANTITY_TIMES = auto()  # e.g., "3 x $4.50"
    QUANTITY_FOR = auto()  # e.g., "3 for $15.00"
    
    # Business entity patterns
    MERCHANT_NAME = auto()      # Store/business name
    PRODUCT_NAME = auto()       # Item/product description
    STORE_ADDRESS = auto()      # Business address
    STORE_PHONE = auto()        # Business phone number


@dataclass
class PatternMatch:
    """Represents a pattern match in receipt text."""

    word: ReceiptWord
    pattern_type: PatternType
    confidence: float  # 0.0 to 1.0
    matched_text: str
    extracted_value: (
        Any  # Type depends on pattern (float for currency, str for others)
    )
    metadata: Dict[str, Any]  # Additional pattern-specific data

    def __post_init__(self) -> None:
        """Validate match data."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Confidence must be between 0 and 1, got {self.confidence}"
            )


class PatternDetector(ABC):
    """Abstract base class for pattern detectors."""

    def __init__(self) -> None:
        """Initialize the pattern detector."""
        self._compiled_patterns: Dict[str, Any] = {}
        self._initialize_patterns()

    @abstractmethod
    def _initialize_patterns(self) -> None:
        """Initialize and compile regex patterns for efficiency."""

    @abstractmethod
    async def detect(self, words: List[ReceiptWord]) -> List[PatternMatch]:
        """Detect patterns in the given receipt words.

        Args:
            words: List of receipt words to analyze

        Returns:
            List of pattern matches found
        """

    @abstractmethod
    def get_supported_patterns(self) -> List[PatternType]:
        """Get list of pattern types this detector supports."""

    def _calculate_position_context(
        self, word: ReceiptWord, all_words: List[ReceiptWord]
    ) -> Dict[str, Any]:
        """Calculate positional context for a word.

        Args:
            word: The word to analyze
            all_words: All words in the receipt

        Returns:
            Dictionary with position context (e.g., relative position, nearby keywords)
        """
        if not all_words:
            return {}

        # Calculate relative vertical position (0.0 = top, 1.0 = bottom)
        y_positions = [w.bounding_box["y"] for w in all_words]
        min_y, max_y = min(y_positions), max(y_positions)
        relative_y = (
            (word.bounding_box["y"] - min_y) / (max_y - min_y)
            if max_y > min_y
            else 0.5
        )

        # Find words on the same line (similar y-coordinate)
        line_threshold = 5  # pixels
        same_line_words = [
            w
            for w in all_words
            if abs(w.bounding_box["y"] - word.bounding_box["y"])
            <= line_threshold
        ]

        # Sort words by x-coordinate to determine line position
        same_line_words.sort(key=lambda w: w.bounding_box["x"])

        # Find the current word's position in the line
        line_position = 0
        for i, w in enumerate(same_line_words):
            if w.word_id == word.word_id:
                line_position = i
                break

        # Get text from other words on the same line
        other_line_words = [
            w for w in same_line_words if w.word_id != word.word_id
        ]
        nearby_text = " ".join([w.text for w in other_line_words])

        return {
            "relative_y_position": relative_y,
            "is_bottom_20_percent": relative_y >= 0.8,
            "same_line_text": nearby_text,
            "line_word_count": len(same_line_words),
            "line_position": line_position,
        }

    def _find_nearby_words(
        self,
        word: ReceiptWord,
        all_words: List[ReceiptWord],
        max_distance: float = 50.0,
    ) -> List[Tuple[ReceiptWord, float]]:
        """Find words near the given word.

        Args:
            word: The reference word
            all_words: All words to search through
            max_distance: Maximum pixel distance to consider

        Returns:
            List of (word, distance) tuples sorted by distance
        """
        nearby = []
        word_x, word_y = word.calculate_centroid()

        for other in all_words:
            if other.word_id == word.word_id:
                continue

            other_x, other_y = other.calculate_centroid()
            distance = (
                (other_x - word_x) ** 2 + (other_y - word_y) ** 2
            ) ** 0.5

            if distance <= max_distance:
                nearby.append((other, distance))

        return sorted(nearby, key=lambda x: x[1])
