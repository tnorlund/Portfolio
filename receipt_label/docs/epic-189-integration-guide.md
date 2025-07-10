# Epic #189 Integration Guide

## Overview

Epic #189 provides real-time embedding capabilities for Epic #192 (AWS Infrastructure Integration):

**Real-time Embedding**: Embed receipt words and lines immediately without waiting for batch processing, integrated with existing merchant validation.

## Architecture

### Real-time Embedding Module

**Modular Structure:**
- `receipt_label/receipt_label/embedding/word/realtime.py` - Word embedding functions
- `receipt_label/receipt_label/embedding/line/realtime.py` - Line embedding functions

Key functions:
- `embed_receipt_words_realtime(receipt_id, merchant_name)` - Embed all words from a receipt
- `embed_words_realtime(words, merchant_name)` - Embed specific words with merchant context
- `embed_receipt_lines_realtime(receipt_id, merchant_name)` - Embed all lines from a receipt

### Merchant Validation Integration

Location: `receipt_label/receipt_label/embedding/integration.py`

Key functions:
- `process_receipt_with_realtime_embedding()` - Complete workflow with merchant validation
- `embed_receipt_realtime()` - Simplified embedding interface

## Integration Points for Epic #192

### 1. During Merchant Validation (Immediate Embeddings)

After merchant validation completes, trigger real-time embeddings:

```python
# In merchant validation Lambda or Step Function
from receipt_label.embedding.integration import process_receipt_with_realtime_embedding

def process_receipt_with_embedding(receipt_id: str):
    """Complete workflow: merchant validation + real-time embedding."""
    
    # Single call handles both merchant validation and embedding
    merchant_metadata, embedding_results = process_receipt_with_realtime_embedding(
        receipt_id=receipt_id,
        embed_words=True,
        embed_lines=False,  # Optional: embed lines too
    )
    
    # Results include both merchant data and embedding status
    return {
        "merchant_name": merchant_metadata.merchant_name,
        "place_id": merchant_metadata.place_id,
        "words_embedded": embedding_results.get("words", {}).get("count", 0),
        "validation_method": merchant_metadata.validated_by,
    }
```

### 2. Modular Real-time Embedding (Advanced Usage)

For use cases where merchant is already known:

```python
# Direct modular usage in specialized Lambdas
from receipt_label.embedding.word.realtime import embed_receipt_words_realtime
from receipt_label.embedding.line.realtime import embed_receipt_lines_realtime

def embed_receipt_words_only(receipt_id: str, merchant_name: str):
    """Embed just words when merchant is pre-validated."""
    
    word_embeddings = embed_receipt_words_realtime(
        receipt_id=receipt_id,
        merchant_name=merchant_name
    )
    
    return {
        "words_embedded": len(word_embeddings),
        "merchant_name": merchant_name,
    }

def embed_receipt_lines_only(receipt_id: str, merchant_name: str):
    """Embed just lines for section classification."""
    
    line_embeddings = embed_receipt_lines_realtime(
        receipt_id=receipt_id,
        merchant_name=merchant_name
    )
    
    return {
        "lines_embedded": len(line_embeddings),
        "merchant_name": merchant_name,
    }
```

### 3. Error Handling and Fallbacks

Handle failures gracefully:

```python
# Robust error handling
from receipt_label.embedding.integration import process_receipt_with_realtime_embedding

def safe_receipt_processing(receipt_id: str):
    """Process receipt with comprehensive error handling."""
    
    try:
        merchant_metadata, embedding_results = process_receipt_with_realtime_embedding(
            receipt_id=receipt_id,
            embed_words=True,
            embed_lines=False,
        )
        
        # Check for embedding errors
        if "error" in embedding_results.get("words", {}):
            logger.warning(f"Word embedding failed for {receipt_id}: {embedding_results['words']['error']}")
            # Could fall back to batch processing
            
        return {
            "status": "success",
            "merchant_metadata": merchant_metadata,
            "embedding_results": embedding_results,
        }
        
    except Exception as e:
        logger.error(f"Receipt processing failed for {receipt_id}: {str(e)}")
        return {
            "status": "failed",
            "error": str(e),
            "fallback_to_batch": True,
        }
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
    merchant_name = metadata.canonical_merchant_name or metadata.merchant_name

    # 2. Embed in real-time
    from receipt_label.embedding.word.realtime import embed_receipt_words_realtime
    embeddings = embed_receipt_words_realtime(receipt_id, merchant_name)

    # 3. Query patterns
    from receipt_label.merchant_patterns import query_patterns_for_words
    patterns = query_patterns_for_words(
        merchant_name,
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
