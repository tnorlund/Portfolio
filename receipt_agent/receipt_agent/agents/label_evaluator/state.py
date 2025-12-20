"""
State definitions for the Label Evaluator agent.

This module defines the state schema for the label evaluator workflow,
which validates labels by analyzing spatial patterns within receipts
and across receipts from the same merchant.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import (
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)


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
    position_in_line: int = (
        0  # Position within the visual line (left to right)
    )

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
            w.current_label.label
            for w in self.words
            if w.current_label is not None
        ]


@dataclass
class GeometricRelationship:
    """Geometric relationship between two label types on a receipt."""

    # Angle in degrees (0-360) from label_a centroid to label_b centroid
    # 0° = directly right, 90° = directly down, 180° = directly left, 270° = directly up
    angle: float

    # Euclidean distance between centroids (normalized 0-1 scale)
    distance: float


@dataclass
class LabelPairGeometry:
    """Statistics about geometric relationships between two label types."""

    # List of observed (angle, distance) pairs from receipts
    observations: List[GeometricRelationship] = field(default_factory=list)

    # Mean angle and distance (for quick comparison)
    mean_angle: Optional[float] = None
    mean_distance: Optional[float] = None

    # Standard deviations (for outlier detection)
    std_angle: Optional[float] = None
    std_distance: Optional[float] = None

    # Cartesian coordinate statistics (for improved anomaly detection)
    # dx = distance * cos(angle), dy = distance * sin(angle)
    mean_dx: Optional[float] = None
    mean_dy: Optional[float] = None
    std_dx: Optional[float] = None
    std_dy: Optional[float] = None

    # Cartesian distance-from-mean metrics (more robust than polar)
    mean_deviation: Optional[float] = (
        None  # Mean distance of observations from mean point
    )
    std_deviation: Optional[float] = None  # Std dev of those distances


@dataclass
class LabelRelativePosition:
    """Position of a label relative to its constellation centroid."""

    # Mean offset from constellation centroid
    mean_dx: float = 0.0
    mean_dy: float = 0.0

    # Standard deviation of offsets
    std_dx: float = 0.0
    std_dy: float = 0.0

    # Combined deviation metric (for threshold comparison)
    std_deviation: float = 0.0


@dataclass
class ConstellationGeometry:
    """
    Statistics about geometric relationships within a label constellation (n-tuple).

    A constellation is a group of labels (e.g., MERCHANT_NAME, ADDRESS_LINE, PHONE_NUMBER)
    that frequently appear together and form a consistent spatial pattern.

    Unlike pairwise geometry which only captures A↔B relationships, constellation
    geometry captures the holistic structure of the group, enabling detection of:
    - One label being displaced while others are correctly positioned
    - Cluster stretching/compression
    - Missing labels from expected groups
    """

    # The labels in this constellation, sorted for consistent lookup
    # Example: ("ADDRESS_LINE", "MERCHANT_NAME", "PHONE_NUMBER")
    labels: Tuple[str, ...] = field(default_factory=tuple)

    # Number of receipts this constellation was observed in
    observation_count: int = 0

    # Relative positions from constellation centroid for each label
    # Key: label name, Value: LabelRelativePosition
    relative_positions: Dict[str, LabelRelativePosition] = field(
        default_factory=dict
    )

    # Bounding box statistics (normalized 0-1 coordinates)
    mean_width: Optional[float] = None
    mean_height: Optional[float] = None
    std_width: Optional[float] = None
    std_height: Optional[float] = None

    # Aspect ratio (width/height) - useful for detecting stretched constellations
    mean_aspect_ratio: Optional[float] = None
    std_aspect_ratio: Optional[float] = None

    # Constellation centroid position (where the group typically appears on receipt)
    mean_centroid_x: Optional[float] = None
    mean_centroid_y: Optional[float] = None
    std_centroid_x: Optional[float] = None
    std_centroid_y: Optional[float] = None


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

    # Label pairs that share the same value (learned from receipt patterns)
    # Example: {("SUBTOTAL", "GRAND_TOTAL"): 42, ("LINE_TOTAL", "SUBTOTAL"): 3}
    # Used to identify valid co-occurring values (e.g., no-tax receipts) vs errors
    value_pairs: Dict[tuple, int] = field(default_factory=dict)

    # Y-position relationships for label pairs
    # Example: {("SUBTOTAL", "GRAND_TOTAL"): (0.28, 0.15)} - SUBTOTAL at y=0.28, GRAND_TOTAL at y=0.15
    # Helps validate spatial ordering (GRAND_TOTAL should be below/after SUBTOTAL)
    value_pair_positions: Dict[tuple, tuple] = field(default_factory=dict)

    # Geometric relationships between label pairs
    # Example: {("ADDRESS_LINE", "UNIT_PRICE"): LabelPairGeometry(...)}
    # Tracks angle and distance between label centroids across receipts
    label_pair_geometry: Dict[tuple, LabelPairGeometry] = field(
        default_factory=dict
    )

    # All label pairs observed across receipts (for unexpected pair detection)
    # This is a complete set, unlike label_pair_geometry which is limited to top 4
    all_observed_pairs: Set[Tuple[str, str]] = field(default_factory=set)

    # Label types that appear multiple times on the same line (tracked from training data)
    # Example: {"PRODUCT_NAME"} if we see multiple products on the same line
    # This helps distinguish between normal multiplicity and errors
    labels_with_same_line_multiplicity: Set[str] = field(default_factory=set)

    # Batch-specific pattern learning (added 2025-12-18)
    # Separates receipts by data quality and learns specialized patterns
    batch_classification: Dict[str, int] = field(
        default_factory=lambda: {"HAPPY": 0, "AMBIGUOUS": 0, "ANTI_PATTERN": 0}
    )  # Count of receipts in each batch

    # Geometric patterns learned from HAPPY batch (high confidence, conflict-free receipts)
    # Use strictest thresholds (1.5σ) for evaluation
    happy_label_pair_geometry: Dict[tuple, LabelPairGeometry] = field(
        default_factory=dict
    )

    # Geometric patterns learned from AMBIGUOUS batch (format variations)
    # Use moderate thresholds (2.0σ) for evaluation
    ambiguous_label_pair_geometry: Dict[tuple, LabelPairGeometry] = field(
        default_factory=dict
    )

    # Geometric patterns learned from ANTI_PATTERN batch (problematic receipts)
    # Use lenient thresholds (3.0σ) or flag for review
    anti_label_pair_geometry: Dict[tuple, LabelPairGeometry] = field(
        default_factory=dict
    )

    # Constellation geometry for n-tuple label groups (n >= 3)
    # Example: {("ADDRESS_LINE", "MERCHANT_NAME", "PHONE_NUMBER"): ConstellationGeometry(...)}
    # Captures holistic spatial relationships within label groups
    constellation_geometry: Dict[Tuple[str, ...], "ConstellationGeometry"] = (
        field(default_factory=dict)
    )


@dataclass
class EvaluationIssue:
    """
    An issue detected during label evaluation.

    Captures all information needed to create a new ReceiptWordLabel
    with the evaluation result.
    """

    issue_type: str  # "position_anomaly", "same_line_conflict", "missing_label_cluster", etc.
    word: ReceiptWord
    current_label: Optional[
        str
    ]  # The label being evaluated (None if unlabeled)
    suggested_status: str  # "VALID", "INVALID", or "NEEDS_REVIEW"
    reasoning: str  # Human-readable explanation

    # Optional: suggested correction
    suggested_label: Optional[str] = None

    # Reference to WordContext for building review context
    word_context: Optional["WordContext"] = None


@dataclass
class ReviewContext:
    """
    Context provided to the LLM for reviewing a flagged issue.

    Contains all information needed for the LLM to make a semantic decision
    about whether the label is correct.
    """

    # The issue being reviewed
    word_text: str
    current_label: Optional[str]
    issue_type: str
    evaluator_reasoning: str

    # Receipt context
    receipt_text: str  # Full receipt in reading order, target word marked with [brackets]
    visual_line_text: str  # The line containing the word
    visual_line_labels: List[str]  # Labels of other words on same line

    # Label history for this word
    label_history: List[Dict[str, Any]]

    # Merchant
    merchant_name: str


@dataclass
class ReviewResult:
    """
    Result from the LLM review of a flagged issue.
    """

    # The original issue
    issue: EvaluationIssue

    # LLM decision
    decision: str  # "VALID", "INVALID", or "NEEDS_REVIEW"
    reasoning: str  # LLM's explanation
    suggested_label: Optional[str] = None  # If INVALID, what should it be?

    # Whether LLM review succeeded
    review_completed: bool = True
    review_error: Optional[str] = None


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

    # LLM review results
    review_results: List["ReviewResult"] = field(default_factory=list)

    # Configuration
    skip_llm_review: bool = (
        False  # If True, skip LLM review and use evaluator results directly
    )
    skip_geometry: bool = (
        False  # If True, skip expensive geometric anomaly detection
    )
    skip_merchant_patterns: bool = (
        False  # If True, skip all merchant pattern learning
    )
    max_receipts: Optional[int] = (
        None  # Max other receipts to fetch (None = use default)
    )

    # Error handling
    error: Optional[str] = None
