"""
State definitions for the Label Evaluator agent.

This module defines the state schema for the label evaluator workflow,
which validates labels by analyzing spatial patterns within receipts
and across receipts from the same merchant.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities import ReceiptMetadata, ReceiptWord, ReceiptWordLabel


@dataclass
class WordContext:
    """
    Transient context for a word during evaluation.

    Combines a ReceiptWord with its label history and computed spatial context
    for use in validation rules.
    """

    word: ReceiptWord

    # Current label (most recent ReceiptWordLabel by timestamp, if any)
    current_label: Optional[ReceiptWordLabel] = None

    # All historical labels for audit trail awareness
    label_history: List[ReceiptWordLabel] = field(default_factory=list)

    # Computed spatial context
    normalized_y: float = 0.0  # 0=bottom, 1=top (receipt coordinate system)
    normalized_x: float = 0.0  # 0=left, 1=right
    visual_line_index: int = 0  # Index of the visual line this word belongs to
    position_in_line: int = 0  # Position within the visual line (left to right)

    # References to neighbor contexts (populated after all WordContexts created)
    same_line_words: List["WordContext"] = field(default_factory=list)


@dataclass
class VisualLine:
    """
    A row of words grouped by y-coordinate proximity.

    Since OCR line_id can split visual lines (e.g., product description and
    price on the same visual row), this groups words by their actual y-position.
    """

    line_index: int
    words: List[WordContext]
    y_center: float  # Average y of words in this line

    def get_labels(self) -> List[str]:
        """Get all labels present on this visual line."""
        return [
            w.current_label.label for w in self.words if w.current_label is not None
        ]


@dataclass
class MerchantPatterns:
    """
    Patterns learned from other receipts of the same merchant.

    Used to detect anomalies by comparing against expected label positions
    and co-occurrence patterns.
    """

    merchant_name: str
    receipt_count: int

    # Per-label y-position distributions (label -> list of y positions from receipts)
    # Example: {"MERCHANT_NAME": [0.95, 0.93, 0.94], "GRAND_TOTAL": [0.08, 0.10]}
    label_positions: Dict[str, List[float]] = field(default_factory=dict)

    # Text examples per label (for text-based validation)
    # Example: {"MERCHANT_NAME": {"Sprouts", "SPROUTS"}}
    label_texts: Dict[str, set] = field(default_factory=dict)

    # Labels that commonly appear together on same visual line
    # Example: {("PRODUCT_NAME", "LINE_TOTAL"): 47}
    same_line_pairs: Dict[tuple, int] = field(default_factory=dict)


@dataclass
class EvaluationIssue:
    """
    An issue detected during label evaluation.

    Captures all information needed to create a new ReceiptWordLabel
    with the evaluation result.
    """

    issue_type: str  # "position_anomaly", "same_line_conflict", "missing_label_cluster", etc.
    word: ReceiptWord
    current_label: Optional[str]  # The label being evaluated (None if unlabeled)
    suggested_status: str  # "VALID", "INVALID", or "NEEDS_REVIEW"
    reasoning: str  # Human-readable explanation

    # Optional: suggested correction
    suggested_label: Optional[str] = None


@dataclass
class OtherReceiptData:
    """Data fetched from another receipt of the same merchant."""

    metadata: ReceiptMetadata
    words: List[ReceiptWord]
    labels: List[ReceiptWordLabel]


@dataclass
class EvaluatorState:
    """
    Agent state passed through the LangGraph workflow.

    Contains all data needed for evaluation, from input identifiers
    to computed structures to final output.
    """

    # Input: receipt to evaluate
    image_id: str
    receipt_id: int

    # Fetched data for this receipt
    words: List[ReceiptWord] = field(default_factory=list)
    labels: List[ReceiptWordLabel] = field(default_factory=list)
    metadata: Optional[ReceiptMetadata] = None

    # Fetched data from other receipts of same merchant
    other_receipt_data: List[OtherReceiptData] = field(default_factory=list)

    # Computed structures
    word_contexts: List[WordContext] = field(default_factory=list)
    visual_lines: List[VisualLine] = field(default_factory=list)
    merchant_patterns: Optional[MerchantPatterns] = None

    # Output: new labels to write (with evaluation results)
    new_labels: List[ReceiptWordLabel] = field(default_factory=list)

    # Evaluation summary
    issues_found: List[EvaluationIssue] = field(default_factory=list)

    # Error handling
    error: Optional[str] = None
