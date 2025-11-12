# ChromaDB Similarity Search Validation

## Overview

ChromaDB similarity search validation is a technique that validates PENDING labels by comparing them against historically validated labels stored in ChromaDB. This provides a fast, cost-effective complement to Chain of Verification (CoVe) validation, using semantic similarity to determine label accuracy.

## How It Works

ChromaDB validation follows this process:

1. **Get Word Embedding**: Retrieve the word's embedding vector from ChromaDB
2. **Query Similar Words**: Find similar words that have VALID labels of the same type
3. **Calculate Similarity**: Compute average similarity score across matches
4. **Check for Conflicts**: Identify if similar words have conflicting VALID labels
5. **Make Decision**: Mark as VALID, INVALID, or keep as PENDING based on similarity and conflicts

## Architecture

### Integration with LangGraph

ChromaDB validation is integrated as an optional node in the LangGraph workflow:

```
load_data → phase1_currency → phase2_line_analysis → combine_results
                                                          ↓
                                              validate_chromadb (optional)
                                                          ↓
                                                         END
```

### Node Implementation

**File**: `receipt_label/receipt_label/langchain/nodes/validate_labels_chromadb.py`

The `validate_labels_chromadb` node:
- Processes PENDING labels from `receipt_word_labels_to_add` and `receipt_word_labels_to_update`
- Queries ChromaDB for similar words with VALID labels
- Updates `validation_status` based on similarity scores and conflicts
- Returns validation statistics

### Workflow Integration

**File**: `receipt_label/receipt_label/langchain/currency_validation.py`

The ChromaDB client is optionally passed to `create_unified_analysis_graph()`:

```python
def create_unified_analysis_graph(
    ollama_api_key: str,
    save_dev_state: bool = False,
    google_places_api_key: Optional[str] = None,
    update_metadata: bool = False,
    chroma_client: Optional[VectorStoreInterface] = None,  # Optional ChromaDB client
) -> CompiledStateGraph:
```

If `chroma_client` is provided, the validation node is added to the workflow.

## Validation Strategy

### Two-Tier Validation System

We use a two-tier approach to maximize label accuracy:

1. **CoVe (LLM-based)**: High accuracy, slower, costs API calls
   - Validates labels by having the LLM verify its own outputs
   - Best for complex reasoning and context-dependent labels
   - Marks labels as VALID if verification passes

2. **ChromaDB (Similarity-based)**: Fast, free, uses historical data
   - Validates labels by comparing against similar validated words
   - Best for pattern-based labels and merchant-specific patterns
   - Can validate PENDING labels that CoVe couldn't verify

### Validation Decision Matrix

| Scenario | Similarity | Matches | Conflicting Labels? | Decision | Reasoning |
|----------|-----------|---------|---------------------|----------|-----------|
| High confidence | ≥ 0.75 | ≥ 3 | No | ✅ **VALID** | Strong evidence from similar words |
| Medium confidence | 0.70-0.75 | ≥ 3 | No | ⏸️ **PENDING** | Wait for more validated data |
| Low confidence | < 0.70 | < 3 | No | ⏸️ **PENDING** | Insufficient evidence |
| Conflicting evidence | ≥ 0.65 | ≥ 2 | Yes | ❌ **INVALID** | Similar words have different labels |
| Strong conflict | ≥ 0.75 | ≥ 5 | Yes | ❌ **INVALID** | High confidence in conflict |

### Configuration Parameters

- **`similarity_threshold`** (default: 0.75): Minimum similarity to mark as VALID
- **`min_matches`** (default: 3): Minimum number of similar words needed
- **`conflict_threshold`** (default: 0.65): If conflicting labels found above this similarity, mark as INVALID
- **`merchant_filter`**: Optionally filter by merchant name for better accuracy

## When to Invalidate Labels

Labels are marked as **INVALID** when:

### 1. Conflicting Evidence
Similar words (similarity ≥ 0.65) have different VALID labels, indicating the label is likely incorrect.

**Example**: Word "39.29" is labeled as `GRAND_TOTAL`, but similar words with `GRAND_TOTAL` are different amounts (e.g., "42.14", "15.99").

### 2. Pattern Mismatch
The word doesn't match expected patterns for the label type.

**Example**: Word "ABC" is labeled as `DATE` but doesn't match date patterns (e.g., "MM/DD/YYYY").

### 3. Merchant-Specific Conflicts
Same merchant, similar words have different labels, indicating a merchant-specific pattern violation.

**Example**: At "Target", similar words are labeled as `SUBTOTAL`, but this word is labeled as `GRAND_TOTAL`.

### 4. Low Similarity with Matching Labels
Similarity < 0.70 and < 3 matches - but this keeps the label as PENDING rather than invalidating (insufficient evidence to invalidate).

## Validating Remaining PENDING Labels

### Tiered Validation Strategy

After initial CoVe + ChromaDB validation, remaining PENDING labels are processed in priority tiers:

#### Tier 1: Critical Labels (Immediate Validation)
- **Labels**: `GRAND_TOTAL`, `MERCHANT_NAME`, `DATE`, `TAX`
- **Thresholds**: Similarity ≥ 0.80, min 5 matches
- **Frequency**: Immediately after CoVe processing
- **Action**: Run ChromaDB validation with strict thresholds

