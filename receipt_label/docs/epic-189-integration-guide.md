# Epic #189 Integration Guide

## Overview

Epic #189 provides two key capabilities for Epic #192 (Agent Integration):

1. **Real-time Embedding**: Embed receipt words immediately without waiting for batch processing
2. **Merchant Pattern Queries**: Query patterns efficiently using merchant context (99% query reduction)

## Architecture

### Real-time Embedding Module

Location: `receipt_label/receipt_label/embedding/realtime/`

Key functions:
- `embed_receipt_realtime(receipt_id, merchant_metadata)` - Embed all words from a receipt
- `embed_words_realtime(words, context)` - Embed specific words with merchant context

### Merchant Patterns Module

Location: `receipt_label/receipt_label/merchant_patterns/`

Key functions:
- `get_merchant_patterns(merchant_name)` - Extract all patterns for a merchant
- `query_patterns_for_words(merchant_name, words)` - Find patterns for specific words

## Integration Points for Epic #192

### 1. During Merchant Validation (Immediate Embeddings)

After merchant validation completes, trigger real-time embeddings:

```python
# In validate_merchant_step_functions Lambda
from receipt_label.embedding.realtime import embed_receipt_realtime

def after_merchant_validation(receipt_id: str, merchant_metadata: ReceiptMetadata):
    """Called after successful merchant validation."""

    # Embed immediately if needed for downstream processing
    if requires_immediate_embedding(receipt_id):
        word_embeddings = embed_receipt_realtime(
            receipt_id=receipt_id,
            merchant_metadata=merchant_metadata
        )

        # Words are now embedded and stored in Pinecone
        # Can proceed with pattern matching immediately
```

### 2. Before GPT Labeling (Pattern Matching)

Check merchant patterns before calling GPT to save costs:

```python
# In word labeling Lambda
from receipt_label.merchant_patterns import query_patterns_for_words

def label_receipt_words(receipt_id: str, merchant_name: str):
    """Label words using patterns first, then GPT."""

    # Get words to label
    words = get_unlabeled_words(receipt_id)

    # Query patterns (single Pinecone query for all words)
    pattern_result = query_patterns_for_words(
        merchant_name=merchant_name,
        words=words,
        confidence_threshold=0.8
    )

    # Auto-label high confidence matches
    for word_text, pattern in pattern_result.pattern_matches.items():
        if pattern.confidence_level == PatternConfidence.HIGH:
            # Skip GPT, use pattern
            apply_label(word_text, pattern.suggested_label)

    # Only call GPT for words without patterns
    if pattern_result.words_without_patterns:
        gpt_labels = call_gpt_for_labels(pattern_result.words_without_patterns)
```

### 3. Pattern Learning Loop

After validation, update patterns:

```python
# In validation completion Lambda
def after_label_validation(receipt_id: str, validated_labels: dict):
    """Update patterns after successful validation."""

    # Patterns are automatically updated when embeddings include
    # validated_labels in metadata

    # Next time query_patterns_for_words is called for this merchant,
    # it will include the newly validated patterns
```

## AWS Infrastructure Requirements for Epic #192

### 1. Lambda Function Updates

Add these capabilities to existing Lambdas:

```python
# Lambda Layer additions
receipt_label_layer/
├── embedding/
│   └── realtime/       # New real-time embedding module
└── merchant_patterns/  # New pattern query module
```

### 2. Environment Variables

```yaml
ENABLE_REALTIME_EMBEDDING: "true"
PATTERN_CONFIDENCE_THRESHOLD: "0.8"
MAX_REALTIME_WORDS_PER_RECEIPT: "100"
```

### 3. Step Function Integration

```json
{
  "EmbedIfNeeded": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:region:account:function:embed-receipt-realtime",
    "Parameters": {
      "receipt_id.$": "$.receipt_id",
      "merchant_metadata.$": "$.merchant_metadata"
    },
    "Next": "QueryPatterns"
  },
  "QueryPatterns": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:region:account:function:query-merchant-patterns",
    "Parameters": {
      "merchant_name.$": "$.merchant_metadata.canonical_merchant_name",
      "receipt_id.$": "$.receipt_id"
    },
    "Next": "LabelWithPatterns"
  }
}
```

### 4. Monitoring and Metrics

Track these metrics:
- Real-time embedding latency
- Pattern match rate
- GPT calls saved
- Cost reduction

## Usage Examples

### Example 1: Simple Pattern Query

```python
from receipt_label.merchant_patterns import get_merchant_patterns

# Get all patterns for Walmart
patterns = get_merchant_patterns("WALMART")

# Check if "TOTAL" has a known pattern
total_pattern = patterns.get_best_pattern("total")
if total_pattern and total_pattern.confidence > 0.9:
    print(f"'TOTAL' maps to '{total_pattern.suggested_label}' with {total_pattern.confidence:.0%} confidence")
```

### Example 2: Batch Pattern Matching

```python
from receipt_label.merchant_patterns import query_patterns_for_words

# Get patterns for multiple words in one query
result = query_patterns_for_words(
    merchant_name="TARGET",
    words=receipt_words,
    confidence_threshold=0.7
)

print(f"Found patterns for {len(result.words_with_patterns)} out of {len(receipt_words)} words")
print(f"Query reduction: {result.query_reduction_ratio:.0%}")
```

### Example 3: Real-time Processing Flow

```python
# Complete flow for immediate processing
async def process_receipt_immediately(receipt_id: str):
    # 1. Get merchant metadata
    metadata = get_merchant_metadata(receipt_id)

    # 2. Embed in real-time
    embeddings = embed_receipt_realtime(receipt_id, metadata)

    # 3. Query patterns
    patterns = query_patterns_for_words(
        metadata.canonical_merchant_name,
        get_receipt_words(receipt_id)
    )

    # 4. Apply patterns and call GPT only when needed
    labels = apply_patterns_and_label(patterns)

    return labels
```

## Performance Characteristics

### Real-time Embedding
- Latency: 1-3 seconds for 50 words
- Cost: Same as regular OpenAI embedding API
- Rate limit: 3,500 RPM (handled internally)

### Pattern Queries
- Latency: 200-500ms per merchant query
- Query reduction: 99% (1 query instead of N)
- Accuracy: Improves with more validated data

## Best Practices

1. **Use Real-time Sparingly**: Only for user-facing or validation workflows
2. **Cache Patterns**: Store frequently used merchant patterns in memory
3. **Set Confidence Thresholds**: Start with 0.8 for auto-labeling
4. **Monitor Costs**: Track real-time vs batch usage
5. **Fallback Gracefully**: Always have GPT as fallback for words without patterns

## Future Enhancements

After Epic #192 integration:
- Pattern confidence learning from validation feedback
- Cross-merchant pattern sharing for chains
- Temporal pattern tracking (seasonal items)
- Multi-language pattern support
