## LLM Review Concurrency: Why We Hit 429s

This document explains how the current Step Function structure drives
high concurrent LLM traffic and produces 429 rate-limit responses.

### Current Step Function Structure

The Label Evaluator state machine fans out work across three nested maps
defined in `infra/label_evaluator_step_functions/infrastructure.py`.

1) **ProcessMerchants** (outer Map)
   - `MaxConcurrency: 3`
   - Each merchant is processed in parallel up to this limit.

2) **ProcessBatches** (middle Map)
   - `MaxConcurrency: max_concurrency`
   - `max_concurrency` is configurable via Pulumi config, default `10`.
   - Each merchant’s receipt batches are processed in parallel.

3) **ProcessReceipts** (inner Map)
   - `MaxConcurrency: 5`
   - Each batch’s receipts are processed in parallel.

Each receipt follows: `FetchReceiptData` -> `EvaluateLabels` -> `LLMReview`
when `skip_llm_review` is `false`. So LLMReview is invoked once per receipt.

### LLMReview Lambda Concurrency

Inside `infra/label_evaluator_step_functions/lambdas/llm_review.py`,
LLMReview runs multiple LLM calls concurrently:

- `MAX_CONCURRENT_LLM_CALLS = 3`
- Uses an `asyncio.Semaphore` to allow 3 in-flight LLM calls per Lambda
  invocation.
- Issues are chunked into fixed-size batches of 20, but each chunk still
  uses the same per-Lambda concurrency.

### Why This Multiplies Concurrency

In the worst case (defaults):

- ProcessMerchants: 3 merchants
- ProcessBatches: 10 batches per merchant
- ProcessReceipts: 5 receipts per batch
  - **Concurrent LLMReview Lambdas:** 3 * 10 * 5 = 150
  - **Concurrent LLM calls:** 150 * 3 = 450

This concurrency is enough to trigger Ollama rate limits. The Step Function
itself retries LLMReview on task failure, while the LLMReview Lambda also
retries 429s internally. That combination increases pressure when bursts
occur.

### Summary of Root Cause

The Step Function’s nested Map structure (3 layers of fan-out) schedules
large numbers of LLMReview Lambdas concurrently. Each LLMReview Lambda
then performs up to 3 concurrent LLM calls. The multiplicative effect
creates a high burst of parallel LLM requests, leading to 429 responses.
