# Label Harmonizer Parallelization & Optimization Strategy

## Current Bottlenecks

1. **Sequential LLM Calls**: One word at a time (~1-2 seconds each)
2. **Sequential Merchant Processing**: One merchant group at a time
3. **Sequential Label Type Processing**: One label type at a time
4. **No Caching**: Same word evaluated multiple times

## Parallelization Opportunities

### 1. Multiple Label Types in Parallel ✅ HIGHEST IMPACT
**Current**: Process GRAND_TOTAL, then SUBTOTAL, then DATE, etc.
**Optimized**: Process all label types concurrently

```python
async def harmonize_multiple_label_types(
    self,
    label_types: list[str],
    max_concurrency: int = 5,
) -> dict[str, dict]:
    """Process multiple label types in parallel."""
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def process_one(label_type: str):
        async with semaphore:
            return await self.harmonize_label_type(label_type)
    
    tasks = [process_one(lt) for lt in label_types]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return {lt: r for lt, r in zip(label_types, results)}
```

**Impact**: If processing 10 label types, reduces time from 10x to ~2x (with concurrency=5)

### 2. Multiple Merchants in Parallel ✅ HIGH IMPACT
**Current**: Process merchant A, then B, then C
**Optimized**: Process merchants concurrently

```python
async def harmonize_label_type(
    self,
    label_type: str,
    max_merchant_concurrency: int = 10,
    ...
):
    groups_to_process = list(self._merchant_groups.values())
    
    semaphore = asyncio.Semaphore(max_merchant_concurrency)
    
    async def process_group(group):
        async with semaphore:
            return await self.analyze_group(group, use_similarity=use_similarity)
    
    tasks = [process_group(g) for g in groups_to_process]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Impact**: If processing 50 merchants, reduces time from 50x to ~5x (with concurrency=10)

### 3. Batch LLM Calls ✅ MEDIUM-HIGH IMPACT
**Current**: One word → one LLM call
**Optimized**: Batch multiple words in one LLM call

```python
async def _llm_determine_outliers_batch(
    self,
    words: list[LabelRecord],
    similar_matches_map: dict[str, List[Tuple[str, float]]],
    group: MerchantLabelGroup,
    batch_size: int = 10,
) -> dict[str, bool]:
    """Batch LLM calls for multiple words."""
    results = {}
    
    # Process in batches
    for i in range(0, len(words), batch_size):
        batch = words[i:i+batch_size]
        
        # Build batch prompt
        prompt = self._build_batch_prompt(batch, similar_matches_map, group)
        
        # Single LLM call for batch
        response = await self.llm.invoke(prompt)
        
        # Parse batch results
        batch_results = self._parse_batch_response(response, batch)
        results.update(batch_results)
    
    return results
```

**Impact**: 10x reduction in LLM calls (10 words per call instead of 1)

### 4. Parallel Word Processing Within Merchant ✅ MEDIUM IMPACT
**Current**: Process word 1, then 2, then 3...
**Optimized**: Process multiple words concurrently

```python
async def _identify_outliers(
    self,
    group: MerchantLabelGroup,
    max_word_concurrency: int = 20,
    ...
):
    # Get all words ready
    label_records = [(chroma_id, record) for ...]
    
    semaphore = asyncio.Semaphore(max_word_concurrency)
    
    async def process_word(chroma_id, record):
        async with semaphore:
            # Query ChromaDB
            similar_matches = await self._find_similar_matches(...)
            # LLM decision
            is_outlier = await self._llm_determine_outlier(...)
            return (record, is_outlier)
    
    tasks = [process_word(cid, rec) for cid, rec in label_records]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    outliers = [r[0] for r in results if r[1] is True]
```

**Impact**: 20x speedup for outlier detection within a merchant

### 5. Caching LLM Decisions ✅ MEDIUM IMPACT
**Current**: Same word evaluated multiple times
**Optimized**: Cache decisions for identical words

```python
class LabelHarmonizer:
    def __init__(self, ...):
        self._llm_cache: dict[str, bool] = {}  # word_text -> is_outlier
    
    async def _llm_determine_outlier(self, word, ...):
        # Check cache
        cache_key = f"{word.word_text}|{group.label_type}|{group.merchant_name}"
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]
        
        # Make LLM call
        is_outlier = await self._make_llm_call(...)
        
        # Cache result
        self._llm_cache[cache_key] = is_outlier
        return is_outlier
