# Phase 3: State Schema Design Decisions

## Overview

The LangGraph state schema is the core data structure that flows between all nodes in the workflow. This document explains what data should be included in the state vs. loaded on demand.

**Updated**: Aligned with PR #221 (Spatial/Mathematical Currency Detection) and PR #223 (Smart Decision Engine with 94.4% Skip Rate)

## Design Principles

### 1. **Minimal State Principle**
Only include data that:
- Changes during workflow execution
- Is needed by multiple nodes
- Represents decisions or transformations
- Is required for audit/debugging

### 2. **Reference Over Embed**
- Store IDs, not full objects
- Load heavy data (embeddings, full OCR) only when needed
- Keep state size under Lambda limits

### 3. **Transformation Tracking**
- Record all labeling decisions
- Track which nodes made changes
- Maintain audit trail for debugging

## What's IN the State

### Essential Input Data
```python
# Identifiers (from DynamoDB Receipt entity)
receipt_id: int  # Receipt ID within image
image_id: str   # UUID identifying the image

# Minimal OCR data for processing (from ReceiptWord entity)
receipt_words: List[Dict[str, Any]]
# Each word dict contains only:
# - word_id, line_id, text
# - confidence, bounding_box
# - x, y coordinates
# Full ReceiptWord entity loaded from DB when needed
```

### Pattern Detection Results (from PR #221 & #223)
```python
# Pattern matches organized by type
pattern_matches: Dict[str, List[PatternMatchResult]]
# PatternMatchResult contains:
# - word_id, pattern_type, confidence
# - extracted_value (float for currency, str for others)
# - matched_text, metadata

# Spatial currency columns (PR #221)
currency_columns: List[PriceColumnInfo]
# PriceColumnInfo contains:
# - column_id, x_center, x_min, x_max
# - prices: List[{word_id, value, y_position}]
# - confidence, alignment_score

# Mathematical relationships (PR #221)
math_solutions: List[MathSolutionInfo]
# MathSolutionInfo contains:
# - solution_type ("items_to_total", "subtotal_tax_total")
# - item_word_ids, sum_value, total_word_id
# - confidence

# Four field summary (PR #223 decision engine)
four_field_summary: FourFieldSummary
# Tracks essential fields: merchant_name, date, time, grand_total
# With found/value/confidence for each
```

### Labeling Progress
```python
# Current label assignments (aligns with ReceiptWordLabel entity)
labels: Dict[int, LabelInfo]
# LabelInfo contains:
# - label (uppercase, e.g., "MERCHANT_NAME")
# - confidence, source ("pattern", "gpt", "manual")
# - validation_status ("pending", "validated", "needs_review")
# - reasoning, proposed_by, assigned_at
# - group_id (for multi-word labels)

grouped_labels: Dict[str, List[int]]  # Multi-word groups

# Essential field tracking (MERCHANT_NAME, DATE, TIME, GRAND_TOTAL)
missing_essentials: List[str]
found_essentials: Dict[str, int]

# Coverage statistics (from decision engine)
total_words: int
labeled_words: int
unlabeled_meaningful_words: int
coverage_percentage: float

# Line items with spatial alignment
line_items: List[Dict]  # AlignedLineItem structures
```

### Decision Engine & Workflow Control
```python
# Decision engine results (PR #223)
decision_outcome: DecisionOutcome  # SKIP, BATCH, or REQUIRED
decision_confidence: ConfidenceLevel  # HIGH, MEDIUM, LOW
decision_reasoning: str
skip_rate: float  # Target: 94.4%

# GPT context (only if decision != SKIP)
needs_gpt: bool
gpt_context_type: str  # "essential_gaps" or "line_items"
gpt_spatial_context: List[Dict]  # Relevant receipt regions
gpt_responses: List[Dict]  # All responses for retry logic

# Current workflow state
current_phase: str  # "pattern", "essential", "extended", "validation"
needs_review: bool  # Human review required?

# Metrics with cost tracking
metrics: ProcessingMetrics
# Includes: pattern_detection_ms, decision_engine_ms
# gpt_cost_usd, pinecone_cost_usd, total_cost_usd
# pattern_coverage, gpt_skip_rate, batch_api_eligible

# Audit trail
decisions: List[Dict]  # All labeling decisions
errors: List[Dict]  # With recoverable flag
node_timings: Dict[str, float]  # Performance tracking
```

