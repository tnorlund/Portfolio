# Dedup Conflict Resolution Gaps

## Problem Statement

When the unified receipt evaluator (`unified_receipt_evaluator.py`) runs Phase 1,
it executes `currency_evaluation` and `metadata_evaluation` concurrently via
`asyncio.gather()`. Both evaluators independently examine words on the receipt and
produce decisions (VALID / INVALID / NEEDS_REVIEW). Because they run in parallel
on the same receipt, **the same word can receive decisions from multiple phases** --
and those decisions can conflict.

After Phase 1 completes, an `apply_phase1_corrections` step is supposed to
reconcile these overlapping decisions before Phase 2 (financial validation) begins.
However, **this step currently records no trace data**, making it impossible to
understand how conflicts were resolved.

## Current State of the Data

Analysis of 588 receipt traces from the label-evaluator-dev LangSmith project:

| Metric | Value |
|--------|-------|
| Total decisions across all phases | 20,543 |
| Words appearing in 2+ phases | 401 |
| Words with **conflicting** decisions across phases | 182 |
| Traces with overlapping currency+metadata decisions | 55 of 588 |
| Overlapping (line_id, word_id) pairs | 119 |
| Pairs with different decisions | 53 |

### Labels Most Affected by Conflicts

| Label | Conflicting Words |
|-------|-------------------|
| LINE_TOTAL | 50 |
| SUBTOTAL | 25 |
| PRODUCT_NAME | 21 |
| TAX | 16 |
| QUANTITY | 11 |
| UNIT_PRICE | 11 |
| GRAND_TOTAL | 11 |
| DISCOUNT | 10 |

### Most Common Conflict Patterns

| Phase Pair | Conflicts |
|------------|-----------|
| currency_evaluation vs financial_validation | 139 |
| currency_evaluation vs metadata_evaluation | 53 |

## What Data Is Missing

The `apply_phase1_corrections` span in **all 588 traces** has:

```json
{
  "inputs": {},
  "outputs": {}
}
```

Both inputs and outputs are empty dicts. This means:

1. **No record exists of which evaluator "won"** when currency and metadata disagree.
2. **Resolution logic is not recorded** -- was it last-writer-wins, confidence
   comparison, phase priority, or something else?
3. **No confidence comparison** between the competing decisions.
4. **No audit trail** for corrections applied to DynamoDB labels.

## What Should Be Traced

The `apply_phase1_corrections` child trace (line ~892 of `unified_receipt_evaluator.py`)
currently uses a bare `with child_trace(...)` context manager that records nothing.
It should record:

### Inputs

```json
{
    "currency_decisions": [
        {
            "line_id": 12, "word_id": 1,
            "decision": "INVALID", "confidence": "high",
            "suggested_label": "LINE_TOTAL"
        },
        # ...
    ],
    "metadata_decisions": [
        {
            "line_id": 12, "word_id": 1,
            "decision": "VALID", "confidence": "medium",
            "suggested_label": null
        },
        # ...
    ],
    "overlapping_words": 3,
    "conflicting_words": 1
}
```

### Outputs

```json
{
    "resolutions": [
        {
            "line_id": 12,
            "word_id": 1,
            "word_text": "$19.99",
            "current_label": "LINE_TOTAL",
            "currency_decision": "INVALID",
            "currency_confidence": "high",
            "metadata_decision": "VALID",
            "metadata_confidence": "medium",
            "winner": "currency",           # which evaluator's decision was applied
            "resolution_reason": "higher_confidence",  # why it won
            "applied_label": "PRODUCT_NAME",  # what label was written to DynamoDB
            "was_corrected": true            # whether a DynamoDB write happened
        }
    ],
    "total_corrections_applied": 1,
    "resolution_strategy": "confidence_priority"  # or "phase_priority", "last_writer"
}
```

## How the Step Function Should Be Instrumented

### Current Code (lines ~887-925 of `unified_receipt_evaluator.py`)

```python
if dynamo_table:
    with child_trace("apply_phase1_corrections", trace_ctx):
        # ... applies corrections but records nothing
```

### Proposed Instrumentation

```python
if dynamo_table:
    # 1. Identify overlaps BEFORE applying corrections
    currency_by_word = {
        (d["issue"]["line_id"], d["issue"]["word_id"]): d
        for d in currency_result
    }
    metadata_by_word = {
        (d["issue"]["line_id"], d["issue"]["word_id"]): d
        for d in metadata_result
    }
    overlapping_keys = set(currency_by_word) & set(metadata_by_word)
    conflicting = [
        k for k in overlapping_keys
        if currency_by_word[k]["llm_review"]["decision"]
        != metadata_by_word[k]["llm_review"]["decision"]
    ]

    # 2. Record inputs and outputs in the trace
    correction_trace_ctx = start_child_trace(
        "apply_phase1_corrections",
        trace_ctx,
        inputs={
            "currency_invalid_count": len([
                d for d in currency_result
                if d["llm_review"]["decision"] == "INVALID"
            ]),
            "metadata_invalid_count": len([
                d for d in metadata_result
                if d["llm_review"]["decision"] == "INVALID"
            ]),
            "overlapping_words": len(overlapping_keys),
            "conflicting_words": len(conflicting),
        },
    )

    try:
        resolutions = []
        # ... existing apply logic, but for each conflict,
        #     record which evaluator won and why ...

        end_child_trace(
            correction_trace_ctx,
            outputs={
                "resolutions": resolutions,
                "total_corrections_applied": applied_count,
                "resolution_strategy": "confidence_priority",
            },
        )
    except Exception:
        end_child_trace(correction_trace_ctx, error=traceback.format_exc())
        raise
```

### Key Changes Required

1. **Switch from `with child_trace(...)` to `start_child_trace()` / `end_child_trace()`**
   so that inputs and outputs can be passed explicitly.

2. **Compute overlaps before applying** by indexing both `currency_result` and
   `metadata_result` by `(line_id, word_id)`.

3. **Define a resolution strategy** and record it. Currently both currency and
   metadata corrections are applied independently (currency first, then metadata),
   which means metadata silently overwrites currency corrections for the same word.
   This is effectively a "last-writer-wins / metadata-priority" strategy, but it
   is not documented or recorded.

4. **Record per-word resolution details** including both decisions, both confidences,
   which won, and what label was written.

## Impact on the Viz-Cache

The journey viz-cache (`evaluator_journey_viz_cache.py`) currently has no way to
determine how conflicts were resolved. For all 182 conflicting words, the
`final_outcome` field simply uses the last phase's decision in trace order, which
may not reflect what was actually written to DynamoDB.

Once the step function is instrumented:

1. The viz-cache builder can read the `apply_phase1_corrections` outputs to
   determine the actual resolution for each word.
2. Each journey entry can include a `resolution` field:
   ```json
   {
     "resolution": {
       "winner": "currency",
       "reason": "higher_confidence",
       "applied_label": "PRODUCT_NAME"
     }
   }
   ```
   Instead of the current implicit `"resolution": "unknown"`.
3. The frontend can display which evaluator won and why, enabling operators to
   audit and improve the resolution logic.

## Recommended Next Steps

1. Instrument `apply_phase1_corrections` as described above.
2. Define and document the resolution strategy (confidence priority is recommended
   over last-writer-wins).
3. Re-run the evaluator on a small batch to verify trace outputs.
4. Update `evaluator_journey_viz_cache.py` to consume the new resolution data.
5. Add a conflict resolution summary to the receipt-level output JSON.
