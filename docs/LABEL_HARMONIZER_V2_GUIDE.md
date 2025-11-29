# Label Harmonizer V2 Guide

## Overview

The Label Harmonizer V2 is an LLM-powered tool that identifies and corrects mislabeled words in receipt data. It uses semantic analysis to find outliers - words that don't belong in their assigned label type.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Step Function: label-harmonizer-dev-sf                         │
├─────────────────────────────────────────────────────────────────┤
│  1. prepare_labels     → Query DynamoDB, group by merchant      │
│  2. flatten_groups     → Create work items (batches of 100)     │
│  3. harmonize_labels   → LLM outlier detection (parallel)       │
│  4. aggregate_results  → Combine results, upload report         │
└─────────────────────────────────────────────────────────────────┘
```

## Business Logic

### What It Does

1. **Groups labels by merchant + type** (e.g., all MERCHANT_NAME labels from "Vons")
2. **Computes consensus** - Most common label text becomes the consensus
3. **LLM outlier detection** - Asks "Does this word belong in this label type?"
4. **Suggests corrections** - For outliers, suggests the correct label type

### Example

```
Input: 85 MERCHANT_NAME labels from "Vons"
  - "Vons" (60 occurrences) ✅ Valid
  - "Store" (5 occurrences) ❌ Outlier → ADDRESS_LINE
  - "for" (3 occurrences) ❌ Outlier → (no suggestion)
  - "Main:" (2 occurrences) ❌ Outlier → PHONE_NUMBER

Output:
  - Consensus: "Vons"
  - Outliers: 10 found
  - Labels needing update: 10
```

## DynamoDB Changes (when dry_run=False)

| Scenario | Old Label | New Label |
|----------|-----------|-----------|
| Outlier with suggested type | Mark `INVALID` | Create with suggested type, status=`PENDING` |
| Outlier without suggestion | Mark `INVALID` | (none created) |
| Non-outlier needing consensus | Mark `INVALID` | Create with consensus, status=`VALID` |

**Safety**: Old labels are never deleted - they're marked INVALID for audit trail.

## Configuration

### Step Function Input

```json
{
  "label_types": ["MERCHANT_NAME"],
  "dry_run": true,
  "min_confidence": 75.0,
  "max_merchants": 5,
  "langchain_project": "label-harmonizer-prod"
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `label_types` | All CORE_LABELS | Which label types to process |
| `dry_run` | `true` | If false, actually updates DynamoDB |
| `min_confidence` | `70.0` | Minimum confidence to apply changes |
| `max_merchants` | `null` | Limit merchants (for testing) |
| `langchain_project` | `label-harmonizer` | LangSmith project name |

## Observability

### LangSmith Traces

All LLM calls are traced in LangSmith with:
- Proper nesting under root trace
- Tags: `label-harmonizer-v2`, `merchant:{name}`, `label-type:{type}`
- Metadata: word_text, confidence, validation_status

### CloudWatch Metrics (EMF)

Namespace: `LabelHarmonizer`

| Metric | Dimension | Description |
|--------|-----------|-------------|
| `OutliersDetected` | LabelType | Number of outliers found |
| `LabelsProcessed` | LabelType | Labels analyzed per batch |
| `ProcessingTimeSeconds` | LabelType | Batch processing time |
| `BatchSucceeded` | LabelType | Successful batch count |
| `BatchFailed` | LabelType | Failed batch count |

## Rollout Strategy

### Phase 1: Dry Run (Current)
- `dry_run: true`
- Review results in S3
- Check LangSmith for decision quality

### Phase 2: Small Batch Test
- `dry_run: false`
- `max_merchants: 1`
- `min_confidence: 80.0`
- Verify DynamoDB changes

### Phase 3: Single Label Type
- `dry_run: false`
- `label_types: ["MERCHANT_NAME"]`
- `min_confidence: 75.0`

### Phase 4: Full Production
- `dry_run: false`
- All label types
- `min_confidence: 70.0`

## Future: Edge Case Learning

The audit trail (VALID/INVALID labels) enables automatic prompt improvement:

```
┌─────────────────────────────────────────────────────────────────┐
│  1. MINE: Find false positives from human corrections           │
│  2. PATTERN: Extract common error patterns                      │
│  3. UPDATE: Add edge cases to prompts                           │
│  4. TRACK: Measure error rate improvement                       │
└─────────────────────────────────────────────────────────────────┘
```

This is known as:
- **DSPy-style Optimization** (Stanford)
- **Prompt Refinement Loop**
- **Active Learning for Prompts**

## Troubleshooting

### Rate Limiting (Ollama)
- Symptom: "too many concurrent requests" errors
- Solution: `max_concurrent_llm_calls` reduced to 2 with exponential backoff

### Lambda Timeout
- Symptom: Batch doesn't complete in 15 minutes
- Solution: Batches limited to 100 labels, 50 LLM checks max

### Low Success Rate
- Check LangSmith for error patterns
- Increase `min_confidence` threshold
- Review specific outlier decisions

## Files

| File | Purpose |
|------|---------|
| `receipt_agent/tools/label_harmonizer_v2.py` | Core business logic |
| `infra/label_harmonizer_step_functions/` | AWS infrastructure |
| `infra/.../lambdas/harmonize_labels.py` | Lambda handler |
| `receipt_langsmith/` | LangSmith query utilities |

