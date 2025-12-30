"""
LayoutLM Inference Stage

Wraps the LayoutLMInference class to run token classification
on receipt words and produce initial label predictions.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .types import (
    LayoutLMResult,
    PipelineContext,
    ValidationStatus,
    WordLabel,
)

if TYPE_CHECKING:
    from receipt_layoutlm.inference import LayoutLMInference

logger = logging.getLogger(__name__)


def _confidence_to_status(confidence: float) -> ValidationStatus:
    """Convert confidence score to validation status."""
    if confidence >= 0.9:
        return ValidationStatus.VALIDATED
    return ValidationStatus.PENDING


async def run_layoutlm_inference(
    ctx: PipelineContext,
    layoutlm_inference: Optional["LayoutLMInference"] = None,
) -> LayoutLMResult:
    """
    Run LayoutLM inference to get initial label predictions.

    Args:
        ctx: Pipeline context with receipt data
        layoutlm_inference: Optional pre-initialized LayoutLMInference instance.
                           If not provided, uses ctx.layoutlm_inference.

    Returns:
        LayoutLMResult with word labels and metadata
    """
    start_time = time.perf_counter()

    inference = layoutlm_inference or ctx.layoutlm_inference
    if inference is None:
        logger.warning("LayoutLM inference not available, returning empty result")
        return LayoutLMResult(
            image_id=ctx.image_id,
            receipt_id=ctx.receipt_id,
            word_labels=[],
            model_metadata={"error": "LayoutLM inference not configured"},
            inference_time_ms=0.0,
        )

    try:
        # Run inference in thread pool since it's CPU-bound (or GPU-bound)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: inference.predict_receipt_from_dynamo(
                ctx.dynamo_client,
                ctx.image_id,
                ctx.receipt_id,
            ),
        )

        # Convert LinePrediction results to WordLabel objects
        word_labels: List[WordLabel] = []
        for line_pred in result.lines:
            for idx, (token, label, conf) in enumerate(
                zip(line_pred.tokens, line_pred.labels, line_pred.confidences)
            ):
                word_id = idx + 1  # 1-indexed
                all_probs = None
                if line_pred.all_probabilities:
                    all_probs = line_pred.all_probabilities[idx]

                word_labels.append(
                    WordLabel(
                        line_id=line_pred.line_id,
                        word_id=word_id,
                        text=token,
                        label=label,
                        confidence=conf,
                        all_probabilities=all_probs,
                        validation_status=_confidence_to_status(conf),
                        source="layoutlm",
                    )
                )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        return LayoutLMResult(
            image_id=ctx.image_id,
            receipt_id=ctx.receipt_id,
            word_labels=word_labels,
            model_metadata=result.meta,
            inference_time_ms=elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("LayoutLM inference failed: %s", e)
        return LayoutLMResult(
            image_id=ctx.image_id,
            receipt_id=ctx.receipt_id,
            word_labels=[],
            model_metadata={"error": str(e)},
            inference_time_ms=elapsed_ms,
        )


def extract_labeled_entities(
    word_labels: List[WordLabel],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract labeled entities from word labels.

    Groups words by label type for downstream processing.
    Useful for extracting merchant name, address, phone, totals, etc.

    Returns:
        Dict mapping label type to list of word info dicts
    """
    entities: Dict[str, List[Dict[str, Any]]] = {}

    for wl in word_labels:
        if wl.label == "O":
            continue

        if wl.label not in entities:
            entities[wl.label] = []

        entities[wl.label].append(
            {
                "line_id": wl.line_id,
                "word_id": wl.word_id,
                "text": wl.text,
                "confidence": wl.confidence,
            }
        )

    return entities


def get_merchant_text(word_labels: List[WordLabel]) -> Optional[str]:
    """Extract merchant name from labels."""
    entities = extract_labeled_entities(word_labels)
    merchant_words = entities.get("MERCHANT_NAME", [])
    if not merchant_words:
        return None
    # Sort by line_id, then word_id to get correct order
    merchant_words.sort(key=lambda w: (w["line_id"], w["word_id"]))
    return " ".join(w["text"] for w in merchant_words)


def get_address_text(word_labels: List[WordLabel]) -> Optional[str]:
    """Extract address from labels."""
    entities = extract_labeled_entities(word_labels)
    address_words = entities.get("ADDRESS", [])
    if not address_words:
        return None
    address_words.sort(key=lambda w: (w["line_id"], w["word_id"]))
    return " ".join(w["text"] for w in address_words)


def get_phone_text(word_labels: List[WordLabel]) -> Optional[str]:
    """Extract phone number from labels."""
    entities = extract_labeled_entities(word_labels)
    phone_words = entities.get("PHONE", [])
    if not phone_words:
        return None
    phone_words.sort(key=lambda w: (w["line_id"], w["word_id"]))
    return " ".join(w["text"] for w in phone_words)
