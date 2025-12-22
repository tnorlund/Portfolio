"""
Shared utilities for ChromaDB initialization and management.

This module provides centralized functions for loading ChromaDB snapshots
from S3 and initializing clients, as well as query functions for semantic
similarity search on receipt words.
"""

import logging
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any, Optional, TypedDict

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
    from receipt_chroma.s3 import download_snapshot_atomic

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
                f"Downloading ChromaDB lines snapshot from s3://{chromadb_bucket}/lines/"
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
                f"ChromaDB lines snapshot downloaded: version={lines_result.get('version_id')}"
            )
        else:
            logger.info(f"ChromaDB lines already cached at {lines_path}")

        # Download words collection to separate directory
        if not os.path.exists(words_db_file):
            logger.info(
                f"Downloading ChromaDB words snapshot from s3://{chromadb_bucket}/words/"
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
                f"ChromaDB words snapshot downloaded: version={words_result.get('version_id')}"
            )
        else:
            logger.info(f"ChromaDB words already cached at {words_path}")
    else:
        logger.info(f"ChromaDB already cached at {base_chroma_path}")

    # Set environment variables for DualChromaClient (separate directories)
    os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_path
    os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_path

    # Create clients using the receipt_agent factory
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_embed_fn,
    )
    from receipt_agent.config.settings import get_settings

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


def parse_chroma_id(chroma_id: str) -> tuple[str, int, int, int]:
    """
    Parse a ChromaDB word ID into components.

    Args:
        chroma_id: ChromaDB ID like "IMAGE#abc#RECEIPT#00001#LINE#00002#WORD#00003"

    Returns:
        Tuple of (image_id, receipt_id, line_id, word_id)
    """
    parts = chroma_id.split("#")
    return (
        parts[1],  # image_id
        int(parts[3]),  # receipt_id
        int(parts[5]),  # line_id
        int(parts[7]),  # word_id
    )


# =============================================================================
# ChromaDB Similar Word Query
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
        logger.debug("Querying similar words for '%s' (%s)", word_text, word_chroma_id)

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

        metadatas = results.get("metadatas")
        if metadatas is None or len(metadatas) == 0 or len(metadatas[0]) == 0:
            return []

        evidence_list: list[SimilarWordEvidence] = []
        distances = results.get("distances", [[]])[0]

        for metadata, distance in zip(metadatas[0], distances):
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

            # Parse valid/invalid labels from comma-delimited format
            valid_labels_str = metadata.get("valid_labels", "")
            invalid_labels_str = metadata.get("invalid_labels", "")

            valid_labels = [
                lbl for lbl in valid_labels_str.split(",") if lbl.strip()
            ]
            invalid_labels = [
                lbl for lbl in invalid_labels_str.split(",") if lbl.strip()
            ]

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
                if label.validation_status == "VALID":
                    if label.label in validated_map:
                        validated_map[label.label]["reasoning"] = label.reasoning
                        validated_map[label.label][
                            "proposed_by"
                        ] = label.label_proposed_by
                        validated_map[label.label][
                            "timestamp"
                        ] = label.timestamp_added
                elif label.validation_status == "INVALID":
                    if label.label in invalidated_map:
                        invalidated_map[label.label][
                            "reasoning"
                        ] = label.reasoning
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
    label_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "example_words": [],
        }
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
