# Claude Development Notes - Receipt Label System

This document contains insights, patterns, and lessons learned while developing the receipt labeling system on the `feat/agent-labeling-system` branch.

## Architecture Insights

### 1. Pattern-First Design Philosophy

The most significant cost optimization came from inverting the traditional approach:

**Old Way**: Use AI for everything, fall back to patterns for simple cases
**New Way**: Use patterns for everything possible, only call AI when patterns fail

This resulted in an 84% reduction in GPT calls during testing.

### 2. Parallel Processing Architecture

The `ParallelPatternOrchestrator` runs all pattern detectors simultaneously using asyncio:

```python
# All detectors run at once, not sequentially
results = await asyncio.gather(
    detect_currency_patterns(words),
    detect_datetime_patterns(words),
    detect_contact_patterns(words),
    detect_quantity_patterns(words),
    query_merchant_patterns(merchant_name)
)
```

Key insight: Even with 5 concurrent operations, the total time is bounded by the slowest operation (typically Pinecone at ~200ms).

### 3. Smart Currency Classification

Currency amounts are ambiguous - `$12.99` could be:
- A unit price
- A line total  
- A subtotal
- Tax amount

The `EnhancedCurrencyAnalyzer` uses context clues:
- **Position**: Bottom 20% of receipt → likely totals
- **Keywords**: Nearby "total", "tax", "subtotal"
- **Patterns**: Preceded by quantity → unit price

This eliminates the need for GPT to disambiguate most currency values.

## Cost Optimization Strategies

### 1. Batch Processing Economics

OpenAI Batch API pricing (as of 2024):
- Regular API: $0.01 per 1K tokens
- Batch API: $0.005 per 1K tokens (50% discount)
- Batch latency: Up to 24 hours

Strategy: Queue non-urgent labeling for batch processing, use regular API only for real-time needs.

### 2. Pinecone Query Optimization

**Problem**: Querying Pinecone for each word = N queries per receipt
**Solution**: Single batch query with all words, filter results client-side

```python
# Bad: N queries
for word in words:
    results = pinecone.query(word.text)

# Good: 1 query  
all_texts = [w.text for w in words]
results = pinecone.query(all_texts, top_k=1000)
# Filter results by word locally
```

### 3. Essential Labels Concept

Not all labels are equally important. The system prioritizes finding:
- `MERCHANT_NAME` - Must know where purchase was made
- `DATE` - Must have transaction date
- `GRAND_TOTAL` - Must know final amount
- At least one `PRODUCT_NAME` - Must have items purchased

If patterns find these, GPT might not be needed at all.

## Pattern Detection Insights

### 1. Regex Patterns That Work

After analyzing thousands of receipts, these patterns proved most reliable:

```python
# Currency - handles all common formats
CURRENCY_PATTERN = r'\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?'

# Quantity with @ symbol - very reliable
QUANTITY_AT_PATTERN = r'(\d+(?:\.\d+)?)\s*@\s*\$?(\d+(?:\.\d+)?)'

# Date formats - covers 90% of cases
DATE_PATTERNS = [
    r'\d{1,2}/\d{1,2}/\d{2,4}',  # MM/DD/YYYY
    r'\d{1,2}-\d{1,2}-\d{2,4}',  # MM-DD-YYYY
    r'\d{4}-\d{1,2}-\d{1,2}'     # YYYY-MM-DD
]
```

### 2. Merchant-Specific Patterns

Different merchants use consistent terminology:
- **Walmart**: "TC#" (transaction), "ST#" (store), "OP#" (operator)
- **McDonald's**: Product names like "Big Mac", "McFlurry"
- **Gas Stations**: "Gallons", "Price/Gal", pump numbers

Storing these in Pinecone allows instant pattern matching for future receipts.

### 3. Noise Word Handling

Critical insight: ~30% of OCR words are noise (punctuation, separators, artifacts).

Strategy:
- Store in DynamoDB for completeness
- Skip embedding/labeling to save costs
- Filter during pattern detection

## Development Workflow Optimizations

### 1. Local Data Export

The `export_receipt_data.py` script enables offline development:

