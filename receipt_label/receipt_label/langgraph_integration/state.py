"""State schema for LangGraph receipt processing workflow.

This module defines the data structure that flows between all nodes
in the LangGraph workflow. Each node receives this state, modifies
relevant fields, and returns the updated state.

Design Principles:
1. Include only data that changes during workflow execution
2. Reference IDs for large static data (load from DB as needed)
3. Store intermediate results needed by multiple nodes
4. Track all decisions and transformations for auditability

This aligns with:
- PR #223: Smart Decision Engine with 94.4% skip rate
- PR #221: Spatial/mathematical currency detection
"""

from typing import TypedDict, List, Dict, Optional, Any, Tuple, Set
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto


class DecisionOutcome(Enum):
    """Decision engine outcomes from PR #221."""
    SKIP = "skip"  # Skip GPT, patterns sufficient
    BATCH = "batch"  # Use batch API (non-urgent)
    REQUIRED = "required"  # Use GPT immediately


class ConfidenceLevel(Enum):
    """Confidence levels for decisions."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LabelInfo(TypedDict):
    """Information about a single label assignment.
    
    Aligns with receipt_word_label entity from receipt_dynamo.
    """
    label: str  # MERCHANT_NAME, DATE, GRAND_TOTAL, etc.
    confidence: float  # 0.0 to 1.0
    source: str  # "pattern", "gpt", "manual", "position_heuristic"
    validation_status: Optional[str]  # "pending", "validated", "failed", "needs_review"
    assigned_at: str  # ISO timestamp
    reasoning: Optional[str]  # Why this label was assigned
    proposed_by: Optional[str]  # System that proposed it
    group_id: Optional[str]  # For grouping multi-word labels


class ValidationResult(TypedDict):
    """Result of a validation check."""
    passed: bool
    errors: List[str]
    warnings: List[str]
    checked_at: str  # ISO timestamp


class ProcessingMetrics(TypedDict):
    """Performance and cost metrics for the processing run."""
    # Timing (milliseconds)
    pattern_detection_ms: float
    decision_engine_ms: float
    gpt_context_building_ms: float
    gpt_labeling_ms: float
    validation_ms: float
    total_processing_ms: float
    
    # Cost tracking
    gpt_prompt_tokens: int
    gpt_completion_tokens: int
    gpt_cost_usd: float
    pinecone_queries: int
    pinecone_cost_usd: float
    total_cost_usd: float
    
    # Efficiency metrics
    pattern_coverage: float  # % of words labeled by patterns
    gpt_skip_rate: float  # 1.0 if no GPT needed, 0.0 if GPT used
    batch_api_eligible: bool  # Could use cheaper batch API?


class PatternMatchResult(TypedDict):
    """Pattern detection result from Phase 2."""
    word_id: int
    pattern_type: str  # PatternType enum value
    confidence: float
    extracted_value: Any  # float for currency, str for others
    matched_text: str
    metadata: Dict[str, Any]


class PriceColumnInfo(TypedDict):
    """Spatial currency column from PR #221."""
    column_id: int
    x_center: float
    x_min: float
    x_max: float
    price_count: int
    prices: List[Dict[str, Any]]  # [{word_id, value, y_position}]
    confidence: float
    alignment_score: Optional[float]


class MathSolutionInfo(TypedDict):
    """Mathematical relationship from PR #221."""
    solution_type: str  # "items_to_total", "subtotal_tax_total"
    item_word_ids: List[int]  # Word IDs that sum to total
    sum_value: float
    total_word_id: int
    confidence: float


class FourFieldSummary(TypedDict):
    """Essential fields from PR #221 decision engine."""
    merchant_name_found: bool
    date_found: bool
    time_found: bool
    grand_total_found: bool
    
    merchant_name_value: Optional[str]
    date_value: Optional[str]
    time_value: Optional[str]
    grand_total_value: Optional[float]
    
    merchant_name_confidence: float
    date_confidence: float
    time_confidence: float
    grand_total_confidence: float


