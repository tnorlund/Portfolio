"""
Shared utilities for ChromaDB initialization and management.

This module provides centralized functions for loading ChromaDB snapshots
from S3 and initializing clients, as well as query functions for semantic
similarity search on receipt words.
"""

import json
import logging
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, List, Optional, TypedDict

from receipt_chroma.s3 import download_snapshot_atomic
from receipt_dynamo.entities import ReceiptWord

from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
from receipt_agent.config.settings import get_settings
from receipt_agent.utils.chroma_types import extract_query_metadata_rows
from receipt_agent.utils.label_metadata import parse_labels_from_metadata

logger = logging.getLogger(__name__)


# =============================================================================
# Type Definitions for Similar Word Evidence
# =============================================================================


class ValidationRecord(TypedDict, total=False):
    """Record of a validation decision."""

    label: str
    reasoning: Optional[str]
    proposed_by: Optional[str]
    timestamp: str


class SimilarWordEvidence(TypedDict):
    """Evidence from a semantically similar word."""

    word_text: str
    similarity_score: float
    chroma_id: str
    position_x: float
    position_y: float
    position_description: str
    left_neighbor: str
    right_neighbor: str
    current_label: Optional[str]
    label_status: str
    validated_as: list[ValidationRecord]
    invalidated_as: list[ValidationRecord]
    merchant_name: str
    is_same_merchant: bool


class SimilarityDistribution(TypedDict):
    """Distribution of similarity scores."""

    very_high: int  # >= 90%
    high: int  # 70-90%
    medium: int  # 50-70%
    low: int  # < 50%


class LabelDistributionStats(TypedDict):
    """Statistics for a label across similar words."""

    count: int
    valid_count: int
    invalid_count: int
    example_words: list[str]


class MerchantBreakdown(TypedDict):
    """Label counts for a merchant."""

    merchant_name: str
    is_same_merchant: bool
    labels: dict[str, int]


