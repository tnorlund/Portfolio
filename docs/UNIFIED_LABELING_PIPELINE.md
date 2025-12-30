# Unified Receipt Labeling Pipeline

## Overview

This document describes the unified labeling pipeline that processes receipts during upload, combining LayoutLM inference, ChromaDB validation, Google Places metadata, and financial validation into a single coherent flow.

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            IMAGE UPLOAD                                      │
│                     (existing upload_receipt Lambda)                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: OCR + Entity Creation (existing - ~5-10s)                         │
│  ──────────────────────────────────────────────────                         │
│  • Download image from S3                                                    │
│  • Run OCR → Lines, Words, Letters                                           │
│  • Store entities in DynamoDB                                                │
│  Output: image_id, receipt_id, words[], lines[]                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │         PARALLEL BLOCK 1        │
                    │          (can run together)     │
                    ▼                                 ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────────┐
│  STAGE 2A: LayoutLM Inference    │  │  STAGE 2B: ChromaDB Embeddings       │
│  ────────────────────────────    │  │  ───────────────────────────────     │
│  • Load model from S3/SageMaker  │  │  • Generate word embeddings          │
│  • Normalize bounding boxes      │  │  • Generate line embeddings          │
│  • Run inference on all words    │  │  • Store in ChromaDB                 │
│  • Output per-word:              │  │  • Trigger async compaction          │
│    - predicted_label             │  │                                      │
│    - confidence (0-1)            │  │  Output: embeddings in ChromaDB      │
│    - all_probabilities{}         │  │                                      │
│  (~2-5s CPU, <1s GPU)            │  │  (~3-5s)                             │
└──────────────────────────────────┘  └──────────────────────────────────────┘
                    │                                 │
                    └────────────────┬────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: Google Places Resolution (~1-3s)                                   │
│  ─────────────────────────────────────────                                   │
│  • Extract candidate: merchant_name, phone, address from LayoutLM            │
│  • Lookup place_id via:                                                      │
│    1. Phone (95% confidence) - most reliable                                 │
│    2. Address (80% confidence)                                               │
│    3. Text search (60% confidence)                                           │
│  • Get canonical metadata from Google Places                                 │
│                                                                              │
│  Output: place_id, canonical merchant_name, address, phone                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: Label Refinement + Validation (~2-5s)                              │
│  ──────────────────────────────────────────────                              │
│  For each word with LayoutLM prediction:                                     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  HIGH CONFIDENCE (≥0.85)                                             │    │
│  │  → Accept LayoutLM label as VALID                                    │    │
│  │  → Skip ChromaDB lookup (fast path)                                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  MEDIUM CONFIDENCE (0.60-0.85)                                       │    │
│  │  → Query ChromaDB for similar validated words                        │    │
│  │  → If similar words agree with LayoutLM → VALID                      │    │
│  │  → If similar words disagree → NEEDS_REVIEW                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  LOW CONFIDENCE (<0.60)                                              │    │
│  │  → Query ChromaDB for label suggestions                              │    │
│  │  → If strong ChromaDB evidence (≥3 matches, ≥0.75 similarity)        │    │
│  │    → Use ChromaDB label instead                                      │    │
│  │  → Otherwise → NEEDS_REVIEW                                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │         PARALLEL BLOCK 2        │
                    │          (can run together)     │
                    ▼                                 ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────────┐
│  STAGE 5A: Metadata Validation   │  │  STAGE 5B: Financial Validation      │
│  ─────────────────────────────   │  │  ───────────────────────────────     │
│  Using Google Places ground      │  │  Using label_evaluator subagent:     │
│  truth:                          │  │                                      │
│                                  │  │  • GRAND_TOTAL = SUBTOTAL + TAX      │
│  • MERCHANT_NAME vs place.name   │  │  • SUBTOTAL = Σ(LINE_TOTAL)          │
│    (similarity > 0.85 = VALID)   │  │  • LINE_TOTAL = QTY × UNIT_PRICE     │
│  • ADDRESS_LINE vs place.address │  │                                      │
│  • PHONE_NUMBER vs place.phone   │  │  Flag math inconsistencies           │
│                                  │  │                                      │
│  Override if Google Places       │  │  (~1-2s)                             │
│  strongly contradicts            │  │                                      │
│  (~1s)                           │  │                                      │
└──────────────────────────────────┘  └──────────────────────────────────────┘
                    │                                 │
                    └────────────────┬────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  STAGE 6: Write Labels to DynamoDB (~1s)                                     │