class ReceiptProcessingState(TypedDict):
    """Complete state that flows through the LangGraph workflow.
    
    This is the core data structure. Each node in the workflow:
    1. Receives this state as input
    2. Reads the fields it needs
    3. Updates/adds fields based on its processing
    4. Returns the modified state
    
    The state accumulates data as it flows through the workflow.
    """
    
    # === Input Data (provided at workflow start) ===
    receipt_id: int  # From DynamoDB Receipt entity
    image_id: str  # UUID from DynamoDB Receipt entity
    receipt_words: List[Dict[str, Any]]  # Minimal ReceiptWord data
    # Word dict contains: word_id, line_id, text, confidence, bounding_box, x, y
    
    # === Phase 2 Results (pattern detection output) ===
    pattern_matches: Dict[str, List[PatternMatchResult]]  # pattern_type -> matches
    currency_columns: List[PriceColumnInfo]  # Spatial price columns
    math_solutions: List[MathSolutionInfo]  # Mathematical relationships
    
    # Structure analysis results
    structure_sections: Optional[Dict[str, List[int]]]  # section_name -> line_ids
    # e.g., {"header": [0,1,2], "body": [3-15], "footer": [16-18]}
    
    # Four field summary for decision making
    four_field_summary: Optional[FourFieldSummary]
    
    # === Merchant Context (loaded from DynamoDB) ===
    # From ReceiptMetadata entity
    merchant_place_id: Optional[str]  # Google Places ID
    merchant_name: Optional[str]  # Canonical name from validation
    merchant_validation_status: Optional[str]  # MATCHED, UNSURE, NO_MATCH
    merchant_matched_fields: Optional[List[str]]  # ["name", "phone", "address"]
    
    # Merchant-specific patterns from Pinecone
    merchant_patterns: Optional[List[str]]  # Known patterns for this merchant
    
    # === Labeling Progress ===
    labels: Dict[int, LabelInfo]  # word_id -> label mapping
    grouped_labels: Dict[str, List[int]]  # group_id -> [word_ids] for multi-word
    
    # Essential field tracking (MERCHANT_NAME, DATE, TIME, GRAND_TOTAL)
    missing_essentials: List[str]  # Essential fields not found
    found_essentials: Dict[str, int]  # label_type -> word_id
    
    # Line item tracking
    line_items: List[Dict[str, Any]]  # Detected line items
    # Item dict: {line_ids, description_words, price_word_id, quantity}
    
    # Coverage statistics from decision engine
    total_words: int
    labeled_words: int
    unlabeled_meaningful_words: int
    coverage_percentage: float
    
    # === Decision Engine Results ===
    decision_outcome: Optional[DecisionOutcome]  # SKIP, BATCH, or REQUIRED
    decision_confidence: Optional[ConfidenceLevel]  # HIGH, MEDIUM, LOW
    decision_reasoning: Optional[str]
    skip_rate: Optional[float]  # Percentage of receipts that can skip GPT
    
    # === GPT Context (only used if decision != SKIP) ===
    needs_gpt: bool  # Determined by decision engine
    gpt_context_type: Optional[str]  # "essential_gaps" or "line_items"
    gpt_spatial_context: Optional[List[Dict[str, Any]]]  # Relevant receipt regions
    gpt_responses: List[Dict[str, Any]]  # All GPT responses (for retry logic)
    # Response dict: {prompt, response, tokens, cost, timestamp}
    
    # === Validation Results ===
    validation_results: Dict[str, ValidationResult]  # Multiple validators
    needs_review: bool  # Human review required?
    validation_notes: List[str]  # Explanatory notes
    
    # === Metrics & Monitoring ===
    metrics: ProcessingMetrics
    
    # === External System Updates ===
    # DynamoDB updates (ReceiptWordLabel entities)
    label_updates: List[Dict[str, Any]]  # New/updated labels to write
    # Dict: {image_id, receipt_id, line_id, word_id, label, reasoning, validation_status}
    
    # Pinecone metadata updates
    pinecone_updates: List[Dict[str, Any]]  # Word metadata to update
    # Dict: {word_id, valid_labels: [], invalid_labels: []}
    
    # === Workflow Control ===
    current_phase: str  # "pattern", "essential", "extended", "validation"
    retry_count: int  # Validation retry attempts
    max_retries: int  # Configurable retry limit
    
    # === Error Handling ===
    errors: List[Dict[str, Any]]  # {node, error, timestamp, recoverable}
    warnings: List[str]  # Non-fatal issues
    
    # === Audit Trail ===
    decisions: List[Dict[str, Any]]  # Record of all labeling decisions
    # Decision dict: {node, action, reasoning, timestamp}
    node_timings: Dict[str, float]  # node_name -> duration_ms


# Re-export commonly used types for convenience
__all__ = [
    "ReceiptProcessingState",
    "LabelInfo",
    "ValidationResult",
    "ProcessingMetrics",
    "DecisionOutcome",
    "ConfidenceLevel",
    "PatternMatchResult",
    "PriceColumnInfo",
    "MathSolutionInfo",
    "FourFieldSummary",
    "DecisionResult",
    "AlignedLineItem",
]

@dataclass
class DecisionResult:
    """Result from the decision engine (PR #221)."""
    action: DecisionOutcome  # SKIP, BATCH, or REQUIRED
    confidence: ConfidenceLevel  # HIGH, MEDIUM, LOW
    reasoning: str
    
    # Statistics used in decision
    essential_fields_found: Set[str]
    essential_fields_missing: Set[str]
    total_words: int
    labeled_words: int
    unlabeled_meaningful_words: int
    coverage_percentage: float
    
    # Merchant context
    merchant_name: Optional[str]
    merchant_reliability_score: Optional[float]
    
    # Performance metrics
    decision_time_ms: Optional[float]
    pinecone_queries_made: int


@dataclass
class AlignedLineItem:
    """Line item with spatial alignment from PR #221."""
    product_text: str
    product_word_ids: List[int]
    price_word_id: int
    product_line: int
    price_line: int
    line_distance: int
    alignment_confidence: float
    column_id: int
    has_indented_description: bool = False
    description_lines: Optional[List[str]] = None