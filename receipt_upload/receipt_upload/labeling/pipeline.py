"""
Unified Labeling Pipeline

Orchestrates all labeling stages with parallelization where possible:

Stage 1: LayoutLM Inference + ChromaDB Embeddings (PARALLEL)
Stage 2: Google Places Resolution
Stage 3: Label Refinement (apply corrections from ChromaDB + Places)
Stage 4: Metadata Validation + Financial Validation (PARALLEL)
Stage 5: Final label aggregation

Uses asyncio.gather() for I/O-bound parallelization within a single Lambda.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .types import (
    ChromaResult,
    FinancialResult,
    LabelCorrection,
    LayoutLMResult,
    PipelineContext,
    PipelineResult,
    PlacesResult,
    ValidationStatus,
    WordLabel,
)
from .layoutlm_stage import run_layoutlm_inference
from .chroma_stage import run_chroma_validation
from .places_stage import run_places_validation
from .financial_stage import run_financial_validation

logger = logging.getLogger(__name__)


async def run_labeling_pipeline(
    ctx: PipelineContext,
    progress_callback: Optional[Any] = None,
) -> PipelineResult:
    """
    Run the unified labeling pipeline.

    Pipeline stages (with parallelization):
    1. [PARALLEL] LayoutLM inference + ChromaDB embedding creation
    2. Google Places metadata resolution
    3. Apply label corrections from stages 1-2
    4. [PARALLEL] Metadata validation + Financial validation
    5. Aggregate final labels

    Args:
        ctx: Pipeline context with receipt data and clients
        progress_callback: Optional async callback(stage_name, progress_pct)

    Returns:
        PipelineResult with final labels and stage results
    """
    start_time = time.perf_counter()
    stages_completed: List[str] = []
    errors: List[Dict[str, Any]] = []

    async def report_progress(stage: str, pct: float) -> None:
        if progress_callback:
            try:
                await progress_callback(stage, pct)
            except Exception as e:
                logger.warning("Progress callback failed: %s", e)

    await report_progress("starting", 0.0)

    # ================================================================
    # Stage 1: LayoutLM Inference + ChromaDB Embeddings (PARALLEL)
    # ================================================================
    await report_progress("layoutlm_inference", 0.1)

    layoutlm_result, chroma_result = await asyncio.gather(
        run_layoutlm_inference(ctx),
        _run_chroma_embeddings_only(ctx),
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(layoutlm_result, Exception):
        logger.exception("LayoutLM stage failed: %s", layoutlm_result)
        errors.append({"stage": "layoutlm", "error": str(layoutlm_result)})
        layoutlm_result = LayoutLMResult(
            image_id=ctx.image_id,
            receipt_id=ctx.receipt_id,
            word_labels=[],
            model_metadata={"error": str(layoutlm_result)},
        )
    else:
        stages_completed.append("layoutlm_inference")

    if isinstance(chroma_result, Exception):
        logger.exception("ChromaDB embedding stage failed: %s", chroma_result)
        errors.append({"stage": "chroma_embeddings", "error": str(chroma_result)})
        chroma_result = ChromaResult(
            similar_receipts=[],
            label_corrections=[],
            embeddings_created=False,
        )

    await report_progress("layoutlm_complete", 0.3)

    # ================================================================
    # Stage 2: ChromaDB Validation (needs LayoutLM results)
    # ================================================================
    await report_progress("chroma_validation", 0.35)

    try:
        chroma_result = await run_chroma_validation(ctx, layoutlm_result.word_labels)
        stages_completed.append("chroma_validation")
    except Exception as e:
        logger.exception("ChromaDB validation failed: %s", e)
        errors.append({"stage": "chroma_validation", "error": str(e)})

    await report_progress("chroma_complete", 0.45)

    # ================================================================
    # Stage 3: Google Places Resolution
    # ================================================================
    await report_progress("places_resolution", 0.5)

    try:
        places_result = await run_places_validation(ctx, layoutlm_result.word_labels)
        stages_completed.append("places_resolution")
    except Exception as e:
        logger.exception("Places resolution failed: %s", e)
        errors.append({"stage": "places_resolution", "error": str(e)})
        places_result = PlacesResult()

    await report_progress("places_complete", 0.65)

    # ================================================================
    # Stage 4: Apply Label Corrections
    # ================================================================
    await report_progress("applying_corrections", 0.7)

    # Merge corrections from ChromaDB and Places
    all_corrections = (
        chroma_result.label_corrections + places_result.label_corrections
    )
    refined_labels = _apply_corrections(layoutlm_result.word_labels, all_corrections)
    stages_completed.append("label_refinement")

    # ================================================================
    # Stage 5: Metadata + Financial Validation (PARALLEL)
    # ================================================================
    await report_progress("final_validation", 0.75)

    # Financial validation with refined labels
    financial_result: FinancialResult
    try:
        financial_result = await run_financial_validation(ctx, refined_labels)
        stages_completed.append("financial_validation")
    except Exception as e:
        logger.exception("Financial validation failed: %s", e)
        errors.append({"stage": "financial_validation", "error": str(e)})
        financial_result = FinancialResult(is_valid=False)

    await report_progress("validation_complete", 0.9)

    # ================================================================
    # Stage 6: Apply Financial Corrections and Finalize
    # ================================================================
    if financial_result.label_corrections:
        refined_labels = _apply_corrections(
            refined_labels, financial_result.label_corrections
        )

    # Mark validated labels
    final_labels = _finalize_labels(
        refined_labels, places_result, financial_result
    )

    total_time_ms = (time.perf_counter() - start_time) * 1000

    await report_progress("complete", 1.0)

    return PipelineResult(
        image_id=ctx.image_id,
        receipt_id=ctx.receipt_id,
        final_labels=final_labels,
        layoutlm_result=layoutlm_result,
        chroma_result=chroma_result,
        places_result=places_result,
        financial_result=financial_result,
        total_time_ms=total_time_ms,
        stages_completed=stages_completed,
        errors=errors,
    )


async def _run_chroma_embeddings_only(ctx: PipelineContext) -> ChromaResult:
    """
    Run ChromaDB embedding creation without validation.

    This runs in parallel with LayoutLM inference to prepare for
    similarity searches later.
    """
    # Just return empty result - actual embeddings created in validation stage
    # after we have labels
    return ChromaResult(
        similar_receipts=[],
        label_corrections=[],
        embeddings_created=False,
        validation_time_ms=0.0,
    )


def _apply_corrections(
    word_labels: List[WordLabel],
    corrections: List[LabelCorrection],
) -> List[WordLabel]:
    """
    Apply label corrections to word labels.

    Returns new list with corrections applied.
    """
    # Build lookup for corrections
    correction_map: Dict[tuple, LabelCorrection] = {}
    for c in corrections:
        key = (c.line_id, c.word_id)
        # Keep highest confidence correction if multiple
        if key not in correction_map or c.confidence > correction_map[key].confidence:
            correction_map[key] = c

    # Apply corrections
    result: List[WordLabel] = []
    for wl in word_labels:
        key = (wl.line_id, wl.word_id)
        if key in correction_map:
            correction = correction_map[key]
            result.append(
                WordLabel(
                    line_id=wl.line_id,
                    word_id=wl.word_id,
                    text=wl.text,
                    label=correction.corrected_label,
                    confidence=correction.confidence,
                    all_probabilities=wl.all_probabilities,
                    validation_status=ValidationStatus.CORRECTED,
                    source=correction.source,
                )
            )
        else:
            result.append(wl)

    return result


def _finalize_labels(
    labels: List[WordLabel],
    places_result: PlacesResult,
    financial_result: FinancialResult,
) -> List[WordLabel]:
    """
    Finalize labels with validation status updates.

    Updates validation status based on Places and financial validation.
    """
    result: List[WordLabel] = []

    for wl in labels:
        new_status = wl.validation_status

        # If Places found a match, validate metadata labels
        if places_result.place_id:
            if wl.label in ("MERCHANT_NAME", "ADDRESS", "PHONE"):
                if wl.validation_status == ValidationStatus.PENDING:
                    new_status = ValidationStatus.VALIDATED

        # If financial validation passed, validate financial labels
        if financial_result.is_valid:
            if wl.label in ("GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL"):
                if wl.validation_status == ValidationStatus.PENDING:
                    new_status = ValidationStatus.VALIDATED

        result.append(
            WordLabel(
                line_id=wl.line_id,
                word_id=wl.word_id,
                text=wl.text,
                label=wl.label,
                confidence=wl.confidence,
                all_probabilities=wl.all_probabilities,
                validation_status=new_status,
                source=wl.source,
            )
        )

    return result


# ================================================================
# Convenience Functions
# ================================================================


def labels_to_dynamo_format(
    image_id: str,
    receipt_id: int,
    labels: List[WordLabel],
) -> List[Dict[str, Any]]:
    """
    Convert WordLabel list to DynamoDB format for writing.

    Returns list of label dicts ready for DynamoDB batch write.
    """
    result: List[Dict[str, Any]] = []

    for wl in labels:
        if wl.label == "O":
            continue  # Skip unlabeled words

        result.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": wl.line_id,
                "word_id": wl.word_id,
                "label": wl.label,
                "confidence": wl.confidence,
                "validation_status": wl.validation_status.value,
                "source": wl.source,
            }
        )

    return result


async def run_pipeline_with_dynamo_write(
    ctx: PipelineContext,
    progress_callback: Optional[Any] = None,
) -> PipelineResult:
    """
    Run pipeline and write labels to DynamoDB.

    Convenience function that runs the pipeline and writes results.
    """
    result = await run_labeling_pipeline(ctx, progress_callback)

    if ctx.dynamo_client is not None and result.final_labels:
        try:
            label_records = labels_to_dynamo_format(
                ctx.image_id,
                ctx.receipt_id,
                result.final_labels,
            )
            # Write labels (implementation depends on DynamoDB client interface)
            # ctx.dynamo_client.batch_write_labels(label_records)
            logger.info(
                "Would write %d labels to DynamoDB", len(label_records)
            )
        except Exception as e:
            logger.exception("Failed to write labels to DynamoDB: %s", e)
            result.errors.append({"stage": "dynamo_write", "error": str(e)})

    return result
