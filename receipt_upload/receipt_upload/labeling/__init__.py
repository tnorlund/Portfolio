"""
Unified Labeling Pipeline

This module provides a complete labeling pipeline for receipts that combines:
- LayoutLM token classification for initial label predictions
- ChromaDB similarity search for label validation and refinement
- Google Places API for merchant metadata validation
- Financial validation for totals, subtotals, and tax

Usage:
    from receipt_upload.labeling import run_labeling_pipeline, PipelineContext

    ctx = PipelineContext(
        image_id="abc123",
        receipt_id=1,
        receipt_text="...",
        words=[...],
        lines=[...],
        dynamo_client=dynamo,
        chroma_client=chroma,
        places_client=places,
        layoutlm_inference=inference,
    )

    result = await run_labeling_pipeline(ctx)
    print(f"Found {len(result.final_labels)} labels")

Pipeline Stages:
    1. [PARALLEL] LayoutLM Inference + ChromaDB Embeddings
    2. ChromaDB Validation (uses similar receipts to validate)
    3. Google Places Resolution (ground truth for metadata)
    4. Label Refinement (apply corrections)
    5. Financial Validation (check math consistency)
    6. Final aggregation
"""

from .types import (
    ChromaResult,
    FinancialResult,
    LabelConfidence,
    LabelCorrection,
    LayoutLMResult,
    PipelineContext,
    PipelineResult,
    PlacesResult,
    ValidationStatus,
    WordLabel,
)

from .pipeline import (
    labels_to_dynamo_format,
    run_labeling_pipeline,
    run_pipeline_with_dynamo_write,
)

from .layoutlm_stage import (
    extract_labeled_entities,
    get_address_text,
    get_merchant_text,
    get_phone_text,
    run_layoutlm_inference,
)

from .chroma_stage import run_chroma_validation
from .places_stage import run_places_validation
from .financial_stage import run_financial_validation, validate_line_item

__all__ = [
    # Main pipeline
    "run_labeling_pipeline",
    "run_pipeline_with_dynamo_write",
    "labels_to_dynamo_format",
    # Context and results
    "PipelineContext",
    "PipelineResult",
    "WordLabel",
    "LabelCorrection",
    # Stage results
    "LayoutLMResult",
    "ChromaResult",
    "PlacesResult",
    "FinancialResult",
    # Enums
    "ValidationStatus",
    "LabelConfidence",
    # Individual stages (for testing or custom pipelines)
    "run_layoutlm_inference",
    "run_chroma_validation",
    "run_places_validation",
    "run_financial_validation",
    # Utilities
    "extract_labeled_entities",
    "get_merchant_text",
    "get_address_text",
    "get_phone_text",
    "validate_line_item",
]
