# Phase 3: State Schema Design Decisions

## Overview

The LangGraph state schema is the core data structure that flows between all nodes in the workflow. This document explains what data should be included in the state vs. loaded on demand.

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
# Identifiers
receipt_id: int  # DynamoDB PK
image_id: str   # UUID

# Minimal OCR data for processing
receipt_words: List[Dict[str, Any]]
# Each word dict contains only:
# - word_id, line_id, text
# - confidence, bounding_box
# - x, y coordinates
```

### Pattern Detection Results
```python
# Transformed pattern matches
pattern_matches: Dict[str, List[Dict]]
# e.g., {"CURRENCY": [{word_id: 5, value: 12.99}]}

# Currency structure (critical for GPT context)
currency_columns: List[Dict]
# Spatial grouping info
structure_sections: Dict[str, List[int]]
```

### Labeling Progress
```python
# Current label assignments
labels: Dict[int, LabelInfo]
grouped_labels: Dict[str, List[int]]  # Multi-word groups

# Gap tracking
missing_essentials: List[str]
found_essentials: Dict[str, int]

# Line items detected
line_items: List[Dict]
```

### Workflow Control
```python
# Current state
current_phase: str
needs_gpt: bool
needs_review: bool

# Metrics
metrics: ProcessingMetrics
node_timings: Dict[str, float]

# Audit trail
decisions: List[Dict]  # All labeling decisions
errors: List[Dict]
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
# Store only:
merchant_place_id: str
merchant_name: str

# Load from DynamoDB when needed:
- Full address
- Phone numbers
- Business hours
- Category details
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
    "currency_columns": [...],
    "labels": {},
    "metrics": {},
    "decisions": [],
    "current_phase": "init"
}
```

### After Pattern Labeling
```python
{
    ...previous fields...,
    "labels": {
        5: {"label_type": "DATE", "confidence": 0.95, ...},
        23: {"label_type": "GRAND_TOTAL", "confidence": 0.85, ...}
    },
    "found_essentials": {"DATE": 5, "GRAND_TOTAL": 23},
    "missing_essentials": ["MERCHANT_NAME", "TIME"],
    "needs_gpt": true,
    "current_phase": "pattern",
    "decisions": [
        {"node": "pattern_labeling", "action": "found_date", ...}
    ]
}
```

### After GPT Labeling
```python
{
    ...previous fields...,
    "labels": {
        ...existing...,
        1: {"label_type": "MERCHANT_NAME", "confidence": 0.8, ...},
        7: {"label_type": "TIME", "confidence": 0.75, ...}
    },
    "missing_essentials": [],
    "gpt_responses": [{"prompt": "...", "tokens": 150, ...}],
    "current_phase": "essential_complete",
    "metrics": {"gpt_cost_usd": 0.003, ...}
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

This design ensures efficient Lambda execution while maintaining full visibility into the labeling process.