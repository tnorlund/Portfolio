# Receipt Label

A Python package for labeling and validating receipt data using GPT and Pinecone.

## Package Responsibilities

**IMPORTANT**: This package handles business logic and AI integrations. It must NOT contain DynamoDB-specific code.

### What belongs in receipt_label:
- ✅ Receipt labeling and analysis logic
- ✅ AI service integrations (OpenAI, Anthropic)
- ✅ Pinecone vector database operations
- ✅ Google Places API integration
- ✅ Label validation and correction logic

### What does NOT belong here:
- ❌ Direct DynamoDB operations (use receipt_dynamo interfaces)
- ❌ DynamoDB retry logic or resilience patterns (use ResilientDynamoClient from receipt_dynamo)
- ❌ DynamoDB batch processing logic (use receipt_dynamo's batch methods)
- ❌ OCR text extraction (belongs in receipt_ocr)

### Example: Proper DynamoDB Usage
```python
# ✅ CORRECT: Use receipt_dynamo's high-level interfaces
from receipt_dynamo import ResilientDynamoClient

client = ResilientDynamoClient(table_name="my-table")
client.put_ai_usage_metric(metric)  # Let receipt_dynamo handle resilience

# ❌ WRONG: Don't implement DynamoDB logic here
def put_with_retry(item):
    for attempt in range(3):  # This belongs in receipt_dynamo!
        try:
            dynamo.put_item(...)
        except:
            time.sleep(2 ** attempt)
```

## Embedding Strategy

For each receipt word, we generate two embeddings to capture both semantic and spatial context:

1. **Word-level embedding**

   - Text: `<word> [label=ITEM_NAME] (pos=top-left)`
   - Captures the token’s semantic content and its key attributes in a single vector.

2. **Context-level embedding**
   - Text: the concatenated words with similar Y-position (using `get_hybrid_context`)
   - Captures layout and neighboring-word relationships to provide visual context.

**Purpose:**

- The dual embeddings enrich prompts by retrieving both token-level and line-level examples from Pinecone.
- Semantic neighbors (word view) help validate the token’s meaning in isolation.
- Context neighbors (context view) help validate how the token is used in layout (e.g., line items, addresses).

**Metadata Updates:**

- On the **valid** path: update each embedding’s metadata with `status: VALID`.
- On the **invalid** path: update metadata with `status: INVALID` and add `proposed_label: <new_label>`.
- We reuse the same vector IDs so we never duplicate embeddings—only metadata changes.

This approach allows agentic, data‑driven validation and label proposal, while keeping Pinecone storage efficient and easy to query.

## 🧪 Pattern-First Labeling System (NEW)

This project uses an innovative pattern-first approach that achieves **84% cost reduction** by minimizing AI usage through intelligent pattern matching.

### 🔹 Core Philosophy: Patterns First, AI Last

Instead of using AI for everything and falling back to patterns, we invert the approach:
1. **Run all pattern detectors in parallel** (asyncio)
2. **Query merchant-specific patterns** from Pinecone
3. **Only call GPT for truly ambiguous cases**

### 🔹 Pattern Detection System

The `ParallelPatternOrchestrator` runs multiple detectors simultaneously:

- **Currency Patterns**: Detects prices, totals, tax amounts with contextual classification
- **DateTime Patterns**: Finds dates and times in various formats
- **Contact Patterns**: Identifies phone numbers, emails, addresses
- **Quantity Patterns**: Detects item quantities ("2 @ $1.99")
- **Merchant Patterns**: Queries known patterns for specific stores

### 🔹 Smart Decision Engine

The system determines if GPT is needed based on:

1. **Essential Label Coverage**:
   - Must have: `MERCHANT_NAME`, `DATE`, `GRAND_TOTAL`
   - Nice to have: At least one `PRODUCT_NAME`

2. **Ambiguity Threshold**:
   - If <5 meaningful words remain unlabeled → Skip GPT
   - If patterns cover >90% of receipt → Skip GPT

3. **Confidence Scoring**:
   - High-confidence patterns (dates, phones) → Direct label
   - Low-confidence patterns → Queue for GPT validation

### 🔹 Cost Optimization Features

- **Batch API Usage**: 50% discount for non-urgent processing
- **Noise Filtering**: Skip ~30% of OCR artifacts
- **Cached Patterns**: Reuse merchant-specific knowledge
- **Parallel Processing**: 200ms total vs 800ms sequential

### 🔹 When AI is Still Used

GPT is called only for:
- Ambiguous currency classification (when context is unclear)
- Product names without clear patterns
- New merchants without established patterns
- Complex multi-line items
- Validation of low-confidence pattern matches

## AI Usage Tracking

This package includes comprehensive AI usage tracking with context manager patterns for automatic cost monitoring.

### Context Manager Patterns

```python
from receipt_label.utils import ai_usage_context, ai_usage_tracked

# Decorator for automatic tracking
@ai_usage_tracked(operation_type="receipt_processing")
def process_receipt(receipt_id: str):
    # Function is automatically tracked
    result = openai_client.chat.completions.create(...)
    return result

# Context manager for complex operations
with ai_usage_context("batch_processing", job_id="job-123") as tracker:
    for receipt in receipts:
        process_receipt(receipt)
    # Metrics automatically flushed
```

### Features

- **Automatic tracking** via decorators
- **Context propagation** across function calls
- **Error recovery** - metrics flushed even on exceptions
- **Partial failure handling** for batch operations
- **Thread-safe** concurrent operations
- **< 5ms overhead** per operation

See [Context Manager Documentation](docs/context_managers.md) for detailed usage.

This stage is ideal for advanced logic, correction propagation, and multi-hop validation workflows.