│  ───────────────────────────────────────                                     │
│  Create ReceiptWordLabel entities:                                           │
│                                                                              │
│  • label: MERCHANT_NAME, DATE, AMOUNT, etc.                                  │
│  • validation_status: VALID | NEEDS_REVIEW                                   │
│  • label_proposed_by: "layoutlm+chroma+places"                               │
│  • reasoning: "LayoutLM 92% + 5 similar validated words"                     │
│  • confidence: 0.0-1.0 (combined score)                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Parallelization Summary

| Block | Stages | Why Parallel |
|-------|--------|--------------|
| **Block 1** | 2A (LayoutLM) + 2B (Embeddings) | No dependencies - both only need words/lines |
| **Block 2** | 5A (Metadata) + 5B (Financial) | No dependencies - both only need labels |

## Estimated Timing

| Stage | Sequential | With Parallelization |
|-------|------------|---------------------|
| Stage 1: OCR | 5-10s | 5-10s |
| Stage 2A: LayoutLM | 2-5s (CPU) | } 3-5s |
| Stage 2B: Embeddings | 3-5s | } (parallel) |
| Stage 3: Places | 1-3s | 1-3s |
| Stage 4: Refinement | 2-5s | 2-5s |
| Stage 5A: Metadata | 1s | } 1-2s |
| Stage 5B: Financial | 1-2s | } (parallel) |
| Stage 6: Write | 1s | 1s |
| **Total** | **16-31s** | **13-26s** |

## GPU Acceleration Options

**AWS Lambda does NOT support GPUs natively** (as of late 2025). Options for GPU-accelerated LayoutLM inference:

| Option | Pros | Cons |
|--------|------|------|
| **CPU in Lambda** | Simple, no new infra | Slower (2-5s vs <1s) |
| **SageMaker Endpoint** | GPU support, managed | Cold start, cost |
| **ECS with GPU** | Full control | More infra to manage |
| **Modal/Coiled** | Serverless GPU | Third-party dependency |

**Recommendation:** Start with CPU inference in Lambda. If latency becomes an issue, add SageMaker endpoint (infrastructure already exists from PR #567).

## Implementation Location

The unified labeling logic should be added to:

```
receipt_upload/
└── receipt_upload/
    └── labeling/                    # NEW
        ├── __init__.py
        ├── pipeline.py              # Main pipeline orchestration
        ├── layoutlm_inference.py    # Wrapper for LayoutLMInference
        ├── chroma_validation.py     # ChromaDB label refinement
        ├── places_validation.py     # Google Places metadata validation
        └── financial_validation.py  # Math validation (uses label_evaluator)
```

Called from `ocr_processor.py` after entity creation:

```python
# In ocr_processor.py, after process_native()
if receipt_id is not None:
    from receipt_upload.labeling import run_labeling_pipeline
    labels = await run_labeling_pipeline(
        image_id=image_id,
        receipt_id=receipt_id,
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
    )
```

## User Experience

Users will see progress updates as each stage completes:

1. "Uploading image..." → S3 upload
2. "Processing image..." → OCR
3. "Analyzing receipt..." → LayoutLM + Embeddings (parallel)
4. "Finding merchant..." → Google Places
5. "Validating labels..." → Refinement + Validation (parallel)
6. "Complete!" → Labels written

## Future Enhancements

1. **Streaming updates** - WebSocket for real-time progress
2. **GPU inference** - SageMaker endpoint for <1s LayoutLM
3. **Batch processing** - Process multiple receipts in parallel
4. **Active learning** - Route NEEDS_REVIEW to human, improve model
