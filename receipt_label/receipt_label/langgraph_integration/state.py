"""State schema for LangGraph receipt processing workflow.

This module defines the data structure that flows between all nodes
in the LangGraph workflow. Each node receives this state, modifies
relevant fields, and returns the updated state.
"""

from typing import TypedDict, List, Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime


class LabelInfo(TypedDict):
    """Information about a single label assignment."""
    label_type: str  # MERCHANT_NAME, DATE, GRAND_TOTAL, etc.
    confidence: float  # 0.0 to 1.0
    source: str  # "pattern", "gpt", "manual", "position_heuristic"
    validation_status: str  # "pending", "validated", "failed"
    assigned_at: str  # ISO timestamp
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
    gpt_context_building_ms: float
    gpt_labeling_ms: float
    validation_ms: float
    total_processing_ms: float
    
    # Cost tracking
    gpt_prompt_tokens: int
    gpt_completion_tokens: int
    gpt_cost_usd: float
    total_cost_usd: float
    
    # Efficiency metrics
    pattern_coverage: float  # % of words labeled by patterns
    gpt_skip_rate: float  # 1.0 if no GPT needed, 0.0 if GPT used


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
    receipt_id: str  # Unique identifier
    receipt_words: List[Dict[str, Any]]  # OCR words with coordinates
    
    # === Phase 2 Results (pattern detection output) ===
    pattern_results: Dict[str, Any]  # All pattern matches from Phase 2
    currency_columns: List[Dict[str, Any]]  # Detected price columns
    math_solutions: List[Dict[str, Any]]  # Mathematical relationships
    
    # === Merchant Context (loaded in first node) ===
    merchant_metadata: Optional[Dict[str, Any]]  # From DynamoDB
    merchant_name: Optional[str]  # Extracted or provided
    
    # === Labeling Progress ===
    labels: Dict[int, LabelInfo]  # word_id -> label mapping
    missing_essentials: List[str]  # Essential fields not found
    extended_labels_found: Dict[str, int]  # Track tier 2-4 labels
    
    # === GPT Context (only used if needed) ===
    needs_gpt: bool  # Determined by gap analysis
    gpt_prompt: Optional[str]  # Built context for GPT
    gpt_response: Optional[Dict[str, Any]]  # Raw GPT response
    gpt_call_count: int  # Number of GPT calls made
    
    # === Validation Results ===
    validation_results: Dict[str, ValidationResult]  # Multiple validators
    needs_review: bool  # Human review required?
    validation_notes: List[str]  # Explanatory notes
    
    # === Metrics & Monitoring ===
    metrics: ProcessingMetrics
    
    # === Workflow Control ===
    retry_count: int  # Validation retry attempts
    error_messages: List[str]  # Accumulated errors
    using_fallback: bool  # Using fallback strategies?
    
    # === Debugging & Tracing ===
    node_outputs: Dict[str, Any]  # Store intermediate results
    workflow_path: List[str]  # Which nodes were executed


# Additional helper types that might be useful

@dataclass
class PriceColumn:
    """Represents a column of aligned prices from Phase 2."""
    column_id: int
    x_center: float
    x_min: float
    x_max: float
    prices: List[Dict[str, Any]]  # Price matches in this column
    confidence: float
    alignment_score: float


@dataclass 
class MathSolution:
    """Mathematical relationship found by Phase 2."""
    solution_type: str  # "items_to_total", "subtotal_tax_total", etc.
    values: List[float]
    sum_value: float
    confidence: float
    items_indices: List[int]  # Which currency values participate


@dataclass
class SpatialGroup:
    """Group of words with spatial relationship."""
    line_number: int
    words: List[Dict[str, Any]]
    group_type: str  # "potential_line_item", "financial_summary", etc.
    has_price: bool
    x_range: tuple[float, float]
    y_position: float