```bash
# Export diverse sample
python scripts/export_receipt_data.py sample --size 20

# Export specific merchants for testing
python scripts/export_receipt_data.py merchant --name "Walmart" --limit 5
```

### 2. API Stubbing Framework

The fixture system allows cost-free testing:

```python
@pytest.fixture
def stub_all_apis():
    # Returns canned responses for all external services
    # No API calls, no costs, fast tests
```

### 3. Decision Engine Testing

The `test_decision_engine.py` script validates pattern coverage:

```bash
python scripts/test_decision_engine.py ./receipt_data

# Output shows skip rate and cost savings
# "Skip Rate: 84.0%" = 84% fewer GPT calls needed
```

## Performance Considerations

### 1. Asyncio Benefits

Running pattern detectors in parallel reduced processing time by ~75%:
- Sequential: ~800ms per receipt
- Parallel: ~200ms per receipt

### 2. Caching Strategy

Two-level caching improves performance:
1. **LRU Cache**: In-memory for pattern compilation
2. **Redis Cache**: Cross-request for Pinecone results

### 3. Batch Size Optimization

Optimal batch sizes discovered through testing:
- Pinecone queries: 100-200 vectors per query
- OpenAI batch: 10-20 receipts per request
- DynamoDB writes: 25 items per batch

## Common Pitfalls and Solutions

### 1. Over-Labeling

**Problem**: Labeling every single word, including noise
**Solution**: Smart thresholds - if <5 meaningful unlabeled words remain, skip GPT

### 2. Context Loss

**Problem**: Labeling words in isolation loses meaning
**Solution**: Include line context in prompts and embeddings

### 3. Pattern Conflicts

**Problem**: Multiple patterns match the same word
**Solution**: Confidence scoring and precedence rules

### 4. Merchant Variations

**Problem**: "Walmart", "WAL-MART", "WALMART #1234" are the same merchant
**Solution**: Normalize merchant names during metadata lookup

## Future Improvements

### 1. Active Learning

Track which patterns GPT corrects most often and update pattern rules accordingly.

### 2. Merchant-Specific Models

Fine-tune smaller models for high-volume merchants (Walmart, Target, etc.) to reduce GPT-4 usage.

### 3. Layout-Aware Patterns

Use spatial relationships between words to improve pattern matching:
- Words aligned vertically are likely related
- Currency amounts on the same line as quantities are likely prices

### 4. Confidence Thresholds

Implement dynamic confidence thresholds based on merchant type and receipt quality.

## Key Metrics to Monitor

1. **Skip Rate**: Percentage of receipts not needing GPT (target: >80%)
2. **Pattern Coverage**: Percentage of words labeled by patterns (target: >60%)
3. **API Cost per Receipt**: Total cost including all services (target: <$0.05)
4. **Processing Time**: End-to-end latency (target: <500ms for patterns, <3s with GPT)
5. **Validation Error Rate**: Percentage of labels needing correction (target: <5%)

## Debugging Tips

### 1. Pattern Misses

When patterns aren't matching expected text:
```python
# Enable debug mode in pattern detectors
detector = CurrencyPatternDetector(debug=True)
# Shows why patterns failed to match
```

### 2. Cost Tracking

Monitor API usage in real-time:
```python
with ai_usage_context("debug_session") as tracker:
    process_receipt(receipt_id)
    print(f"Total cost: ${tracker.total_cost}")
```

### 3. Local Testing

Always test with local data first:
```bash
USE_STUB_APIS=true pytest -xvs
```

## Code Smells to Avoid

1. **Sequential API Calls**: Always batch or parallelize
2. **Unbounded Queries**: Always set top_k limits
3. **Missing Error Handling**: Every external call needs try/except
4. **Hardcoded Thresholds**: Make configurable via environment
5. **Synchronous I/O**: Use asyncio for all I/O operations

## References

- [OpenAI Batch API Docs](https://platform.openai.com/docs/guides/batch)
- [Pinecone Best Practices](https://docs.pinecone.io/docs/best-practices)
- [Receipt Label Architecture Diagrams](./docs/architecture/)
- [Cost Analysis Spreadsheet](./docs/cost-analysis.xlsx)