## What's NOT in the State (Load on Demand)

### 1. **Full Receipt Data**
```python
# NOT stored:
receipt.raw_s3_bucket  # Load if needed
receipt.cdn_s3_keys    # Load if needed
receipt.sha256         # Load if needed
```

### 2. **Full Merchant Metadata**
```python
# Store only (from ReceiptMetadata entity):
merchant_place_id: str  # Google Places ID
merchant_name: str  # Canonical name
merchant_validation_status: str  # MATCHED, UNSURE, NO_MATCH
merchant_matched_fields: List[str]  # ["name", "phone", "address"]

# Load from DynamoDB when needed:
- Full address
- Phone numbers  
- Business hours
- Category details
- Canonical merchant info
- Validation reasoning
```

### 3. **Embeddings**
```python
# Never store in state
# Query Pinecone directly when needed
```

### 4. **Historical Data**
```python
# Don't store:
- Previous receipt analyses
- Historical patterns
- Past validations

# Load from DynamoDB/Pinecone as needed
```

### 5. **Large Analysis Results**
```python
# Don't store full objects:
ReceiptStructureAnalysis  # 100+ fields
ReceiptLineItemAnalysis   # Complex nested data

# Extract only what's needed:
structure_sections: Dict  # Just section boundaries
line_items: List[Dict]    # Just detected items
```

## Tool Call Storage

**Question**: Do all tool calls get saved to state?

**Answer**: No, but we track their effects:

### What We Track
```python
# Decision tracking
decisions: List[Dict[str, Any]]
# Example: {
#   "node": "pattern_labeling",
#   "action": "assigned_label",
#   "word_id": 5,
#   "label": "GRAND_TOTAL",
#   "reasoning": "Largest value at bottom",
#   "timestamp": "2024-01-15T10:30:00Z"
# }

# Performance tracking
node_timings: Dict[str, float]
# Example: {"load_merchant": 125.5, "pattern_labeling": 89.2}

# Error tracking
errors: List[Dict[str, Any]]
# Example: {
#   "node": "gpt_labeling",
#   "error": "Rate limit exceeded",
#   "timestamp": "2024-01-15T10:31:00Z",
#   "recoverable": true
# }

# External system updates
label_updates: List[Dict]  # DynamoDB ReceiptWordLabel entities
pinecone_updates: List[Dict]  # Pinecone metadata updates
```

### What We Don't Track
- Raw tool inputs/outputs
- Intermediate calculations
- Temporary variables
- Debug logs

## State Size Considerations

### Lambda Limits
- Max payload: 6MB (synchronous)
- Max memory: 10GB
- Typical state size: <100KB

### Size Estimates
```python
# Typical receipt state size:
receipt_words: ~20KB (500 words)
labels: ~5KB (50 labels)
pattern_matches: ~10KB
metrics: ~1KB
decisions: ~10KB
-------------------
Total: ~50KB (well under limits)
```

## Loading Data On Demand

### Pattern for Loading External Data
```python
async def load_merchant_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Load merchant data from DynamoDB"""
    
    # Use ID from state
    if state.get("merchant_place_id"):
        # Load full data (not stored in state)
        merchant_data = await load_from_dynamodb(state["merchant_place_id"])
        
        # Extract only what's needed for state
        state["merchant_name"] = merchant_data.merchant_name
        state["merchant_patterns"] = merchant_data.common_patterns
        
        # Use full data locally, don't store
        validate_merchant_fields(merchant_data, state["receipt_words"])
    
    return state
```

### Pattern for Querying Embeddings
```python
async def similarity_search_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Query similar receipts from Pinecone"""
    
    # Build query from state
    query_text = build_query(state["merchant_name"], state["found_essentials"])
    
    # Query Pinecone (results not stored in state)
    similar_receipts = await pinecone_client.query(
        text=query_text,
        filter={"merchant_name": state["merchant_name"]},
        top_k=5
    )
    
    # Extract insights, don't store raw results
    common_labels = extract_common_labels(similar_receipts)
    state["decisions"].append({
        "node": "similarity_search",
        "action": "found_patterns",
        "patterns": common_labels,
        "timestamp": datetime.now().isoformat()
    })
    
    return state
```

