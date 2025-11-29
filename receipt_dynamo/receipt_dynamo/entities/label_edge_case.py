"""
LabelEdgeCase entity for storing known invalid word/label combinations.

Edge cases are patterns that have been identified as invalid and should be
rejected immediately without expensive LLM validation calls.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str


@dataclass(eq=True, unsafe_hash=False)
class LabelEdgeCase:
    """
    Represents a known invalid word/label combination.

    Can be:
    - Global: applies to all merchants for a label type
    - Merchant-specific: only applies to specific merchant

    Attributes:
        label_type: CORE_LABEL (e.g., "PHONE_NUMBER")
        word_text: The invalid word (e.g., "Main:")
        normalized_word: Normalized version for matching (uppercase, stripped)
        merchant_name: None = global, else merchant-specific
        match_type: "exact", "prefix", "suffix", "contains", "regex"
        pattern: For regex patterns
        reason: Human-readable explanation
        times_identified: Statistics - how many times this edge case correctly rejected a label
        false_positives: Statistics - how many times this rule incorrectly rejected a valid label
        created_at: ISO timestamp when created
        updated_at: ISO timestamp when last updated
        created_by: Who created this edge case (default: "label-harmonizer")
    """

    label_type: str
    word_text: str
    normalized_word: str
    merchant_name: Optional[str] = None
    match_type: str = "exact"  # "exact", "prefix", "suffix", "contains", "regex"
    pattern: Optional[str] = None  # For regex patterns
    reason: str = ""
    times_identified: int = 0
    false_positives: int = 0
    created_at: str = ""
    updated_at: str = ""
    created_by: str = "label-harmonizer"

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        if not isinstance(self.label_type, str) or not self.label_type:
            raise ValueError("label_type must be a non-empty string")
        self.label_type = self.label_type.upper()

        if not isinstance(self.word_text, str):
            raise ValueError("word_text must be a string")
        # word_text can be empty for regex patterns

        if not isinstance(self.normalized_word, str):
            raise ValueError("normalized_word must be a string")
        self.normalized_word = self.normalized_word.upper().strip()

        if self.merchant_name is not None:
            if not isinstance(self.merchant_name, str):
                raise ValueError("merchant_name must be a string or None")
            if not self.merchant_name:
                raise ValueError("merchant_name cannot be empty if provided")

        valid_match_types = ["exact", "prefix", "suffix", "contains", "regex"]
        if self.match_type not in valid_match_types:
            raise ValueError(
                f"match_type must be one of {valid_match_types}, got {self.match_type}"
            )

        if self.match_type == "regex" and not self.pattern:
            raise ValueError("pattern is required when match_type is 'regex'")

        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")

        if not isinstance(self.times_identified, int) or self.times_identified < 0:
            raise ValueError("times_identified must be a non-negative integer")

        if not isinstance(self.false_positives, int) or self.false_positives < 0:
            raise ValueError("false_positives must be a non-negative integer")

        # Set timestamps if not provided
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        elif isinstance(self.created_at, datetime):
            self.created_at = self.created_at.isoformat()
        elif isinstance(self.created_at, str):
            # Validate it's a valid ISO format
            try:
                datetime.fromisoformat(self.created_at)
            except ValueError as e:
                raise ValueError(
                    "created_at string must be in ISO format"
                ) from e

        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()
        elif isinstance(self.updated_at, datetime):
            self.updated_at = self.updated_at.isoformat()
        elif isinstance(self.updated_at, str):
            # Validate it's a valid ISO format
            try:
                datetime.fromisoformat(self.updated_at)
            except ValueError as e:
                raise ValueError(
                    "updated_at string must be in ISO format"
                ) from e

        if not isinstance(self.created_by, str) or not self.created_by:
            raise ValueError("created_by must be a non-empty string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the label edge case.

        Returns:
            dict: The primary key for the label edge case.
        """
        # Build SK based on whether it's merchant-specific
        if self.merchant_name:
            sk = (
                f"LABEL#{self.label_type}#MERCHANT#{self.merchant_name}"
                f"#WORD#{self.normalized_word}"
            )
        else:
            sk = f"LABEL#{self.label_type}#WORD#{self.normalized_word}"

        return {
            "PK": {"S": "CONFIG#EDGE_CASES"},
            "SK": {"S": sk},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the LabelEdgeCase object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the LabelEdgeCase object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            "TYPE": {"S": "LABEL_EDGE_CASE"},
            "label_type": {"S": self.label_type},
            "word_text": (
                {"S": self.word_text} if self.word_text else {"NULL": True}
            ),
            "normalized_word": {"S": self.normalized_word},
            "match_type": {"S": self.match_type},
            "reason": {"S": self.reason},
            "times_identified": {"N": str(self.times_identified)},
            "false_positives": {"N": str(self.false_positives)},
            "created_at": {"S": self.created_at},
            "updated_at": {"S": self.updated_at},
            "created_by": {"S": self.created_by},
        }

        # Add optional fields
        if self.merchant_name:
            item["merchant_name"] = {"S": self.merchant_name}
        else:
            item["merchant_name"] = {"NULL": True}

        if self.pattern:
            item["pattern"] = {"S": self.pattern}
        else:
            item["pattern"] = {"NULL": True}

        return item

    def matches(self, word: str, merchant: Optional[str] = None) -> bool:
        """Check if this edge case matches a word.

        Args:
            word: The word text to check
            merchant: Optional merchant name for merchant-specific edge cases

        Returns:
            True if this edge case matches the word
        """
        # Check merchant if this is merchant-specific
        if self.merchant_name and merchant != self.merchant_name:
            return False

        normalized = word.strip().upper()
        word_to_match = self.normalized_word

        if self.match_type == "exact":
            return normalized == word_to_match
        elif self.match_type == "prefix":
            return normalized.startswith(word_to_match)
        elif self.match_type == "suffix":
            return normalized.endswith(word_to_match)
        elif self.match_type == "contains":
            return word_to_match in normalized
        elif self.match_type == "regex":
            import re

            pattern = self.pattern or word_to_match
            return bool(re.match(pattern, normalized))
        return False

    def __repr__(self) -> str:
        """Returns a string representation of the LabelEdgeCase object."""
        merchant_str = (
            f"merchant={self.merchant_name}" if self.merchant_name else "global"
        )
        return (
            f"LabelEdgeCase("
            f"label_type={_repr_str(self.label_type)}, "
            f"word_text={_repr_str(self.word_text)}, "
            f"{merchant_str}, "
            f"match_type={_repr_str(self.match_type)}, "
            f"times_identified={self.times_identified})"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the LabelEdgeCase object's attributes."""
        yield "label_type", self.label_type
        yield "word_text", self.word_text
        yield "normalized_word", self.normalized_word
        yield "merchant_name", self.merchant_name
        yield "match_type", self.match_type
        yield "pattern", self.pattern
        yield "reason", self.reason
        yield "times_identified", self.times_identified
        yield "false_positives", self.false_positives
        yield "created_at", self.created_at
        yield "updated_at", self.updated_at
        yield "created_by", self.created_by

    def __eq__(self, other) -> bool:
        """Determines whether two LabelEdgeCase objects are equal."""
        if not isinstance(other, LabelEdgeCase):
            return NotImplemented
        return (
            self.label_type == other.label_type
            and self.normalized_word == other.normalized_word
            and self.merchant_name == other.merchant_name
            and self.match_type == other.match_type
            and self.pattern == other.pattern
        )

    def __hash__(self) -> int:
        """Returns the hash value of the LabelEdgeCase object."""
        return hash(
            (
                self.label_type,
                self.normalized_word,
                self.merchant_name,
                self.match_type,
                self.pattern,
            )
        )


def item_to_label_edge_case(item: Dict[str, Any]) -> LabelEdgeCase:
    """Converts a DynamoDB item to a LabelEdgeCase object.

    Args:
        item: The DynamoDB item to convert.

    Returns:
        LabelEdgeCase: The LabelEdgeCase object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "label_type",
        "normalized_word",
        "match_type",
        "reason",
        "created_at",
        "updated_at",
        "created_by",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}"
        )

    # Extract values
    label_type = item["label_type"]["S"]
    normalized_word = item["normalized_word"]["S"]

    word_text = None
    if "word_text" in item:
        if "S" in item["word_text"]:
            word_text = item["word_text"]["S"]
        elif "NULL" in item["word_text"]:
            word_text = ""

    merchant_name = None
    if "merchant_name" in item:
        if "S" in item["merchant_name"]:
            merchant_name = item["merchant_name"]["S"]
        elif "NULL" in item["merchant_name"]:
            merchant_name = None

    match_type = item["match_type"]["S"]

    pattern = None
    if "pattern" in item:
        if "S" in item["pattern"]:
            pattern = item["pattern"]["S"]
        elif "NULL" in item["pattern"]:
            pattern = None

    reason = item["reason"]["S"]
    times_identified = int(item.get("times_identified", {}).get("N", "0"))
    false_positives = int(item.get("false_positives", {}).get("N", "0"))
    created_at = item["created_at"]["S"]
    updated_at = item["updated_at"]["S"]
    created_by = item["created_by"]["S"]

    return LabelEdgeCase(
        label_type=label_type,
        word_text=word_text or "",
        normalized_word=normalized_word,
        merchant_name=merchant_name,
        match_type=match_type,
        pattern=pattern,
        reason=reason,
        times_identified=times_identified,
        false_positives=false_positives,
        created_at=created_at,
        updated_at=updated_at,
        created_by=created_by,
    )