```

**Impact**: Eliminates redundant LLM calls for duplicate words

### 6. Skip LLM for High-Confidence Cases ✅ LOW-MEDIUM IMPACT
**Current**: LLM call for every word
**Optimized**: Skip LLM if word has many similar matches

```python
async def _llm_determine_outlier(self, word, similar_matches, ...):
    # Skip LLM if word has many similar matches (high confidence it's valid)
    if len(similar_matches) >= 10:
        return False  # Not an outlier
    
    # Skip LLM if word has zero similar matches (obvious outlier)
    if len(similar_matches) == 0:
        return True  # Is an outlier
    
    # Use LLM for ambiguous cases (1-9 similar matches)
    return await self._make_llm_call(...)
```

**Impact**: Reduces LLM calls by 50-70% (most words are obvious)

## Production Architecture

### Option 1: Single Process with Async Concurrency
```python
# Process all label types in parallel
label_types = ["GRAND_TOTAL", "SUBTOTAL", "DATE", "MERCHANT_NAME", ...]
results = await harmonizer.harmonize_multiple_label_types(
    label_types=label_types,
    max_concurrency=5,  # 5 label types at once
)
```

**Pros**: Simple, single process, easy to debug
**Cons**: Limited by single machine resources

### Option 2: Distributed Processing (AWS Lambda/ECS)
```python
# Each Lambda processes one merchant group
# Orchestrator (Step Functions) coordinates
# DynamoDB stores intermediate results
```

**Pros**: Scales infinitely, fault-tolerant
**Cons**: More complex, requires infrastructure

### Option 3: Batch Processing with Queue
```python
# SQS queue: One message per merchant group
# Workers: Process messages concurrently
# Results: Stored in DynamoDB/S3
```

**Pros**: Good balance, scalable, fault-tolerant
**Cons**: Requires queue infrastructure

## Recommended Implementation Plan

### Phase 1: Quick Wins (1-2 days)
1. ✅ Add parallel merchant processing (10x speedup)
2. ✅ Add caching for LLM decisions (eliminates duplicates)
3. ✅ Skip LLM for high-confidence cases (50% reduction)

**Expected Speedup**: 5-10x faster

### Phase 2: Medium Effort (3-5 days)
4. ✅ Batch LLM calls (10 words per call)
5. ✅ Parallel word processing within merchant (20x speedup)

**Expected Speedup**: Additional 10-20x faster

### Phase 3: Production Scale (1-2 weeks)
6. ✅ Multiple label types in parallel
7. ✅ Distributed processing (Lambda/ECS)
8. ✅ Progress tracking and resumability

**Expected Speedup**: Scales to any number of labels

## Example: Combined Optimization

```python
async def harmonize_all_labels_optimized(
    self,
    label_types: list[str] = None,
    max_label_concurrency: int = 5,
    max_merchant_concurrency: int = 10,
    max_word_concurrency: int = 20,
    llm_batch_size: int = 10,
    skip_high_confidence: bool = True,
):
    """
    Fully optimized harmonization:
    - Multiple label types in parallel
    - Multiple merchants in parallel
    - Multiple words in parallel
    - Batched LLM calls
    - Caching
    - High-confidence skipping
    """
    # Process all label types concurrently
    # Within each label type, process merchants concurrently
    # Within each merchant, process words concurrently
    # Batch LLM calls when possible
    # Cache and skip when appropriate
```

**Expected Performance**:
- Current: 520 words × 1.5s = 13 minutes per merchant
- Optimized: 520 words ÷ 20 concurrency ÷ 10 batch = ~4 seconds per merchant
- **Speedup: ~200x faster** 🚀

## Monitoring & Observability

1. **Progress Tracking**: Track completion per label type/merchant
2. **Metrics**: LLM calls, cache hits, processing time
3. **Error Handling**: Retry logic, dead letter queue
4. **Cost Tracking**: LLM API costs per label type

## Cost Considerations

- **LLM Calls**: Batch calls reduce cost (10 words per call = 10x cheaper)
- **Caching**: Reduces redundant calls
- **Concurrency**: More concurrent = faster but higher peak costs
- **Infrastructure**: Distributed = more AWS costs but faster

## Next Steps

1. Implement Phase 1 optimizations (parallel merchants + caching)
2. Test with small dataset
3. Measure performance improvement
4. Iterate based on results
