"""
ChromaDB Validation Stage

Uses ChromaDB similarity search to validate and refine LayoutLM predictions.
Finds similar receipts/words and uses consensus to correct low-confidence labels.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .types import (
    ChromaResult,
    LabelCorrection,
    PipelineContext,
    WordLabel,
)

logger = logging.getLogger(__name__)

# Minimum similarity threshold for considering matches
MIN_SIMILARITY_THRESHOLD = 0.75

# Confidence threshold below which we consider corrections
LOW_CONFIDENCE_THRESHOLD = 0.85


async def run_chroma_validation(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
) -> ChromaResult:
    """
    Validate labels using ChromaDB similarity search.

    1. Query similar words/lines from ChromaDB
    2. Use consensus from similar receipts to validate/correct labels
    3. Create embeddings for the new receipt's words

    Args:
        ctx: Pipeline context with ChromaDB client
        word_labels: Initial labels from LayoutLM

    Returns:
        ChromaResult with corrections and similar receipts
    """
    start_time = time.perf_counter()

    if ctx.chroma_client is None or ctx.embed_fn is None:
        logger.warning("ChromaDB not configured, skipping validation")
        return ChromaResult(
            similar_receipts=[],
            label_corrections=[],
            embeddings_created=False,
            validation_time_ms=0.0,
        )

    try:
        # Run validation in executor since ChromaDB calls can block
        loop = asyncio.get_event_loop()

        # Find similar receipts using line embeddings
        similar_receipts = await loop.run_in_executor(
            None,
            lambda: _find_similar_receipts(ctx, word_labels),
        )

        # Validate labels against similar receipts
        corrections = await loop.run_in_executor(
            None,
            lambda: _validate_against_similar(ctx, word_labels, similar_receipts),
        )

        # Create embeddings for new receipt (for future searches)
        embeddings_created = await loop.run_in_executor(
            None,
            lambda: _create_embeddings(ctx, word_labels),
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        return ChromaResult(
            similar_receipts=similar_receipts,
            label_corrections=corrections,
            embeddings_created=embeddings_created,
            validation_time_ms=elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("ChromaDB validation failed: %s", e)
        return ChromaResult(
            similar_receipts=[],
            label_corrections=[],
            embeddings_created=False,
            validation_time_ms=elapsed_ms,
        )


def _find_similar_receipts(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
) -> List[Dict[str, Any]]:
    """Find similar receipts using ChromaDB similarity search."""
    similar_receipts: List[Dict[str, Any]] = []

    # Group words by line to form line texts
    lines_by_id: Dict[int, List[str]] = {}
    for wl in word_labels:
        if wl.line_id not in lines_by_id:
            lines_by_id[wl.line_id] = []
        lines_by_id[wl.line_id].append(wl.text)

    # Search for similar lines (focus on top 3 lines which often contain merchant info)
    for line_id in sorted(lines_by_id.keys())[:3]:
        line_text = " ".join(lines_by_id[line_id])
        if len(line_text.strip()) < 3:
            continue

        try:
            query_embedding = ctx.embed_fn([line_text])[0]
            results = ctx.chroma_client.query(
                collection_name="lines",
                query_embeddings=[query_embedding],
                n_results=5,
                include=["metadatas", "documents", "distances"],
            )

            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
                similarity = max(0.0, 1.0 - (dist / 2))
                if similarity >= MIN_SIMILARITY_THRESHOLD:
                    similar_receipts.append(
                        {
                            "chroma_id": doc_id,
                            "text": doc,
                            "similarity": similarity,
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "merchant_name": meta.get("merchant_name"),
                            "place_id": meta.get("place_id"),
                        }
                    )
        except Exception as e:
            logger.warning("Error querying ChromaDB for line %d: %s", line_id, e)

    # Deduplicate by receipt
    seen_receipts: set = set()
    unique_receipts: List[Dict[str, Any]] = []
    for r in similar_receipts:
        key = (r.get("image_id"), r.get("receipt_id"))
        if key not in seen_receipts:
            seen_receipts.add(key)
            unique_receipts.append(r)

    return unique_receipts


def _validate_against_similar(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
    similar_receipts: List[Dict[str, Any]],
) -> List[LabelCorrection]:
    """
    Validate labels against similar receipts.

    For low-confidence labels, check if similar receipts
    have consistent labels for similar words.
    """
    corrections: List[LabelCorrection] = []

    if not similar_receipts:
        return corrections

    # Find low-confidence labels to validate
    low_confidence_labels = [
        wl for wl in word_labels if wl.confidence < LOW_CONFIDENCE_THRESHOLD
    ]

    for wl in low_confidence_labels:
        # Query similar words
        try:
            query_embedding = ctx.embed_fn([wl.text])[0]
            results = ctx.chroma_client.query(
                collection_name="words",
                query_embeddings=[query_embedding],
                n_results=10,
                include=["metadatas", "distances"],
            )

            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            # Count labels from similar words
            label_votes: Dict[str, int] = {}
            for meta, dist in zip(metadatas, distances):
                similarity = max(0.0, 1.0 - (dist / 2))
                if similarity >= MIN_SIMILARITY_THRESHOLD:
                    label = meta.get("label")
                    if label:
                        label_votes[label] = label_votes.get(label, 0) + 1

            # If there's a consensus different from current label
            if label_votes:
                top_label = max(label_votes.items(), key=lambda x: x[1])
                if top_label[0] != wl.label and top_label[1] >= 3:
                    corrections.append(
                        LabelCorrection(
                            line_id=wl.line_id,
                            word_id=wl.word_id,
                            original_label=wl.label,
                            corrected_label=top_label[0],
                            confidence=min(0.9, top_label[1] / 10),
                            reason=f"ChromaDB consensus: {top_label[1]} similar words labeled as {top_label[0]}",
                            source="chroma",
                        )
                    )
        except Exception as e:
            logger.warning(
                "Error validating word (%d, %d): %s", wl.line_id, wl.word_id, e
            )

    return corrections


def _create_embeddings(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
) -> bool:
    """
    Create embeddings for the receipt's words and lines.

    This adds the new receipt to ChromaDB for future similarity searches.
    """
    try:
        # Group words by line
        lines_by_id: Dict[int, List[WordLabel]] = {}
        for wl in word_labels:
            if wl.line_id not in lines_by_id:
                lines_by_id[wl.line_id] = []
            lines_by_id[wl.line_id].append(wl)

        # Add line embeddings
        line_docs: List[str] = []
        line_ids: List[str] = []
        line_metadatas: List[Dict[str, Any]] = []

        for line_id, words in lines_by_id.items():
            line_text = " ".join(w.text for w in words)
            if len(line_text.strip()) < 2:
                continue

            line_docs.append(line_text)
            line_ids.append(f"{ctx.image_id}_{ctx.receipt_id}_{line_id}")
            line_metadatas.append(
                {
                    "image_id": ctx.image_id,
                    "receipt_id": ctx.receipt_id,
                    "line_id": line_id,
                }
            )

        if line_docs:
            line_embeddings = ctx.embed_fn(line_docs)
            ctx.chroma_client.add(
                collection_name="lines",
                ids=line_ids,
                embeddings=line_embeddings,
                documents=line_docs,
                metadatas=line_metadatas,
            )

        # Add word embeddings for labeled words
        word_docs: List[str] = []
        word_ids: List[str] = []
        word_metadatas: List[Dict[str, Any]] = []

        for wl in word_labels:
            if wl.label == "O" or len(wl.text.strip()) < 2:
                continue

            word_docs.append(wl.text)
            word_ids.append(f"{ctx.image_id}_{ctx.receipt_id}_{wl.line_id}_{wl.word_id}")
            word_metadatas.append(
                {
                    "image_id": ctx.image_id,
                    "receipt_id": ctx.receipt_id,
                    "line_id": wl.line_id,
                    "word_id": wl.word_id,
                    "label": wl.label,
                    "validation_status": wl.validation_status.value,
                }
            )

        if word_docs:
            word_embeddings = ctx.embed_fn(word_docs)
            ctx.chroma_client.add(
                collection_name="words",
                ids=word_ids,
                embeddings=word_embeddings,
                documents=word_docs,
                metadatas=word_metadatas,
            )

        logger.info(
            "Created embeddings: %d lines, %d words", len(line_docs), len(word_docs)
        )
        return True

    except Exception as e:
        logger.exception("Failed to create embeddings: %s", e)
        return False
