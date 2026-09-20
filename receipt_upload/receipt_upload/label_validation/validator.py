"""Lightweight label validation at upload time.

This module provides the validation decision contract used by the words
pipeline for PENDING labels. The similarity-consensus voting that once ran
against the retired ``words`` collection was removed with the vector-store
teardown; the validator now auto-validates only ``O`` labels and abstains on
everything else, so pending labels fall through to the LLM validator exactly
as they did in production before the teardown.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ValidationDecision(Enum):
    """Validation decision for a pending label."""

    AUTO_VALIDATE = "auto_validate"  # High confidence FOR, update to VALID
    AUTO_INVALID = "auto_invalid"  # High confidence AGAINST, update to INVALID
    NEEDS_REVIEW = "needs_review"  # Mixed evidence, log to Langsmith
    KEEP_PENDING = "keep_pending"  # Not enough data, keep PENDING


@dataclass
class LabelScore:
    """Score for a candidate label from a similarity sweep."""

    label: str
    match_count: int
    avg_similarity: float
    score: float  # match_count * avg_similarity


@dataclass
class ValidationResult:
    """Result of validating a single label."""

    decision: ValidationDecision
    confidence: float
    consensus_label: Optional[str]
    matching_count: int
    reason: str
    suggested_label: Optional[str] = (
        None  # Best alternative from a similarity sweep
    )
    label_scores: Optional[List[LabelScore]] = (
        None  # Top candidates for LLM prompt
    )


def _build_word_vector_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build the canonical vector-store key for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def _distance_to_similarity(distance: float) -> float:
    """Convert L2 distance to similarity score (0-1)."""
    return max(0.0, 1.0 - (distance / 2.0))


class LightweightLabelValidator:
    """Validates PENDING labels at upload time.

    With no similarity evidence source wired, every non-``O`` label is
    kept PENDING so the LLM validator decides it. The class keeps its
    constructor and query surface so the words pipeline and the async LLM
    payload builder need no changes when an evidence source is added.

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
        merchant_name: Optional[str] = None,
        word_embeddings: Optional[Dict[Tuple[int, int], List[float]]] = None,
    ):
        """Initialize the validator.

        Args:
            merchant_name: Optional merchant name for same-merchant boosting
            word_embeddings: Optional cached embeddings from orchestration
        """
        self.merchant_name = (
            merchant_name.strip().title() if merchant_name else None
        )
        self.word_embeddings = word_embeddings or {}

    def _get_word_embedding(
        self, vector_id: str, line_id: int, word_id: int
    ) -> Optional[List[float]]:
        """Get the embedding for a word from the orchestration cache.

        Args:
            vector_id: The canonical vector key for the word (logging only)
            line_id: Line ID for cache lookup
            word_id: Word ID for cache lookup

        Returns:
            The embedding vector or None if not cached
        """
        del vector_id
        cached = self.word_embeddings.get((line_id, word_id))
        return cached or None

    def _query_similar_for_label(
        self,
        embedding: List[float],
        exclude_id: str,
        predicted_label: str,
        n_results_per_query: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return similar validated words for ``predicted_label``.

        No similarity evidence source is wired, so this always returns an
        empty list; the async LLM payload builder carries that as "no
        similar evidence" for the word.
        """
        del embedding, exclude_id, predicted_label, n_results_per_query
        return []

    def validate_label(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        predicted_label: str,
    ) -> ValidationResult:
        """Validate a single pending label.

        ``O`` labels are auto-validated (they are the most common and carry
        minimal signal). Every other label is kept PENDING because there is
        no similarity evidence to vote with.

        Args:
            image_id: Image ID of the word
            receipt_id: Receipt ID of the word
            line_id: Line ID of the word
            word_id: Word ID within the line
            predicted_label: The predicted label to validate

        Returns:
            ValidationResult with decision and reasoning
        """
        if predicted_label == "O":
            return ValidationResult(
                decision=ValidationDecision.AUTO_VALIDATE,
                confidence=1.0,
                consensus_label="O",
                matching_count=0,
                reason="O labels auto-validated",
            )

        vector_id = _build_word_vector_id(
            image_id, receipt_id, line_id, word_id
        )
        embedding = self._get_word_embedding(vector_id, line_id, word_id)
        if not embedding:
            return ValidationResult(
                decision=ValidationDecision.KEEP_PENDING,
                confidence=0.0,
                consensus_label=None,
                matching_count=0,
                reason="Word embedding not found",
            )

        return ValidationResult(
            decision=ValidationDecision.KEEP_PENDING,
            confidence=0.0,
            consensus_label=None,
            matching_count=0,
            reason=f"No similar words evaluated for {predicted_label}",
        )