## State Evolution Example

### Initial State (Workflow Start)
```python
{
    "receipt_id": 12345,
    "image_id": "uuid-here",
    "receipt_words": [...],  # Minimal OCR data
    "pattern_matches": {...},  # From Phase 2
    "currency_columns": [...],  # Spatial columns
    "math_solutions": [...],  # Mathematical relationships
    "four_field_summary": {...},  # Essential field status
    "labels": {},
    "metrics": {},
    "decisions": [],
    "current_phase": "init",
    "total_words": 150,
    "labeled_words": 0,
    "coverage_percentage": 0.0
}
```

### After Pattern Labeling & Decision Engine
```python
{
    ...previous fields...,
    "labels": {
        5: {"label": "DATE", "confidence": 0.95, "source": "pattern", ...},
        23: {"label": "GRAND_TOTAL", "confidence": 0.85, "source": "position_heuristic", ...}
    },
    "found_essentials": {"DATE": 5, "GRAND_TOTAL": 23},
    "missing_essentials": ["MERCHANT_NAME", "TIME"],
    "labeled_words": 45,
    "coverage_percentage": 30.0,
    "decision_outcome": "REQUIRED",  # Missing essentials
    "decision_confidence": "HIGH",
    "decision_reasoning": "Missing 2 essential fields",
    "needs_gpt": true,
    "current_phase": "pattern",
    "decisions": [
        {"node": "pattern_labeling", "action": "found_date", ...},
        {"node": "decision_engine", "action": "require_gpt", ...}
    ]
}
```

### After GPT Labeling (if needed)
```python
{
    ...previous fields...,
    "labels": {
        ...existing...,
        1: {"label": "MERCHANT_NAME", "confidence": 0.8, "source": "gpt", ...},
        7: {"label": "TIME", "confidence": 0.75, "source": "gpt", ...}
    },
    "missing_essentials": [],
    "labeled_words": 120,
    "coverage_percentage": 80.0,
    "gpt_responses": [{"prompt": "...", "tokens": 150, "cost": 0.003, ...}],
    "current_phase": "essential_complete",
    "metrics": {
        "gpt_cost_usd": 0.003,
        "gpt_skip_rate": 0.0,  # Had to use GPT
        "pattern_coverage": 0.3,  # 30% by patterns
        ...
    },
    "label_updates": [  # Ready for DynamoDB
        {"image_id": "uuid", "receipt_id": 12345, "word_id": 1, "label": "MERCHANT_NAME", ...}
    ]
}
```

## Best Practices

### 1. **State Updates**
```python
# Good: Create new objects
state["labels"] = {**state["labels"], word_id: new_label}

# Bad: Mutate in place
state["labels"][word_id] = new_label  # May not trigger updates
```

### 2. **Conditional Loading**
```python
# Only load what you need
if state["needs_gpt"]:
    context = await build_gpt_context(state["receipt_id"])
    # Use context, don't store
```

### 3. **Cleanup After Use**
```python
# Clear temporary data
state["gpt_spatial_context"] = None  # Clear after GPT call
```

## Summary

The state schema is designed to be:
- **Lean**: Only essential, changing data
- **Traceable**: Full audit trail of decisions
- **Performant**: Under 100KB typical size
- **Flexible**: Load heavy data on demand
- **Type-Safe**: Strong typing for mypy compliance
- **Entity-Aligned**: Matches DynamoDB entities exactly

This design ensures efficient Lambda execution while maintaining full visibility into the labeling process and achieving the 94.4% GPT skip rate target from PR #223.

## Key Achievements

1. **Alignment with PRs**: State schema now incorporates spatial analysis (PR #221) and decision engine (PR #223)
2. **Entity Consistency**: LabelInfo matches ReceiptWordLabel, merchant data matches ReceiptMetadata
3. **Cost Optimization**: Decision engine integration enables 94.4% skip rate
4. **Type Safety**: All types use TypedDict, dataclass, and Enum for strong typing
5. **External System Clarity**: Separate tracking for DynamoDB and Pinecone updates