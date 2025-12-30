# Unified Labeling Pipeline

## Overview

The unified labeling pipeline combines LayoutLM token classification, ChromaDB similarity search, and Google Places API to automatically label receipt images with high accuracy.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Receipt Upload Lambda                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                  run_labeling_pipeline()                     │   │
│  │                                                              │   │
│  │  ┌──────────────────┐    ┌──────────────────┐               │   │
│  │  │  LayoutLM        │    │  ChromaDB        │  PARALLEL     │   │
│  │  │  Inference       │    │  Prep            │  (Stage 1)    │   │
│  │  └────────┬─────────┘    └────────┬─────────┘               │   │
│  │           │                       │                          │   │
│  │           └───────────┬───────────┘                          │   │
│  │                       ▼                                      │   │
│  │           ┌──────────────────────┐                           │   │
│  │           │  ChromaDB Validation │  (Stage 2)                │   │
│  │           │  - Find similar      │                           │   │
│  │           │  - Validate labels   │                           │   │
│  │           └──────────┬───────────┘                           │   │
│  │                      ▼                                       │   │
│  │           ┌──────────────────────┐                           │   │
│  │           │  Google Places       │  (Stage 3)                │   │
│  │           │  Resolution          │                           │   │
│  │           └──────────┬───────────┘                           │   │
│  │                      ▼                                       │   │
│  │           ┌──────────────────────┐                           │   │
│  │           │  Apply Corrections   │  (Stage 4)                │   │
│  │           └──────────┬───────────┘                           │   │
│  │                      ▼                                       │   │
│  │           ┌──────────────────────┐                           │   │
│  │           │  Financial           │  (Stage 5)                │   │
│  │           │  Validation          │                           │   │
│  │           └──────────┬───────────┘                           │   │
│  │                      ▼                                       │   │
│  │           ┌──────────────────────┐                           │   │
│  │           │  Final Labels        │  (Stage 6)                │   │
│  │           │  + DynamoDB Write    │                           │   │
│  │           └──────────────────────┘                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Pipeline Stages

### Stage 1: LayoutLM Inference + ChromaDB Prep (PARALLEL)

**LayoutLM Inference:**
- Runs LayoutLM token classification model on receipt words
- Produces initial label predictions with confidence scores
- Labels include: MERCHANT_NAME, ADDRESS, PHONE, DATE, GRAND_TOTAL, SUBTOTAL, TAX, LINE_TOTAL, etc.

**ChromaDB Prep:**
- Prepares for similarity search (runs in parallel to save time)

**Timing:** ~3-8 seconds (CPU) or ~0.5-2 seconds (GPU)

### Stage 2: ChromaDB Validation

- Queries ChromaDB for similar receipts based on line embeddings
- Validates low-confidence labels against consensus from similar receipts
- Creates embeddings for the new receipt (for future searches)

**Timing:** ~1-2 seconds

### Stage 3: Google Places Resolution

- Extracts merchant name, address, phone from initial labels
- Searches Google Places API (phone > address > text search)
- Provides ground truth for merchant metadata
- Generates corrections if Places data differs significantly

**Timing:** ~0.5-2 seconds (cached lookups faster)

### Stage 4: Apply Label Corrections

- Merges corrections from ChromaDB and Places stages
- Applies highest-confidence corrections when multiple sources disagree
- Updates validation status for corrected labels

**Timing:** ~10ms

### Stage 5: Financial Validation

- Validates: Grand Total = Subtotal + Tax (within tolerance)
- Validates: Subtotal = Sum of LINE_TOTAL values
- Detects currency from receipt text
- Proposes corrections for unlabeled amounts near "total" keywords

**Timing:** ~50ms

### Stage 6: Final Labels + DynamoDB Write

- Marks labels as VALIDATED based on Places and financial results
- Writes final labels to DynamoDB

**Timing:** ~100ms (DynamoDB write)

## Total Timing Estimates

| Scenario | Time |
|----------|------|
| Best case (GPU + cached) | ~2-4 seconds |
| Typical (CPU + cached) | ~5-10 seconds |
| Worst case (CPU + uncached) | ~10-15 seconds |

All stages complete well within Lambda's 15-minute timeout.

## Usage

```python
from receipt_upload.labeling import (
    run_labeling_pipeline,
    PipelineContext,
)

# Create context with all clients
ctx = PipelineContext(
    image_id="abc123",
    receipt_id=1,
    receipt_text="WALMART...",
    words=[...],  # From OCR
    lines=[...],  # From OCR
    dynamo_client=dynamo,
    chroma_client=chroma,
    places_client=places,
    layoutlm_inference=inference,
    embed_fn=embedding_function,
)

# Run pipeline with progress callback
async def on_progress(stage: str, pct: float):
    print(f"Pipeline: {stage} ({pct*100:.0f}%)")

result = await run_labeling_pipeline(ctx, progress_callback=on_progress)

# Access results
print(f"Labels: {len(result.final_labels)}")
print(f"Place ID: {result.places_result.place_id}")
print(f"Financial valid: {result.financial_result.is_valid}")
print(f"Time: {result.total_time_ms:.0f}ms")
```

## GPU Options

AWS Lambda doesn't support GPUs natively. Options for GPU inference:

1. **CPU in Lambda (Default)**: Works, 3-8 seconds per receipt
2. **SageMaker Endpoint**: Deploy LayoutLM to SageMaker for <1s inference
3. **ECS with GPU**: Run on EC2 GPU instances (more complex)
4. **Modal/Coiled**: Third-party serverless GPU platforms

Start with CPU in Lambda; add SageMaker if latency becomes an issue.

## Label Types

| Label | Description | Validation Source |
|-------|-------------|-------------------|
| MERCHANT_NAME | Business name | Places API |
| ADDRESS | Street address | Places API |
| PHONE | Phone number | Places API |
| DATE | Transaction date | Pattern matching |
| GRAND_TOTAL | Final amount | Financial validation |
| SUBTOTAL | Pre-tax total | Financial validation |
| TAX | Tax amount | Financial validation |
| LINE_TOTAL | Line item total | Financial validation |
| QUANTITY | Item quantity | Financial validation |
| UNIT_PRICE | Per-unit price | Financial validation |
| ITEM_NAME | Product name | ChromaDB similarity |
| O | Unlabeled | - |

## Removed Agents

This pipeline replaces the following deprecated agents:

- `label_suggestion` - Replaced by LayoutLM + ChromaDB
- `label_validation` - Replaced by unified validation
- `label_harmonizer` - Absorbed by label_evaluator

See CHANGELOG.md for migration details.
