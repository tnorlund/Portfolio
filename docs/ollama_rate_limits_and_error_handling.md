# Ollama Cloud Rate Limits and Error Handling

## Overview

This document describes the rate limit issues encountered when running the Label Evaluator Step Function with LLM-based subagents (Currency and Metadata evaluators), and the design decisions made to handle these issues.

---

## What We Observed

### The Problem

When running the Label Evaluator Step Function against all merchants with `dry_run: false`, we observed significant rate limiting from Ollama Cloud:

```
[ERROR] Currency LLM call failed: too many concurrent requests (status code: 429)
[ERROR] Metadata LLM call failed: too many concurrent requests (status code: 429)
[WARNING] Rate limit error 1/5: too many concurrent requests (status code: 429)
```

### Metrics from a Full Run

| Lambda | Failed (429) | Successful | Failure Rate |
|--------|-------------|------------|--------------|
| evaluate-currency | ~200-206 | ~334-435 | **~33%** |
| evaluate-metadata | ~204-210 | ~334-435 | **~33%** |
| llm-review | 721 | - | High |
| discover-patterns | 4 | - | Low |

**About 1/3 of LLM calls were failing silently** and defaulting to `NEEDS_REVIEW` instead of actually evaluating the labels.

### Root Cause: High Concurrency

The original Step Function configuration allowed too many concurrent LLM calls:

```
ProcessMerchants (3 merchants in parallel)
  └─ ProcessBatches (3 batches per merchant in parallel)
       └─ ProcessReceipts (5 receipts per batch in parallel)
            └─ ParallelEvaluation (3 evaluators per receipt)
                 ├─ EvaluateLabels
                 ├─ EvaluateCurrencyLabels (LLM call)
                 └─ EvaluateMetadataLabels (LLM call)
```

**Maximum concurrent LLM calls:** 3 × 3 × 5 × 2 = **90 concurrent calls**

This far exceeded Ollama Cloud's rate limits (~10 req/s per IP, ~5-10 concurrent connections).

---

## Current Design Decisions

### 1. Silent Failure with NEEDS_REVIEW Default

When an LLM call fails (e.g., 429 rate limit), the Currency and Metadata subagents **do not throw an exception**. Instead, they:

1. Log an error: `Currency LLM call failed: {error}`
2. Return `NEEDS_REVIEW` for all words that would have been evaluated
3. Return a "completed" status to the Step Function

**Code location:** `receipt_agent/agents/label_evaluator/currency_subagent.py:477-493`

```python
logger.error(f"Currency LLM call failed: {e}")
# Return NEEDS_REVIEW for all words
decisions.append({
    ...
    "decision": "NEEDS_REVIEW",
    "reasoning": f"LLM call failed: {e}",
})
```

**Rationale:**
- The Step Function continues processing other receipts
- Failures don't block the entire batch
- Results can be reviewed manually via the `NEEDS_REVIEW` status

**Trade-off:**
- Silent failures can go unnoticed if not monitoring logs
- `NEEDS_REVIEW` decisions may be mistaken for legitimate uncertainty
- The Step Function reports 0 TaskFailed events even when LLM calls fail

### 2. Step Function Retry Policy

The Step Function has retry policies for 429 errors that bubble up:

```python
"Retry": [
    {
        "ErrorEquals": ["OllamaRateLimitError"],
        "IntervalSeconds": 5,
        "MaxAttempts": 3,
        "BackoffRate": 2.0,
    },
    {
        "ErrorEquals": ["States.TaskFailed"],
        "IntervalSeconds": 2,
        "MaxAttempts": 2,
        "BackoffRate": 2.0,
    }
],
```

**Note:** These retries only trigger if the Lambda throws an exception. Since the subagents catch 429 errors and return `NEEDS_REVIEW`, these retries are NOT triggered for rate-limited LLM calls.

### 3. Reduced Concurrency (The Fix)

To reduce rate limit hits, we reduced the Step Function concurrency:

| Setting | Before | After |
|---------|--------|-------|
| ProcessBatches MaxConcurrency | 3 | 2 |
| ProcessReceipts MaxConcurrency | 5 | 2 |

