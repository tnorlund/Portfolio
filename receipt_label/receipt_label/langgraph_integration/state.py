"""State schema for LangGraph receipt processing workflow.

This module defines the data structure that flows between all nodes
in the LangGraph workflow. Each node receives this state, modifies
relevant fields, and returns the updated state.

Design Principles:
1. Include only data that changes during workflow execution
2. Reference IDs for large static data (load from DB as needed)
3. Store intermediate results needed by multiple nodes
4. Track all decisions and transformations for auditability
"""

from typing import TypedDict, List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


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
    receipt_id: int  # From DynamoDB
    image_id: str  # UUID from DynamoDB
    receipt_words: List[Dict[str, Any]]  # Minimal OCR word data needed for processing
    # Word dict contains: word_id, line_id, text, confidence, bounding_box, x, y
    
    # === Phase 2 Results (pattern detection output) ===
    pattern_matches: Dict[str, List[Dict[str, Any]]]  # pattern_type -> matches
    # Match dict: {word_id, confidence, extracted_value, metadata}
    
    currency_columns: List[Dict[str, Any]]  # Detected price columns
    # Column dict: {column_id, x_center, prices: [{word_id, value, y}]}
    
    structure_sections: Optional[Dict[str, List[int]]]  # section_name -> line_ids
    # e.g., {"header": [0,1,2], "body": [3-15], "footer": [16-18]}
    
    # === Merchant Context (loaded in first node) ===
    merchant_place_id: Optional[str]  # Google Places ID from DynamoDB
    merchant_name: Optional[str]  # Canonical name from validation
    merchant_patterns: Optional[List[str]]  # Known patterns for this merchant
    # Don't store full metadata - load from DB when needed
    
    # === Labeling Progress ===
    labels: Dict[int, LabelInfo]  # word_id -> label mapping
    grouped_labels: Dict[str, List[int]]  # group_id -> [word_ids] for multi-word
    
    missing_essentials: List[str]  # Essential fields not found
    found_essentials: Dict[str, int]  # label_type -> word_id
    
    # Line item tracking
    line_items: List[Dict[str, Any]]  # Detected line items
    # Item dict: {line_ids, description_words, price_word_id, quantity}
    
    # === GPT Context (only used if needed) ===
    needs_gpt: bool  # Determined by gap analysis
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
    pinecone_updates: List[Dict[str, Any]]  # Vectors to update
    dynamodb_updates: List[Dict[str, Any]]  # Records to write
    
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