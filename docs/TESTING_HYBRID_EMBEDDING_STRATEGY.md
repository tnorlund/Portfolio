# Testing Hybrid Embedding Implementation - Strategy

## Overview

This document outlines how to test the new hybrid embedding implementation (`search_similar_words` tool) and compare it to the background process without interfering with it.

## Current State

### Background Process
- **Script**: `scripts/test_all_needs_review_merchant_names.py`
- **Status**: Running (started ~12:04 PM, ~5+ hours runtime)
- **Progress**: 609 labels tested so far
- **Results File**: `label_validation_results_20251128_120722.json`
- **Results**:
  - VALID: 296 (48.6%)
  - INVALID: 297 (48.8%)
  - NEEDS_REVIEW: 16 (2.6%)

### Background Process Approach
- Uses **stored embeddings** from ChromaDB (if available)
- Falls back to error if word not found in ChromaDB
- This is the "old" approach (before hybrid implementation)

### New Hybrid Approach
- **First**: Try to get stored embedding from ChromaDB (fast, free)
- **Fallback**: Embed on-the-fly using new format if not found (slower, costs money)
- **Benefits**:
  - Faster for words already in ChromaDB
  - Works even if word doesn't exist in ChromaDB
  - Uses new embedding format for consistency

## Testing Strategy

### Option 1: Parallel Testing (Recommended)

Test the new hybrid approach on a **separate sample** of labels without interfering with the background process.

**Pros:**
- No interference with running process
- Can test immediately
- Can compare results side-by-side
- Can measure performance differences

**Cons:**
- Tests different labels (not exact same set)
- Can't do direct A/B comparison on same labels

**How to Run:**
```bash
# Test with 20 labels (quick test)
SAMPLE_SIZE=20 python scripts/test_hybrid_embedding_comparison.py

# Test with 50 labels (more comprehensive)
SAMPLE_SIZE=50 python scripts/test_hybrid_embedding_comparison.py

# Test with 100 labels (full comparison)
SAMPLE_SIZE=100 python scripts/test_hybrid_embedding_comparison.py
```

**What It Does:**
1. Gets a sample of NEEDS_REVIEW MERCHANT_NAME labels (different from background process)
2. Tests with NEW hybrid embedding approach
3. Measures performance (time, tools used, embedding source)
4. Saves results to `hybrid_embedding_comparison_*.json`
5. Generates analysis in `hybrid_embedding_analysis_*.json`

### Option 2: Wait and Compare (After Background Process Completes)

Wait for the background process to finish, then:
1. Re-run the same labels with the new hybrid approach
2. Compare results side-by-side

**Pros:**
- Tests exact same labels
- Direct A/B comparison
- Can see if decisions change

**Cons:**
- Must wait for background process to complete
- Takes longer to get results

**How to Run:**
```bash
# After background process completes, re-run on same labels
python scripts/test_hybrid_embedding_comparison.py --use-same-labels-from=label_validation_results_20251128_120722.json
```

### Option 3: Overlap Testing (Test Same Labels in Parallel)

Test a small subset of labels that the background process has already tested.

**Pros:**
- Direct comparison on same labels
- Can see immediate differences

**Cons:**
- Risk of file conflicts (if both write to same file)
- Need to coordinate carefully

**How to Run:**
```bash
# Extract first 10 labels from background results
python scripts/test_hybrid_embedding_comparison.py --labels-from=label_validation_results_20251128_120722.json --limit=10
```

## Metrics to Compare

### 1. Performance Metrics
- **Average time per label**: How long does each approach take?
- **Min/Max time**: What's the range?
- **Embedding source**: How often is stored vs on-the-fly used?

### 2. Accuracy Metrics
- **Decision distribution**: VALID vs INVALID vs NEEDS_REVIEW
- **Confidence scores**: Average confidence per decision type
- **Consistency**: Do both approaches agree on decisions?

### 3. Tool Usage
- **Which tools are called**: `get_word_context`, `search_similar_words`, etc.
- **Frequency**: How often is each tool used?
- **Patterns**: Are there patterns in tool usage?

### 4. Embedding Source Tracking
- **Stored embeddings used**: How many times did we use stored embeddings?
- **On-the-fly embeddings**: How many times did we fall back?
- **Missing embeddings**: How many words weren't in ChromaDB?

## Expected Differences

### Performance
- **Hybrid approach**: Should be faster for words already in ChromaDB (uses stored)
- **Background process**: May fail for words not in ChromaDB (no fallback)

### Accuracy
- **Hybrid approach**: Should have better similarity search (new format consistency)
- **Background process**: May have format mismatches (old vs new format)

### Reliability
- **Hybrid approach**: Should work for all words (has fallback)
- **Background process**: May fail for words not in ChromaDB

## Analysis Script

After running the comparison test, analyze the results:

```python
# Compare results
python scripts/analyze_hybrid_embedding_comparison.py \
  --background-results=label_validation_results_20251128_120722.json \
  --hybrid-results=hybrid_embedding_comparison_*.json
```

This will generate:
- Side-by-side comparison
- Performance differences
- Decision agreement/disagreement
- Recommendations

## Recommendations

### For Immediate Testing (Now)
1. **Run Option 1** (Parallel Testing) with 20-50 labels
2. Compare performance and accuracy metrics
3. Check if hybrid approach is faster/better

### For Comprehensive Testing (After Background Completes)
1. **Run Option 2** (Wait and Compare) on same labels
2. Direct A/B comparison
3. Measure exact differences

### For Production
1. If hybrid approach is better, update background process
2. If similar, keep both approaches
3. Monitor long-term performance

## Safety Considerations

### File Conflicts
- Background process writes to: `label_validation_results_20251128_120722.json`
- Comparison test writes to: `hybrid_embedding_comparison_*.json`
- **No conflicts** - different file names

### ChromaDB Access
- Both processes read from same ChromaDB (read-only)
- **No conflicts** - ChromaDB supports concurrent reads

### DynamoDB Access
- Both processes read from DynamoDB (read-only)
- **No conflicts** - DynamoDB supports concurrent reads

### API Rate Limits
- Both processes use OpenAI embedding API
- **Potential conflict**: Rate limits
- **Mitigation**: Test with small sample first, monitor rate limits

## Next Steps

1. **Review this strategy** - Make sure it makes sense
2. **Run Option 1** - Test with 20 labels first
3. **Analyze results** - Compare performance and accuracy
4. **Decide on approach** - Hybrid vs stored-only
5. **Update if needed** - Modify background process if hybrid is better