#### Tier 2: Important Labels (Batch Validation)
- **Labels**: `SUBTOTAL`, `LINE_TOTAL`, `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`
- **Thresholds**: Similarity ≥ 0.75, min 3 matches
- **Frequency**: Daily batch job
- **Action**: Run ChromaDB validation with standard thresholds

#### Tier 3: Supporting Labels (Periodic Validation)
- **Labels**: `PAYMENT_METHOD`, `LOYALTY_ID`, `WEBSITE`, `PHONE_NUMBER`
- **Thresholds**: Similarity ≥ 0.70, min 2 matches
- **Frequency**: Weekly batch job
- **Action**: Run ChromaDB validation with relaxed thresholds

### Batch Validation Script

**File**: `dev.validate_pending_labels_batch.py` (to be created)

This script:
1. Lists all PENDING labels from DynamoDB
2. Groups by label type and priority tier
3. Validates using ChromaDB similarity search
4. Updates `validation_status` to VALID or INVALID
5. Reports statistics and updates ChromaDB metadata

## ChromaDB Metadata Structure

ChromaDB stores word embeddings with metadata that includes:

- **`valid_labels`**: Comma-delimited string of labels with `validation_status="VALID"` (e.g., `",GRAND_TOTAL,DATE,"`)
- **`invalid_labels`**: Comma-delimited string of labels with `validation_status="INVALID"`
- **`merchant_name`**: Normalized merchant name for filtering
- **`label_status`**: Overall label status (`"validated"`, `"invalidated"`, `"auto_suggested"`)
- Other metadata: word text, position, context, etc.

### Query Pattern

```python
# Find similar words with VALID labels of a specific type
where_filter = {
    "valid_labels": {"$contains": "GRAND_TOTAL"}
}

# Optionally filter by merchant
where_filter = {
    "$and": [
        {"valid_labels": {"$contains": "GRAND_TOTAL"}},
        {"merchant_name": {"$eq": "target"}},
    ]
}

# Query ChromaDB
query_results = chroma_client.query(
    collection_name="words",
    query_embeddings=[word_embedding],
    n_results=20,
    where=where_filter,
    include=["metadatas", "distances"],
)
```

## Benefits

### 1. Self-Improving System
As more labels are validated, the ChromaDB database grows, improving future validation accuracy.

### 2. Merchant-Aware Validation
Filtering by merchant name provides better accuracy for merchant-specific patterns (e.g., Target vs. Walmart receipt formats).

### 3. Fast and Cost-Effective
No LLM API calls required - uses existing embeddings and similarity search.

### 4. Conflict Detection
Identifies and invalidates labels that conflict with validated historical data.

### 5. Integrated Workflow
Runs as part of the LangGraph workflow, providing visibility in LangSmith traces.

## Usage

### In LangGraph Workflow

```python
from receipt_label.vector_store import VectorStoreInterface

# Create ChromaDB client
chroma_client = ChromaDBClient(
    persist_directory="/path/to/chromadb",
    mode="read",
)

# Pass to workflow
unified_graph = create_unified_analysis_graph(
    ollama_api_key=ollama_api_key,
    chroma_client=chroma_client,  # Optional
)

# Run workflow
result = await unified_graph.ainvoke(initial_state)
```

### Standalone Validation

```python
from receipt_label.langchain.nodes.validate_labels_chromadb import (
    validate_labels_chromadb,
)

# Validate PENDING labels
results = await validate_labels_chromadb(
    state=currency_analysis_state,
    chroma_client=chroma_client,
    similarity_threshold=0.75,
    min_matches=3,
    conflict_threshold=0.65,
)
```

## Comparison: CoVe vs. ChromaDB Validation

| Aspect | CoVe (LLM) | ChromaDB (Similarity) |
|--------|------------|----------------------|
| **Speed** | Slower (LLM calls) | Fast (vector search) |
| **Cost** | API costs | Free (uses existing data) |
| **Accuracy** | High (reasoning-based) | High (pattern-based) |
| **Best For** | Complex reasoning, context-dependent | Pattern matching, merchant-specific |
| **When** | Initial extraction | Post-processing, batch validation |
| **Self-Improving** | No | Yes (more data = better) |

## Future Enhancements

1. **Adaptive Thresholds**: Adjust similarity thresholds based on label type and merchant
2. **Confidence Scores**: Store confidence scores alongside validation status
3. **Batch Processing**: Optimize for large-scale batch validation
4. **Merchant Clustering**: Group similar merchants for better pattern matching
5. **Temporal Validation**: Consider time-based patterns (e.g., date formats changing over time)

## Related Documentation

- [Chain of Verification (CoVe) Implementation](./CHAIN_OF_VERIFICATION.md)
- [PENDING Labels Best Practices](../../docs/PENDING_LABELS_BEST_PRACTICES.md)
- [ChromaDB Embedding Write Paths](../../docs/CHROMADB_EMBEDDING_WRITE_PATHS.md)

