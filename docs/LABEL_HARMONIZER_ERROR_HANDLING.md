# Label Harmonizer Error Handling & Rate Limiting

## Current Error Handling

### LLM Call Failures

**Location**: `_llm_determine_outlier()` method (lines 951-979)

**Current Behavior**:
```python
try:
    response = self.llm.invoke(messages)
    # Process response...
    return is_outlier
except Exception as e:
    logger.warning(
        f"Error calling LLM for outlier detection on '{word.word_text}': {e}. "
        f"Falling back to heuristic (similar_matches={len(similar_matches)})"
    )
    # Fallback: use simple heuristic
    return len(similar_matches) < 2
```

**What Happens**:
1. ✅ **Does NOT break the process** - Exception is caught
2. ✅ **Falls back to heuristic** - Uses `similar_matches < 2` as threshold
3. ✅ **Continues processing** - Moves to next word
4. ⚠️ **No retry logic** - Single attempt, then fallback

### ChromaDB Query Failures

**Location**: `_identify_outliers()` method (lines 845-849)

**Current Behavior**:
```python
except Exception as e:
    logger.warning(
        f"Error querying similar words for '{word.word_text}' ({chroma_id}): {e}"
    )
    continue  # Skip this word, continue with next
```

**What Happens**:
1. ✅ **Does NOT break the process** - Exception is caught
2. ✅ **Skips problematic word** - Continues with next word
3. ⚠️ **No retry logic** - Single attempt, then skip

## Rate Limiting Scenarios

### Scenario 1: Ollama Rate Limit (429 Too Many Requests)

**What Happens**:
- LLM call throws exception (likely HTTP 429)
- Exception caught in `_llm_determine_outlier()`
- Falls back to heuristic for that word
- **Process continues** - Other words still processed
- **No retry** - Word gets heuristic decision instead of LLM

**Impact**:
- ✅ Process doesn't crash
- ⚠️ Some words get heuristic instead of LLM decision
- ⚠️ May miss some outliers that LLM would catch

### Scenario 2: Ollama Timeout

**What Happens**:
- LLM call times out (120 second timeout configured)
- Exception caught
- Falls back to heuristic
- **Process continues**

**Impact**:
- ✅ Process doesn't hang
- ⚠️ Slow words get heuristic decision

### Scenario 3: Ollama Service Unavailable (500/503)

**What Happens**:
- LLM call fails with server error
- Exception caught
- Falls back to heuristic
- **Process continues**
- **No retry** - Single attempt only

**Impact**:
- ✅ Process doesn't crash
- ⚠️ Temporary server issues cause heuristic fallback

## Comparison with Other Tools

Other tools in the codebase (e.g., `place_id_finder.py`, `receipt_metadata_finder.py`) have retry logic:

```python
# Retry logic for server errors
max_retries = 3
retry_delay = 2.0  # seconds

for attempt in range(max_retries):
    try:
        result = await llm_call()
        break  # Success
    except Exception as e:
        is_retryable = (
            "500" in str(e) or
            "Internal Server Error" in str(e) or
            "disconnected" in str(e).lower()
        )
        if is_retryable and attempt < max_retries - 1:
            await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff
            continue
        else:
            raise
```

**Label Harmonizer**: ❌ No retry logic
**Other Tools**: ✅ Retry with exponential backoff

## Recommendations

### Option 1: Add Retry Logic (Recommended)

Add retry logic similar to other tools:

```python
async def _llm_determine_outlier(self, word, similar_matches, ...):
    max_retries = 3
    retry_delay = 2.0

    for attempt in range(max_retries):
        try:
            response = await self.llm.invoke(messages)
            # Process response...
            return is_outlier
        except Exception as e:
            error_str = str(e)

            # Check if retryable (rate limit, server error, timeout)
            is_retryable = (
                "429" in error_str or  # Rate limit
                "500" in error_str or  # Server error
                "503" in error_str or  # Service unavailable
                "timeout" in error_str.lower() or
                "rate limit" in error_str.lower()
            )

            if is_retryable and attempt < max_retries - 1:
                wait_time = retry_delay * (attempt + 1)  # Exponential backoff
                logger.warning(
                    f"Retryable error for '{word.word_text}' "
                    f"(attempt {attempt + 1}/{max_retries}): {error_str[:100]}. "
                    f"Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                # Not retryable or max retries reached
                logger.warning(
                    f"Error calling LLM for outlier detection on '{word.word_text}': {e}. "
                    f"Falling back to heuristic (similar_matches={len(similar_matches)})"
                )
                return len(similar_matches) < 2
```

### Option 2: Add Rate Limit Detection

Detect rate limits and add exponential backoff:

```python
# Check for rate limit in response headers
if hasattr(e, 'response') and e.response.status_code == 429:
    retry_after = e.response.headers.get('Retry-After', retry_delay)
    await asyncio.sleep(float(retry_after))
    continue
```

### Option 3: Add Circuit Breaker

Stop making LLM calls if too many failures:

```python
if self._llm_failure_count > 10:
    logger.warning("Too many LLM failures, using heuristic only")
    return len(similar_matches) < 2
```

## Current Resilience

**Good News**:
- ✅ Process doesn't crash on LLM failures
- ✅ Falls back to heuristic (better than nothing)
- ✅ Continues processing other words
- ✅ Individual word failures don't break the whole run

**Areas for Improvement**:
- ⚠️ No retry on transient errors (rate limits, timeouts)
- ⚠️ No exponential backoff
- ⚠️ No rate limit detection
- ⚠️ May use heuristic when LLM would work after retry

## Parallel Execution Impact

With 16 processes running in parallel:
- **Higher rate limit risk** - 16x more concurrent requests
- **Current behavior**: Each process handles failures independently
- **Result**: Some processes may fall back to heuristic while others succeed
- **Overall**: Process continues, but quality may degrade if many LLM calls fail

## Summary

**Current State**: ✅ Resilient (doesn't crash) but ⚠️ No retry logic

**If Ollama Rate Limits**:
1. Individual LLM calls fail
2. Fall back to heuristic for those words
3. Process continues
4. Other words still get LLM decisions
5. Final results may have mix of LLM + heuristic decisions

**Recommendation**: Add retry logic with exponential backoff for better resilience.


