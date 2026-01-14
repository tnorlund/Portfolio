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

    AUTO_VALIDATE = "auto_validate"  # High confidence FOR, update to VALID
    AUTO_INVALID = "auto_invalid"  # High confidence AGAINST, update to INVALID
    NEEDS_REVIEW = "needs_review"  # Mixed evidence, log to Langsmith
    KEEP_PENDING = "keep_pending"  # Not enough data, keep PENDING


@dataclass
class ValidationResult:
    """Result of validating a single label."""

    decision: ValidationDecision
    confidence: float
    consensus_label: Optional[str]  # The label consensus agrees on (None if unknown)
    matching_count: int
    reason: str
    suggested_label: Optional[str] = None  # Alternative label hint for LLM review


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

    def _query_single_label_value(
        self,
        embedding: List[float],
        exclude_id: str,
        label_field: str,
        label_value: bool,
        n_results: int,
    ) -> List[Dict[str, Any]]:
        """Query for similar words with a specific label value (True or False).

        Args:
            embedding: The word's embedding vector
            exclude_id: ChromaDB ID to exclude (the word being validated)
            label_field: The label field name (e.g., "label_GRAND_TOTAL")
            label_value: True for positive evidence, False for negative
            n_results: Maximum number of results to return

        Returns:
            List of dicts with similarity, label_valid, merchant_name, word_text
        """
        try:
            results = self.words_client.query(
                collection_name="words",
                query_embeddings=[embedding],
                n_results=n_results,
                where={
                    "$and": [
                        {"label_status": "validated"},
                        {label_field: label_value},
                    ]
                },
                include=["metadatas", "distances"],
            )

            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            similar = []
            for metadata, distance in zip(metadatas, distances):
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

                similar.append(
                    {
                        "similarity": similarity,
                        "label_valid": label_value,
                        "merchant_name": metadata.get("merchant_name"),
                        "word_text": metadata.get("text", ""),
                    }
                )

            return similar

        except Exception as e:
            logger.warning(
                "Error querying %s=%s: %s", label_field, label_value, e
            )
            return []

    def _query_similar_for_label(
        self,
        embedding: List[float],
        exclude_id: str,
        predicted_label: str,
        n_results_per_query: int = 10,
    ) -> List[Dict[str, Any]]:
        """Query for similar words with balanced positive and negative evidence.

        Runs TWO separate queries to ensure balanced evidence:
        1. Words where label=True (positive evidence)
        2. Words where label=False (negative evidence)

        This prevents skewed results when one category dominates similarity.

        Args:
            embedding: The word's embedding vector
            exclude_id: ChromaDB ID to exclude (the word being validated)
            predicted_label: The label to filter by (e.g., "GRAND_TOTAL")
            n_results_per_query: Results per query (default 10 each = 20 total max)

        Returns:
            List of dicts with similarity, label_valid (bool), merchant_name, word_text
        """
        label_field = f"label_{predicted_label}"

        # Query for positive evidence (words validated AS this label)
        positive = self._query_single_label_value(
            embedding=embedding,
            exclude_id=exclude_id,
            label_field=label_field,
            label_value=True,
            n_results=n_results_per_query,
        )

        # Query for negative evidence (words validated as NOT this label)
        negative = self._query_single_label_value(
            embedding=embedding,
            exclude_id=exclude_id,
            label_field=label_field,
            label_value=False,
            n_results=n_results_per_query,
        )

        # Combine results
        return positive + negative

    def _find_suggested_label(
        self,
        embedding: List[float],
        exclude_id: str,
        exclude_label: str,
        n_results: int = 10,
    ) -> Optional[str]:
        """Find the most likely alternative label for a word.

        Queries similar validated words (any label) and counts which labels
        they have. Returns the most common label that isn't the excluded one.

        Used to provide a hint to LLM review: "This isn't GRAND_TOTAL,
        but similar words are often LINE_TOTAL."

        Args:
            embedding: The word's embedding vector
            exclude_id: ChromaDB ID to exclude (the word being validated)
            exclude_label: Label to exclude from suggestions (the rejected prediction)
            n_results: Number of similar words to check

        Returns:
            Most common alternative label, or None if no suggestion
        """
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

            # Count weighted votes for each label
            label_weights: Dict[str, float] = {}

            for metadata, distance in zip(metadatas, distances):
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

                # Find which labels are True for this word
                for label in CORE_LABELS:
                    if label == exclude_label:
                        continue
                    field_name = f"label_{label}"
                    if metadata.get(field_name) is True:
                        label_weights[label] = label_weights.get(label, 0) + similarity

            if not label_weights:
                return None

            # Return the label with highest weight
            return max(label_weights, key=label_weights.get)  # type: ignore

        except Exception as e:
            logger.warning("Error finding suggested label: %s", e)
            return None

    def validate_label(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        predicted_label: str,
    ) -> ValidationResult:
        """Validate a single pending label against similar validated words.

        Uses binary consensus voting: queries similar words that have been
        evaluated for the specific predicted label, then counts weighted
        votes for (label=True) and against (label=False).

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

        # Query similar words evaluated for this specific label
        similar_words = self._query_similar_for_label(
            embedding=embedding,
            exclude_id=chroma_id,
            predicted_label=predicted_label,
        )

        if not similar_words:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=0,
                reason=f"No similar words evaluated for {predicted_label}",
            )

        if len(similar_words) < self.MIN_MATCHES:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=len(similar_words),
                reason=f"Only {len(similar_words)} matches (need {self.MIN_MATCHES})",
            )

        # Binary consensus voting: count weighted votes for/against
        votes_for = 0.0
        votes_against = 0.0

        for word in similar_words:
            # Apply same-merchant boost
            weight = word["similarity"]
            if self.merchant_name and word.get("merchant_name") == self.merchant_name:
                weight = min(1.0, weight + self.SAME_MERCHANT_BOOST)

            if word["label_valid"]:
                votes_for += weight
            else:
                votes_against += weight

        total_votes = votes_for + votes_against
        if total_votes == 0:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=len(similar_words),
                reason="No weighted votes computed",
            )

        # Confidence = proportion of votes FOR the predicted label
        confidence = votes_for / total_votes

        # Make decision based on confidence thresholds
        if confidence >= self.CONSENSUS_THRESHOLD:
            # Strong evidence FOR the prediction
            return ValidationResult(
                decision=ValidationDecision.AUTO_VALIDATE,
                confidence=confidence,
                consensus_label=predicted_label,
                matching_count=len(similar_words),
                reason=f"{confidence:.0%} of similar words validated as {predicted_label}",
            )

        if confidence <= (1.0 - self.CONSENSUS_THRESHOLD):
            # Strong evidence AGAINST the prediction
            # Find what label it might actually be
            suggested = self._find_suggested_label(
                embedding=embedding,
                exclude_id=chroma_id,
                exclude_label=predicted_label,
            )
            return ValidationResult(
                decision=ValidationDecision.AUTO_INVALID,
                confidence=1.0 - confidence,  # Confidence in INVALID decision
                consensus_label=None,  # We don't know what it IS, only what it's NOT
                matching_count=len(similar_words),
                reason=f"{1.0 - confidence:.0%} of similar words rejected {predicted_label}",
                suggested_label=suggested,
            )

        # Mixed evidence - needs review
        # Find what label it might be to help LLM review
        suggested = self._find_suggested_label(
            embedding=embedding,
            exclude_id=chroma_id,
            exclude_label=predicted_label,
        )
        return ValidationResult(
            decision=ValidationDecision.NEEDS_REVIEW,
            confidence=confidence,
            consensus_label=None,  # No clear consensus
            matching_count=len(similar_words),
            reason=f"Mixed evidence: {confidence:.0%} for, {1.0 - confidence:.0%} against",
            suggested_label=suggested,
        )
