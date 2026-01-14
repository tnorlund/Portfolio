"""Lightweight label validation using ChromaDB similarity search.

This module provides a lightweight alternative to the full label evaluator
for validating PENDING labels at upload time. Instead of using LLM calls
and geometric pattern analysis, it uses semantic similarity search to find
consensus among previously validated words.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo.constants import CORE_LABELS

logger = logging.getLogger(__name__)


class ValidationDecision(Enum):
    """Validation decision for a pending label."""

    AUTO_VALIDATE = "auto_validate"  # High confidence, update to VALIDATED
    NEEDS_REVIEW = "needs_review"  # Log to Langsmith, keep PENDING
    KEEP_PENDING = "keep_pending"  # Not enough data, keep PENDING


@dataclass
class ValidationResult:
    """Result of validating a single label."""

    decision: ValidationDecision
    confidence: float
    consensus_label: Optional[str]
    matching_count: int
    reason: str


def _build_word_chroma_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build ChromaDB ID for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def _distance_to_similarity(distance: float) -> float:
    """Convert L2 distance to similarity score (0-1)."""
    return max(0.0, 1.0 - (distance / 2.0))


def _extract_valid_labels_from_metadata(metadata: Dict[str, Any]) -> List[str]:
    """Extract valid labels from boolean metadata fields.

    PR #636 changed the metadata format from comma-delimited 'valid_labels'
    to individual boolean fields per label (e.g., 'label_GRAND_TOTAL': True).

    Args:
        metadata: ChromaDB document metadata

    Returns:
        List of label names that are marked as valid (True)
    """
    valid_labels = []
    for label in CORE_LABELS:
        field_name = f"label_{label}"
        if metadata.get(field_name) is True:
            valid_labels.append(label)
    return valid_labels


class LightweightLabelValidator:
    """Validates PENDING labels using ChromaDB similarity search.

    This validator queries semantically similar words that have been
    previously validated, then uses weighted consensus voting to
    determine if the predicted label should be auto-validated.

    Thresholds:
        MIN_SIMILARITY: Minimum similarity score to consider a match
        MIN_MATCHES: Minimum number of similar validated words required
        CONSENSUS_THRESHOLD: Minimum consensus ratio for auto-validation
        SAME_MERCHANT_BOOST: Additional weight for same-merchant matches
    """

    MIN_SIMILARITY = 0.80
    MIN_MATCHES = 3
    CONSENSUS_THRESHOLD = 0.80
    SAME_MERCHANT_BOOST = 0.10

    def __init__(
        self,
        words_client: ChromaClient,
        merchant_name: Optional[str] = None,
        word_embeddings: Optional[Dict[Tuple[int, int], List[float]]] = None,
    ):
        """Initialize the validator.

        Args:
            words_client: ChromaClient with words collection (snapshot+delta merged)
            merchant_name: Optional merchant name for same-merchant boosting
            word_embeddings: Optional cached embeddings from orchestration
                             (avoids redundant ChromaDB fetches for current receipt)
        """
        self.words_client = words_client
        self.merchant_name = (
            merchant_name.strip().title() if merchant_name else None
        )
        self.word_embeddings = word_embeddings or {}

    def _get_word_embedding(
        self, chroma_id: str, line_id: int, word_id: int
    ) -> Optional[List[float]]:
        """Get the embedding for a word, checking cache first.

        Args:
            chroma_id: The ChromaDB document ID for the word
            line_id: Line ID for cache lookup
            word_id: Word ID for cache lookup

        Returns:
            The embedding vector or None if not found
        """
        # Check cache first (embeddings from current receipt)
        cached = self.word_embeddings.get((line_id, word_id))
        if cached:
            return cached

        # Fallback to ChromaDB fetch (for words not in current receipt)
        try:
            result = self.words_client.get(
                collection_name="words",
                ids=[chroma_id],
                include=["embeddings"],
            )
            embeddings = result.get("embeddings")
            if embeddings is None or len(embeddings) == 0:
                return None

            embedding = embeddings[0]
            if embedding is None:
                return None

            # Convert numpy array to list
            if hasattr(embedding, "tolist"):
                if hasattr(embedding, "size") and embedding.size == 0:
                    return None
                return embedding.tolist()

            if not embedding:
                return None
            return list(embedding)
        except Exception as e:
            logger.warning("Error getting embedding for %s: %s", chroma_id, e)
        return None

    def _query_similar_validated(
        self,
        embedding: List[float],
        exclude_id: str,
        n_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query for similar words with validated labels."""
        try:
            results = self.words_client.query(
                collection_name="words",
                query_embeddings=[embedding],
                n_results=n_results,
                where={"label_status": "validated"},
                include=["metadatas", "distances"],
            )

            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            similar = []
            for metadata, distance in zip(metadatas, distances):
                # Build result ID to check for self
                result_id = _build_word_chroma_id(
                    metadata.get("image_id", ""),
                    metadata.get("receipt_id", 0),
                    metadata.get("line_id", 0),
                    metadata.get("word_id", 0),
                )
                if result_id == exclude_id:
                    continue

                similarity = _distance_to_similarity(distance)
                if similarity < self.MIN_SIMILARITY:
                    continue

                # Extract valid labels from boolean metadata fields
                valid_labels = _extract_valid_labels_from_metadata(metadata)

                if not valid_labels:
                    continue

                similar.append(
                    {
                        "similarity": similarity,
                        "valid_labels": valid_labels,
                        "merchant_name": metadata.get("merchant_name"),
                        "word_text": metadata.get("text", ""),
                    }
                )

            return similar

        except Exception as e:
            logger.warning("Error querying similar validated words: %s", e)
            return []

    def validate_label(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        predicted_label: str,
    ) -> ValidationResult:
        """Validate a single pending label against similar validated words.

        Args:
            image_id: Image ID of the word
            receipt_id: Receipt ID of the word
            line_id: Line ID of the word
            word_id: Word ID within the line
            predicted_label: The predicted label to validate

        Returns:
            ValidationResult with decision and reasoning
        """
        # Skip "O" labels - they're the most common and provide minimal value
        if predicted_label == "O":
            return ValidationResult(
                decision=ValidationDecision.AUTO_VALIDATE,
                confidence=1.0,
                consensus_label="O",
                matching_count=0,
                reason="O labels auto-validated",
            )

        # Build the word's ChromaDB ID
        chroma_id = _build_word_chroma_id(
            image_id, receipt_id, line_id, word_id
        )

        # Get the word's embedding (uses cache if available)
        embedding = self._get_word_embedding(chroma_id, line_id, word_id)
        if not embedding:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=0,
                reason="Word embedding not found",
            )

        # Query similar validated words
        similar_words = self._query_similar_validated(
            embedding=embedding,
            exclude_id=chroma_id,
        )

        if not similar_words:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=0,
                reason="No similar validated words found",
            )

        if len(similar_words) < self.MIN_MATCHES:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=len(similar_words),
                reason=f"Only {len(similar_words)} matches (need {self.MIN_MATCHES})",
            )

        # Compute weighted label distribution
        label_weights: Dict[str, float] = {}
        for word in similar_words:
            # Apply same-merchant boost
            effective_similarity = word["similarity"]
            if self.merchant_name and word.get("merchant_name") == self.merchant_name:
                effective_similarity = min(
                    1.0, effective_similarity + self.SAME_MERCHANT_BOOST
                )

            # Each valid label gets the effective similarity weight
            for label in word["valid_labels"]:
                label_weights[label] = (
                    label_weights.get(label, 0) + effective_similarity
                )

        if not label_weights:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=len(similar_words),
                reason="No valid labels found in similar words",
            )

        # Find consensus label
        total_weight = sum(label_weights.values())
        best_label = max(label_weights, key=label_weights.get)  # type: ignore
        consensus_ratio = label_weights[best_label] / total_weight

        # Make decision based on consensus
        if consensus_ratio >= self.CONSENSUS_THRESHOLD:
            if best_label == predicted_label:
                return ValidationResult(
                    decision=ValidationDecision.AUTO_VALIDATE,
                    confidence=consensus_ratio,
                    consensus_label=best_label,
                    matching_count=len(similar_words),
                    reason=f"Prediction matches {consensus_ratio:.0%} consensus",
                )
            # Consensus disagrees with prediction - needs human review
            return ValidationResult(
                decision=ValidationDecision.NEEDS_REVIEW,
                confidence=consensus_ratio,
                consensus_label=best_label,
                matching_count=len(similar_words),
                reason=(
                    f"Consensus ({best_label}) differs from "
                    f"prediction ({predicted_label})"
                ),
            )

        # No clear consensus - needs review
        return ValidationResult(
            decision=ValidationDecision.NEEDS_REVIEW,
            confidence=consensus_ratio,
            consensus_label=best_label,
            matching_count=len(similar_words),
            reason=f"Weak consensus ({consensus_ratio:.0%} for {best_label})",
        )