**New maximum concurrent LLM calls:** 3 × 2 × 2 × 2 = **24 concurrent calls**

This is a ~73% reduction in concurrent LLM calls.

---

## Monitoring Rate Limit Issues

### CloudWatch Log Queries

**Check for 429 errors:**
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/label-evaluator-dev-evaluate-currency" \
  --filter-pattern "429" \
  --query 'length(events)'
```

**Check for LLM call failures:**
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/label-evaluator-dev-evaluate-currency" \
  --filter-pattern "Currency LLM call failed" \
  --query 'length(events)'
```

**Check decision distributions:**
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/label-evaluator-dev-evaluate-currency" \
  --filter-pattern "Decisions" \
  --query 'events[*].message'
```

### Warning Signs

1. **High NEEDS_REVIEW counts with 0 VALID/INVALID:**
   ```
   Decisions: {'VALID': 0, 'INVALID': 0, 'NEEDS_REVIEW': 30}
   ```
   This indicates an LLM call failed and all items defaulted to NEEDS_REVIEW.

2. **Step Function shows 0 TaskFailed but logs show errors:**
   This is expected with the current design - errors are caught silently.

3. **High concurrent Lambda invocations:**
   Check CloudWatch metrics for `ConcurrentExecutions` on the LLM-using Lambdas.

---

## Alternative Approaches (Not Currently Implemented)

### Option 1: Re-throw 429 Errors

Instead of catching and defaulting to NEEDS_REVIEW, throw the error so Step Function retries:

```python
except Exception as e:
    if "429" in str(e) or "rate limit" in str(e).lower():
        # Re-throw so Step Function retries
        raise OllamaRateLimitError(str(e))
    # Other errors still default to NEEDS_REVIEW
    logger.error(f"Currency LLM call failed: {e}")
    ...
```

**Pros:**
- Step Function retry policy handles rate limits
- Retries use exponential backoff
- TaskFailed events provide visibility

**Cons:**
- Failed receipts block the batch until retries complete
- May cause cascading delays

### Option 2: Internal Retry with Backoff

Add retry logic inside the subagent before defaulting to NEEDS_REVIEW:

```python
for attempt in range(3):
    try:
        response = llm.invoke(prompt)
        break
    except Exception as e:
        if "429" in str(e) and attempt < 2:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s
            continue
        raise
```

**Pros:**
- Transparent retries without Step Function involvement
- Reduces NEEDS_REVIEW defaults

**Cons:**
- Increases Lambda execution time (and cost)
- May exceed Lambda timeout (300s)

### Option 3: Distributed Rate Limiter (DynamoDB)

Use a DynamoDB-based token bucket to coordinate rate limiting across all Lambdas:

```python
rate_limiter = DistributedRateLimiter(table_name="RateLimiter")
if rate_limiter.acquire(timeout=5):
    response = llm.invoke(prompt)
else:
    raise RateLimitError("Could not acquire token")
```

See `routing_conversation.md` for a complete implementation.

**Pros:**
- Precise control over global request rate
- Works across all concurrent Lambdas

**Cons:**
- Additional DynamoDB table and costs
- More complex implementation
- Adds latency for token acquisition

---

## Current Recommendation

For now, the **reduced concurrency approach** is the simplest fix:

1. `ProcessBatches MaxConcurrency: 2`
2. `ProcessReceipts MaxConcurrency: 2`

This reduces max concurrent LLM calls from ~90 to ~24, which should stay within Ollama Cloud's limits without requiring code changes to the subagents.

**Monitor the next full run** to verify:
- 429 error count is significantly reduced
- NEEDS_REVIEW counts are proportional to actual uncertainty, not failures
- Step Function completes in a reasonable time

---

## Related Files

- `infra/label_evaluator_step_functions/infrastructure.py` - Step Function definition
- `receipt_agent/agents/label_evaluator/currency_subagent.py` - Currency evaluation with error handling
- `receipt_agent/agents/label_evaluator/metadata_subagent.py` - Metadata evaluation with error handling
- `docs/routing_conversation.md` - Detailed conversation about OpenRouter rate limits and scaling patterns