def load_dual_chroma_from_s3(
    chromadb_bucket: str,
    base_chroma_path: Optional[str] = None,
    verify_integrity: bool = False,
) -> tuple[Any, Callable[[list[str]], list[list[float]]]]:
    """
    Load ChromaDB snapshots for both lines and words collections from S3.

    Downloads snapshots to separate directories (lines/ and words/) and sets
    environment variables for DualChromaClient to use them.

    Args:
        chromadb_bucket: S3 bucket name containing ChromaDB snapshots
        base_chroma_path: Base directory for ChromaDB persistence.
                         Defaults to RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY or /tmp/chromadb
        verify_integrity: Whether to verify snapshot integrity (slower but safer)

    Returns:
        Tuple of (chroma_client, embed_fn)

    Raises:
        RuntimeError: If snapshot download or client creation fails
    """
    if base_chroma_path is None:
        base_chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
            os.path.join(tempfile.gettempdir(), "chromadb"),
        )

    lines_path = os.path.join(base_chroma_path, "lines")
    words_path = os.path.join(base_chroma_path, "words")

    # Check if already cached
    lines_db_file = os.path.join(lines_path, "chroma.sqlite3")
    words_db_file = os.path.join(words_path, "chroma.sqlite3")

    if not os.path.exists(lines_db_file) or not os.path.exists(words_db_file):
        # Download lines collection to separate directory
        if not os.path.exists(lines_db_file):
            logger.info(
                "Downloading ChromaDB lines snapshot from s3://%s/lines/",
                chromadb_bucket,
            )
            lines_result = download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="lines",
                local_path=lines_path,
                verify_integrity=verify_integrity,
            )

            if lines_result.get("status") != "downloaded":
                raise RuntimeError(
                    f"Failed to download ChromaDB lines snapshot: {lines_result.get('error')}"
                )

            logger.info(
                "ChromaDB lines snapshot downloaded: version=%s",
                lines_result.get("version_id"),
            )
        else:
            logger.info("ChromaDB lines already cached at %s", lines_path)

        # Download words collection to separate directory
        if not os.path.exists(words_db_file):
            logger.info(
                "Downloading ChromaDB words snapshot from s3://%s/words/",
                chromadb_bucket,
            )
            words_result = download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="words",
                local_path=words_path,
                verify_integrity=verify_integrity,
            )

            if words_result.get("status") != "downloaded":
                raise RuntimeError(
                    f"Failed to download ChromaDB words snapshot: {words_result.get('error')}"
                )

            logger.info(
                "ChromaDB words snapshot downloaded: version=%s",
                words_result.get("version_id"),
            )
        else:
            logger.info("ChromaDB words already cached at %s", words_path)
    else:
        logger.info("ChromaDB already cached at %s", base_chroma_path)

    # Set environment variables for DualChromaClient (separate directories)
    os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_path
    os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_path

    # Create clients using the receipt_agent factory
    settings = get_settings()

    # Verify OpenAI API key is available for embeddings
    if not settings.openai_api_key:
        logger.warning(
            "RECEIPT_AGENT_OPENAI_API_KEY not set - embeddings may fail"
        )
    else:
        logger.info("OpenAI API key available for embeddings")

    chroma_client = create_chroma_client(settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    logger.info("ChromaDB and embeddings loaded and cached")
    return chroma_client, embed_fn


# =============================================================================
# Position Description Utility
# =============================================================================


def describe_position(x: float, y: float) -> str:
    """
    Describe position on receipt.

    Args:
        x: Horizontal position (0=left, 1=right)
        y: Vertical position (0=bottom, 1=top in normalized space)

    Returns:
        Human-readable position like "top-left" or "middle-center"
    """
    # Y: 0=bottom, 1=top in our normalized space
    if y > 0.7:
        v_pos = "top"
    elif y > 0.3:
        v_pos = "middle"
    else:
        v_pos = "bottom"

    if x < 0.33:
        h_pos = "left"
    elif x < 0.66:
        h_pos = "center"
    else:
        h_pos = "right"

    return f"{v_pos}-{h_pos}"


# =============================================================================
# ChromaDB ID Utilities
# =============================================================================


def build_word_chroma_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build ChromaDB ID for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def build_line_chroma_id(image_id: str, receipt_id: int, line_id: int) -> str:
    """Build ChromaDB ID for a visual-row line embedding."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}"
    )


def parse_chroma_id(chroma_id: str) -> tuple[str, int, int, int]:
    """
    Parse a ChromaDB word ID into components.

    Args:
        chroma_id: ChromaDB ID like "IMAGE#abc#RECEIPT#00001#LINE#00002#WORD#00003"

    Returns:
        Tuple of (image_id, receipt_id, line_id, word_id)

    Raises:
        ValueError: If the ID format is invalid
    """
    parts = chroma_id.split("#")
    if len(parts) < 8:
        raise ValueError(f"Invalid chroma_id format: {chroma_id}")
    return (
        parts[1],  # image_id
        int(parts[3]),  # receipt_id
        int(parts[5]),  # line_id
        int(parts[7]),  # word_id
    )


# =============================================================================
# Targeted Boolean Label Query (Optimized for Label Validation)
# =============================================================================


@dataclass
class LabelEvidence:
    """Evidence for or against a specific label from a similar embedding."""

    word_text: str
    similarity_score: float
    chroma_id: str
    label_valid: bool  # True = validated AS this label, False = NOT this label
    merchant_name: str
    is_same_merchant: bool
    position_description: str
    left_neighbor: str
    right_neighbor: str
    evidence_source: str = "words"  # "words" or "lines"


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_row_line_ids(metadata: dict[str, Any]) -> set[int]:
    """Extract row line IDs from line metadata."""
    row_line_ids_raw = metadata.get("row_line_ids")
    row_line_ids: list[int] = []

    if isinstance(row_line_ids_raw, str) and row_line_ids_raw:
        try:
            parsed = json.loads(row_line_ids_raw)
            if isinstance(parsed, list):
                row_line_ids = [_safe_int(v) for v in parsed]
        except (TypeError, ValueError, json.JSONDecodeError):
            row_line_ids = []

    if not row_line_ids:
        line_id = _safe_int(metadata.get("line_id"), default=-1)
        if line_id >= 0:
            row_line_ids = [line_id]

    return {line_id for line_id in row_line_ids if line_id >= 0}


def _result_chroma_id_for_metadata(
    collection_name: str, metadata: dict[str, Any]
) -> str:
    """Construct Chroma ID from metadata for words or lines collections."""
    image_id = str(metadata.get("image_id", ""))
    receipt_id = _safe_int(metadata.get("receipt_id"))
    line_id = _safe_int(metadata.get("line_id"))

    if collection_name == "words":
        return build_word_chroma_id(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=_safe_int(metadata.get("word_id")),
        )

    return build_line_chroma_id(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
    )


def _get_word_query_embedding(
    chroma_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
) -> tuple[list[float], str] | None:
    """Load the query word embedding and canonical ID."""
    word_chroma_id = build_word_chroma_id(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
    )
    try:
        result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )
    except Exception as exc:
        logger.warning("Error getting embedding for %s: %s", word_chroma_id, exc)
        return None

    embeddings = result.get("embeddings")
    if embeddings is None or len(embeddings) == 0:
        logger.warning("No embedding found for %s", word_chroma_id)
        return None

    embedding = embeddings[0]
    if hasattr(embedding, "tolist"):
        embedding = embedding.tolist()

    if not isinstance(embedding, list) or len(embedding) == 0:
        logger.warning("Invalid embedding format for %s", word_chroma_id)
        return None

    return embedding, word_chroma_id


def _get_line_query_embedding(
    chroma_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
) -> tuple[list[float], str] | None:
    """Load line embedding for a line_id, including row-based fallback lookup."""
    line_chroma_id = build_line_chroma_id(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
    )
    try:
        direct_result = chroma_client.get(
            collection_name="lines",
            ids=[line_chroma_id],
            include=["embeddings"],
        )
        direct_embeddings = direct_result.get("embeddings")
        if direct_embeddings is not None and len(direct_embeddings) > 0:
            embedding = direct_embeddings[0]
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            if isinstance(embedding, list) and len(embedding) > 0:
                return embedding, line_chroma_id
    except Exception as exc:
        logger.debug(
            "Direct line lookup failed for %s, trying row fallback: %s",
            line_chroma_id,
            exc,
        )

    # Row-based fallback: line_id may not be the row's primary line id.
    try:
        fallback_result = chroma_client.get(
            collection_name="lines",
            where={
                "$and": [
                    {"image_id": image_id},
                    {"receipt_id": receipt_id},
                ]
            },
            include=["metadatas", "embeddings"],
        )
    except Exception as exc:
        logger.warning(
            "Error looking up row embedding for line %s: %s",
            line_chroma_id,
            exc,
        )
        return None

    fallback_metadatas = fallback_result.get("metadatas")
    if fallback_metadatas is None:
        fallback_metadatas = []
    fallback_embeddings = fallback_result.get("embeddings")
    if fallback_embeddings is None:
        fallback_embeddings = []
    fallback_ids = fallback_result.get("ids")
    if fallback_ids is None:
        fallback_ids = []
    target_line_id = _safe_int(line_id, default=-1)

    for idx, metadata in enumerate(fallback_metadatas):
        if not isinstance(metadata, dict):
            continue
        row_line_ids = _extract_row_line_ids(metadata)
        if target_line_id not in row_line_ids:
            continue

        if idx >= len(fallback_embeddings):
            continue
        embedding = fallback_embeddings[idx]
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        if not isinstance(embedding, list) or len(embedding) == 0:
            continue

        if idx < len(fallback_ids):
            matched_id = str(fallback_ids[idx])
        else:
            matched_id = _result_chroma_id_for_metadata("lines", metadata)
        return embedding, matched_id

    logger.warning(
        "No line embedding found for IMAGE#%s RECEIPT#%s LINE#%s",
        image_id,
        receipt_id,
        line_id,
    )
    return None


def _query_label_evidence_for_collection(
    chroma_client: Any,
    collection_name: str,
    query_embedding: list[float],
    exclude_chroma_id: str,
    target_label: str,
    target_merchant: str,
    n_results_per_query: int,
    min_similarity: float,
) -> list[LabelEvidence]:
    """Query one collection for positive and negative label evidence."""
    label_field = f"label_{target_label}"

    def _query_single_value(label_value: bool) -> list[LabelEvidence]:
        try:
            results = chroma_client.query(
                collection_name=collection_name,
                query_embeddings=[query_embedding],
                n_results=n_results_per_query,
                where={label_field: label_value},
                include=["metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning(
                "Error querying %s in %s for %s=%s: %s",
                collection_name,
                target_label,
                label_field,
                label_value,
                exc,
            )
            return []

        metadata_rows = results.get("metadatas", [[]])
        distance_rows = results.get("distances", [[]])
        metadatas = metadata_rows[0] if metadata_rows else []
        distances = distance_rows[0] if distance_rows else []

        evidence_list: list[LabelEvidence] = []
        for metadata, distance in zip(metadatas, distances, strict=True):
            if not isinstance(metadata, dict):
                continue

            result_id = _result_chroma_id_for_metadata(collection_name, metadata)
            if result_id == exclude_chroma_id:
                continue

            try:
                distance_val = float(distance)
            except (TypeError, ValueError):
                continue
            similarity = max(0.0, 1.0 - (distance_val / 2.0))
            if similarity < min_similarity:
                continue

            merchant = str(metadata.get("merchant_name", "Unknown"))
            is_same = merchant.lower() == target_merchant.lower()
            left_neighbor = str(
                metadata.get("left", metadata.get("prev_line", "<EDGE>"))
            )
            right_neighbor = str(
                metadata.get("right", metadata.get("next_line", "<EDGE>"))
            )
            try:
                pos_x = float(metadata.get("x", 0.5))
                pos_y = float(metadata.get("y", 0.5))
            except (TypeError, ValueError):
                pos_x = 0.5
                pos_y = 0.5

            evidence_list.append(
                LabelEvidence(
                    word_text=str(metadata.get("text", "")),
                    similarity_score=round(similarity, 3),
                    chroma_id=result_id,
                    label_valid=label_value,
                    merchant_name=merchant,
                    is_same_merchant=is_same,
                    position_description=describe_position(pos_x, pos_y),
                    left_neighbor=left_neighbor,
                    right_neighbor=right_neighbor,
                    evidence_source=collection_name,
                )
            )

        return evidence_list

    return _query_single_value(True) + _query_single_value(False)


def query_label_evidence(
    chroma_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_label: str,
    target_merchant: str,
    n_results_per_query: int = 15,
    min_similarity: float = 0.70,
    include_collections: tuple[str, ...] = ("words", "lines"),
) -> list[LabelEvidence]:
    """
    Query ChromaDB for evidence FOR and AGAINST a specific label.

    Uses targeted boolean metadata filters across one or more collections.
    For each collection, runs two queries:
    1. Embeddings where label was validated as TRUE (positive evidence)
    2. Embeddings where label was validated as FALSE (negative evidence)

    Args:
        chroma_client: ChromaDB client
        image_id: Image ID of the target word
        receipt_id: Receipt ID of the target word
        line_id: Line ID of the target word
        word_id: Word ID of the target word
        target_label: Label to find evidence for (e.g., "GRAND_TOTAL")
        target_merchant: Merchant name for same-merchant identification
        n_results_per_query: Max results per polarity, per collection
        min_similarity: Minimum similarity threshold (0-1)
        include_collections: Collections to query ("words", "lines")

    Returns:
        LabelEvidence sorted by same_merchant then similarity descending
    """
    all_evidence: list[LabelEvidence] = []

    if "words" in include_collections:
        word_query = _get_word_query_embedding(
            chroma_client=chroma_client,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
        )
        if word_query:
            word_embedding, word_chroma_id = word_query
            all_evidence.extend(
                _query_label_evidence_for_collection(
                    chroma_client=chroma_client,
                    collection_name="words",
                    query_embedding=word_embedding,
                    exclude_chroma_id=word_chroma_id,
                    target_label=target_label,
                    target_merchant=target_merchant,
                    n_results_per_query=n_results_per_query,
                    min_similarity=min_similarity,
                )
            )

    if "lines" in include_collections:
        line_query = _get_line_query_embedding(
            chroma_client=chroma_client,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
        )
        if line_query:
            line_embedding, line_chroma_id = line_query
            all_evidence.extend(
                _query_label_evidence_for_collection(
                    chroma_client=chroma_client,
                    collection_name="lines",
                    query_embedding=line_embedding,
                    exclude_chroma_id=line_chroma_id,
                    target_label=target_label,
                    target_merchant=target_merchant,
                    n_results_per_query=n_results_per_query,
                    min_similarity=min_similarity,
                )
            )

    combined = all_evidence
    combined.sort(
        key=lambda e: (e.is_same_merchant, e.similarity_score),
        reverse=True,
    )

    return combined


def compute_label_consensus(
    evidence: list[LabelEvidence],
    same_merchant_boost: float = 0.1,
) -> tuple[float, int, int]:
    """
    Compute weighted consensus score from label evidence.

    Args:
        evidence: List of LabelEvidence from query_label_evidence()
        same_merchant_boost: Extra weight for same-merchant matches

    Returns:
        Tuple of (consensus_score, positive_count, negative_count)
        - consensus_score: -1.0 to 1.0 (negative = INVALID, positive = VALID)
        - positive_count: Number of positive evidence items
        - negative_count: Number of negative evidence items
    """
    if not evidence:
        return 0.0, 0, 0

    positive_weight = 0.0
    negative_weight = 0.0
    positive_count = 0
    negative_count = 0

    for e in evidence:
        weight = e.similarity_score
        if e.is_same_merchant:
            weight += same_merchant_boost

        if e.label_valid:
            positive_weight += weight
            positive_count += 1
        else:
            negative_weight += weight
            negative_count += 1

    total_weight = positive_weight + negative_weight
    if total_weight == 0:
        return 0.0, positive_count, negative_count

    # Consensus ranges from -1 (all negative) to +1 (all positive)
    consensus = (positive_weight - negative_weight) / total_weight

    return consensus, positive_count, negative_count


def format_label_evidence_for_prompt(
    evidence: list[LabelEvidence],
    target_label: str,
    max_positive: int = 5,
    max_negative: int = 3,
) -> str:
    """
    Format label evidence for inclusion in LLM prompt.

    Args:
        evidence: List of LabelEvidence from query_label_evidence()
        target_label: The label being validated
        max_positive: Max positive examples to show
        max_negative: Max negative examples to show

    Returns:
        Formatted string for LLM prompt
    """
    if not evidence:
        return f"No similar validated evidence found for {target_label}."

    positive = [e for e in evidence if e.label_valid][:max_positive]
    negative = [e for e in evidence if not e.label_valid][:max_negative]

    lines = []

    if positive:
        lines.append(f"Evidence validated AS {target_label}:")
        for e in positive:
            merchant_tag = "(same merchant)" if e.is_same_merchant else ""
            source_tag = "[WORD]" if e.evidence_source == "words" else "[LINE]"
            lines.append(
                f'  - {source_tag} "{e.word_text}" [{e.position_description}] '
                f"{e.similarity_score:.0%} similar {merchant_tag}"
            )

    if negative:
        lines.append(f"Evidence validated as NOT {target_label}:")
        for e in negative:
            merchant_tag = "(same merchant)" if e.is_same_merchant else ""
            source_tag = "[WORD]" if e.evidence_source == "words" else "[LINE]"
            lines.append(
                f'  - {source_tag} "{e.word_text}" [{e.position_description}] '
                f"{e.similarity_score:.0%} similar {merchant_tag}"
            )

    # Add consensus summary
    consensus, pos_count, neg_count = compute_label_consensus(evidence)
    if pos_count + neg_count > 0:
        if consensus > 0.3:
            verdict = "Evidence suggests VALID"
        elif consensus < -0.3:
            verdict = "Evidence suggests INVALID"
        else:
            verdict = "Evidence is mixed"
        lines.append(
            f"\nConsensus: {verdict} "
            f"({pos_count} for, {neg_count} against, score: {consensus:+.2f})"
        )

    return "\n".join(lines)


def query_cascade_evidence(
    chroma_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_label: str,
    target_merchant: str,
    cascade_threshold: float = 0.2,
    n_results_per_query: int = 15,
    min_similarity: float = 0.70,
    line_discount: float = 0.5,
) -> tuple[list[LabelEvidence], float, int, int, bool]:
    """
    Cascade evidence query: words first, lines only if inconclusive.

    Returns:
        Tuple of (evidence, consensus, pos_count, neg_count, lines_queried)
    """
    if not target_label:
        return [], 0.0, 0, 0, False

    # Step 1: Query words only
    word_evidence = query_label_evidence(
        chroma_client=chroma_client,
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        target_label=target_label,
        target_merchant=target_merchant,
        n_results_per_query=n_results_per_query,
        min_similarity=min_similarity,
        include_collections=("words",),
    )

    word_consensus, pos, neg = compute_label_consensus(word_evidence)

    # Step 2: If inconclusive, also query lines
    lines_queried = False
    if abs(word_consensus) < cascade_threshold:
        line_evidence = query_label_evidence(
            chroma_client=chroma_client,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            target_label=target_label,
            target_merchant=target_merchant,
            n_results_per_query=n_results_per_query,
            min_similarity=min_similarity,
            include_collections=("lines",),
        )
        # Discount line similarity scores
        for e in line_evidence:
            e.similarity_score *= line_discount

        all_evidence = word_evidence + line_evidence
        all_evidence.sort(
            key=lambda e: (e.is_same_merchant, e.similarity_score),
            reverse=True,
        )
        lines_queried = True
    else:
        all_evidence = word_evidence

    consensus, pos_count, neg_count = compute_label_consensus(all_evidence)
    return all_evidence, consensus, pos_count, neg_count, lines_queried


# =============================================================================
# ChromaDB Similar Word Query (General Purpose)
# =============================================================================


def query_similar_words(
    chroma_client: Any,
    word_text: str,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_merchant: str,
    n_results: int = 100,
) -> list[SimilarWordEvidence]:
    """
    Query ChromaDB for semantically similar words.

    Returns ALL similar words above a minimum threshold, grouped by merchant.
    The LLM decides what evidence is sufficient.

    Args:
        chroma_client: ChromaDB client (DualChromaClient or similar)
        word_text: Text of the word being queried (for logging)
        image_id: Image ID of the query word
        receipt_id: Receipt ID of the query word
        line_id: Line ID of the query word
        word_id: Word ID of the query word
        target_merchant: Merchant name for same-merchant identification
        n_results: Maximum number of results to return

    Returns:
        List of SimilarWordEvidence, sorted by relevance
    """
    word_chroma_id = build_word_chroma_id(
        image_id, receipt_id, line_id, word_id
    )

    try:
        logger.debug(
            "Querying similar words for '%s' (%s)", word_text, word_chroma_id
        )

        # First, get the target word's embedding
        word_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        embeddings = word_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning("No embedding found for %s", word_chroma_id)
            return []

        embedding = embeddings[0]
        # Handle numpy array - convert to list for ChromaDB query
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        # Query for similar words (no merchant filter - we'll categorize after)
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[embedding],
            n_results=n_results,
            where={"label_status": {"$in": ["validated", "auto_suggested"]}},
            include=["metadatas", "distances"],
        )

        metadatas = extract_query_metadata_rows(results)
        if not metadatas:
            return []

        evidence_list: list[SimilarWordEvidence] = []
        distances = results.get("distances", [[]])[0]

        for metadata, distance in zip(metadatas, distances, strict=True):
            # Convert L2 distance to similarity score (0-1)
            similarity = max(0.0, 1.0 - (distance / 2.0))

            # Skip very low similarity
            if similarity < 0.3:
                continue

            # Skip self
            result_chroma_id = (
                f"IMAGE#{metadata.get('image_id')}"
                f"#RECEIPT#{metadata.get('receipt_id', 0):05d}"
                f"#LINE#{metadata.get('line_id', 0):05d}"
                f"#WORD#{metadata.get('word_id', 0):05d}"
            )
            if result_chroma_id == word_chroma_id:
                continue

            merchant = metadata.get("merchant_name", "Unknown")
            is_same = merchant.lower() == target_merchant.lower()

            valid_labels = parse_labels_from_metadata(
                metadata,
                array_field="valid_labels_array",
            )
            invalid_labels = parse_labels_from_metadata(
                metadata,
                array_field="invalid_labels_array",
            )

            evidence: SimilarWordEvidence = {
                "word_text": metadata.get("text", ""),
                "similarity_score": round(similarity, 3),
                "chroma_id": result_chroma_id,
                "position_x": metadata.get("x", 0.5),
                "position_y": metadata.get("y", 0.5),
                "position_description": describe_position(
                    metadata.get("x", 0.5), metadata.get("y", 0.5)
                ),
                "left_neighbor": metadata.get("left", "<EDGE>"),
                "right_neighbor": metadata.get("right", "<EDGE>"),
                "current_label": metadata.get("label"),
                "label_status": metadata.get("label_status", "unvalidated"),
                "validated_as": [],  # Will be enriched from DynamoDB
                "invalidated_as": [],
                "merchant_name": merchant,
                "is_same_merchant": is_same,
            }

            # Pre-populate validation info from metadata
            for label in valid_labels:
                evidence["validated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,  # Will be fetched from DynamoDB
                        "proposed_by": metadata.get("label_proposed_by"),
                        "timestamp": metadata.get("label_validated_at", ""),
                    }
                )

            for label in invalid_labels:
                evidence["invalidated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,
                        "proposed_by": None,
                        "timestamp": "",
                    }
                )

            evidence_list.append(evidence)

        # Sort by relevance: same merchant + validated + high similarity
        evidence_list.sort(
            key=lambda e: (
                e["is_same_merchant"],
                len(e["validated_as"]) > 0,
                e["similarity_score"],
            ),
            reverse=True,
        )

        return evidence_list

    except Exception as e:
        logger.warning("Error querying similar words: %s", e)
        return []


# =============================================================================
# DynamoDB Enrichment
# =============================================================================


def enrich_evidence_with_dynamo_reasoning(
    evidence_list: list[SimilarWordEvidence],
    dynamo_client: Any,
    limit: int = 20,
) -> list[SimilarWordEvidence]:
    """
    Fetch reasoning from DynamoDB for top similar words.

    Only fetches for the top N most relevant words to limit DynamoDB calls.

    Args:
        evidence_list: List of similar word evidence
        dynamo_client: DynamoDB client with list_receipt_word_labels_for_word()
        limit: Maximum number of words to enrich

    Returns:
        The same list with reasoning fields populated
    """
    for evidence in evidence_list[:limit]:
        try:
            # Parse IDs from chroma_id
            image_id, receipt_id, line_id, word_id = parse_chroma_id(
                evidence["chroma_id"]
            )

            # Fetch labels from DynamoDB (returns tuple: labels, pagination_key)
            labels, _ = dynamo_client.list_receipt_word_labels_for_word(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
            )

            # Enrich validation history with reasoning
            validated_map = {v["label"]: v for v in evidence["validated_as"]}
            invalidated_map = {
                v["label"]: v for v in evidence["invalidated_as"]
            }

            for label in labels:
                if (
                    label.validation_status == "VALID"
                    and label.label in validated_map
                ):
                    validated_map[label.label]["reasoning"] = label.reasoning
                    validated_map[label.label][
                        "proposed_by"
                    ] = label.label_proposed_by
                    validated_map[label.label][
                        "timestamp"
                    ] = label.timestamp_added
                elif (
                    label.validation_status == "INVALID"
                    and label.label in invalidated_map
                ):
                    invalidated_map[label.label]["reasoning"] = label.reasoning
                    invalidated_map[label.label][
                        "proposed_by"
                    ] = label.label_proposed_by

        except Exception as e:
            logger.debug("Could not enrich evidence: %s", e)
            continue

    return evidence_list


# =============================================================================
# Distribution Computation
# =============================================================================


def compute_similarity_distribution(
    evidence_list: list[SimilarWordEvidence],
) -> SimilarityDistribution:
    """
    Compute distribution of similarity scores.

    Args:
        evidence_list: List of similar word evidence

    Returns:
        Distribution with counts in each similarity bucket
    """
    dist: SimilarityDistribution = {
        "very_high": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }

    for e in evidence_list:
        score = e["similarity_score"]
        if score >= 0.9:
            dist["very_high"] += 1
        elif score >= 0.7:
            dist["high"] += 1
        elif score >= 0.5:
            dist["medium"] += 1
        else:
            dist["low"] += 1

    return dist


def compute_label_distribution(
    evidence_list: list[SimilarWordEvidence],
) -> dict[str, LabelDistributionStats]:
    """
    Compute label distribution across similar words.

    Args:
        evidence_list: List of similar word evidence

    Returns:
        Dict mapping label names to their statistics
    """

    def _new_label_stats() -> LabelDistributionStats:
        return {
            "count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "example_words": [],
        }

    label_stats: defaultdict[str, LabelDistributionStats] = defaultdict(
        _new_label_stats
    )

    for e in evidence_list:
        label = e["current_label"]
        if not label:
            continue

        stats = label_stats[label]
        stats["count"] += 1
        stats["valid_count"] += len(e["validated_as"])
        stats["invalid_count"] += len(e["invalidated_as"])

        if len(stats["example_words"]) < 3:
            stats["example_words"].append(e["word_text"])

    return dict(label_stats)


def compute_merchant_breakdown(
    evidence_list: list[SimilarWordEvidence],
) -> list[MerchantBreakdown]:
    """
    Compute label counts by merchant.

    Args:
        evidence_list: List of similar word evidence

    Returns:
        List of merchant breakdowns, sorted by total count descending
    """
    merchant_labels: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"is_same": False, "labels": Counter()}
    )

    for e in evidence_list:
        merchant = e["merchant_name"]
        label = e["current_label"]
        if label:
            merchant_labels[merchant]["labels"][label] += 1
            if e["is_same_merchant"]:
                merchant_labels[merchant]["is_same"] = True

    breakdown: list[MerchantBreakdown] = []
    for merchant, data in sorted(
        merchant_labels.items(), key=lambda x: -sum(x[1]["labels"].values())
    ):
        breakdown.append(
            {
                "merchant_name": merchant,
                "is_same_merchant": data["is_same"],
                "labels": dict(data["labels"]),
            }
        )

    return breakdown


# =============================================================================
# Simple Similar Word Query (from label_evaluator/helpers.py)
# =============================================================================


@dataclass
class SimilarWordResult:
    """Result from ChromaDB similarity search for a word."""

    word_text: str
    similarity_score: float
    label: Optional[str]
    validation_status: Optional[str]
    valid_labels: List[str] = field(default_factory=list)
    invalid_labels: List[str] = field(default_factory=list)
    merchant_name: Optional[str] = None


def query_similar_validated_words(
    word: ReceiptWord,
    chroma_client: Any,
    n_results: int = 10,
    min_similarity: float = 0.7,
    merchant_name: Optional[str] = None,
) -> List[SimilarWordResult]:
    """
    Query ChromaDB for similar words that have validated labels.

    Uses the word's existing embedding from ChromaDB instead of generating
    a new one. This ensures we use the same contextual embedding that was
    created during the embedding pipeline.

    Args:
        word: The ReceiptWord to find similar words for
        chroma_client: ChromaDB client (DualChromaClient or similar)
        n_results: Maximum number of results to return
        min_similarity: Minimum similarity score (0.0-1.0) to include
        merchant_name: Optional merchant name to scope results to same merchant

    Returns:
        List of SimilarWordResult objects, sorted by similarity descending
    """
    if not chroma_client:
        logger.warning("ChromaDB client not provided")
        return []

    try:
        # Build the word's ChromaDB ID
        word_chroma_id = build_word_chroma_id(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )

        # Get the word's existing embedding from ChromaDB
        get_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        if not get_result:
            logger.warning("Word not found in ChromaDB: %s", word_chroma_id)
            return []

        embeddings = get_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning("No embeddings found for word: %s", word_chroma_id)
            return []

        if embeddings[0] is None:
            logger.warning("No embedding found for word: %s", word_chroma_id)
            return []

        # Convert numpy array to list
        try:
            query_embedding = list(embeddings[0])
        except (TypeError, ValueError):
            logger.warning(
                "Invalid embedding format for word: %s",
                word_chroma_id,
            )
            return []

        if not query_embedding:
            logger.warning(
                "Empty embedding found for word: %s",
                word_chroma_id,
            )
            return []

        # Query ChromaDB words collection using the existing embedding
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results * 2
            + 1,  # Fetch more, filter later (+1 for self)
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("ids"):
            return []

        ids = list(results.get("ids", [[]])[0])
        documents = list(results.get("documents", [[]])[0])
        metadatas = extract_query_metadata_rows(results)
        distances = list(results.get("distances", [[]])[0])

        similar_words: List[SimilarWordResult] = []

        for doc_id, doc, meta, dist in zip(
            ids, documents, metadatas, distances
        ):
            # Skip self (the query word)
            if doc_id == word_chroma_id:
                continue

            # Filter by merchant if specified
            if merchant_name:
                result_merchant = meta.get("merchant_name")
                if result_merchant != merchant_name:
                    continue

            # Convert distance to Python float and compute similarity
            try:
                dist_float = float(dist)
            except (TypeError, ValueError):
                logger.debug("Invalid distance value: %s", dist)
                continue

            # Convert L2 distance to similarity (0.0-1.0)
            similarity = max(0.0, 1.0 - (dist_float / 2))

            if similarity < min_similarity:
                continue

            valid_labels = parse_labels_from_metadata(
                meta,
                array_field="valid_labels_array",
            )
            invalid_labels = parse_labels_from_metadata(
                meta,
                array_field="invalid_labels_array",
            )

            similar_words.append(
                SimilarWordResult(
                    word_text=doc or meta.get("text", ""),
                    similarity_score=similarity,
                    label=meta.get("label"),
                    validation_status=meta.get("validation_status"),
                    valid_labels=valid_labels,
                    invalid_labels=invalid_labels,
                    merchant_name=meta.get("merchant_name"),
                )
            )

        # Sort by similarity descending and limit results
        similar_words.sort(key=lambda w: -w.similarity_score)
        return similar_words[:n_results]

    except Exception as e:
        logger.error(
            "Error querying ChromaDB for similar words: %s",
            e,
            exc_info=True,
        )
        return []


def format_similar_words_for_prompt(
    similar_words: List[SimilarWordResult],
    max_examples: int = 5,
) -> str:
    """
    Format similar validated words for inclusion in LLM prompt.

    Prioritizes words with VALID validation status and groups by label.

    Args:
        similar_words: Results from query_similar_validated_words()
        max_examples: Maximum number of examples to include

    Returns:
        Formatted string for LLM prompt
    """
    if not similar_words:
        return "No similar validated words found in database."

    # Prioritize validated words
    validated = [w for w in similar_words if w.validation_status == "VALID"]
    other = [w for w in similar_words if w.validation_status != "VALID"]

    # Take validated first, then fill with others
    examples = validated[:max_examples]
    if len(examples) < max_examples:
        examples.extend(other[: max_examples - len(examples)])

    if not examples:
        return "No similar validated words found in database."

    lines = []
    for w in examples:
        status = (
            f"[{w.validation_status}]"
            if w.validation_status
            else "[unvalidated]"
        )
        label = w.label or "no label"
        lines.append(
            f'- "{w.word_text}" → {label} {status} '
            f"(similarity: {w.similarity_score:.2f})"
        )

        # Add valid/invalid labels if available
        if w.valid_labels:
            lines.append(f"    Valid labels: {', '.join(w.valid_labels)}")
        if w.invalid_labels:
            lines.append(f"    Invalid labels: {', '.join(w.invalid_labels)}")

    return "\n".join(lines)
