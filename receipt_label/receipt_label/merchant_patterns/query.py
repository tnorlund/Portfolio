"""Query patterns from Pinecone using merchant filtering."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import ReceiptWord

from receipt_label.client_manager import get_client_manager
from receipt_label.embedding.realtime.embed import (
    EmbeddingContext,
    embed_words_realtime,
)

from .types import (
    MerchantPatterns,
    PatternConfidence,
    PatternMatch,
)

logger = logging.getLogger(__name__)


@dataclass
class MerchantPatternResult:
    """Result of pattern matching for multiple words."""

    merchant_name: str
    pattern_matches: Dict[str, PatternMatch]  # word -> pattern
    words_with_patterns: List[str]
    words_without_patterns: List[str]
    query_reduction_ratio: float  # How many queries we saved


def get_merchant_patterns(
    merchant_name: str,
    min_frequency: int = 2,
    min_confidence: float = 0.5,
) -> MerchantPatterns:
    """
    Extract patterns from all validated receipts for a merchant.

    This function uses Pinecone to find all embeddings from a specific merchant
    and extracts word->label patterns from the validated labels.

    Args:
        merchant_name: Canonical merchant name to query
        min_frequency: Minimum times a pattern must appear
        min_confidence: Minimum confidence threshold

    Returns:
        MerchantPatterns object with extracted patterns
    """
    client_manager = get_client_manager()
    pinecone_client = client_manager.pinecone

    # Get Pinecone index
    index = pinecone_client.Index("receipt-embeddings")

    # Query all vectors for this merchant
    # Using a dummy vector for metadata filtering
    results = index.query(
        vector=[0.0] * 1536,  # Dummy vector for metadata filtering
        filter={
            "merchant_name": {"$eq": merchant_name},
            "embedding_type": {"$eq": "word"},
        },
        top_k=10000,  # Get all vectors for this merchant
        include_metadata=True,
        namespace="word",
    )

    # Extract patterns from results
    pattern_counts: Dict[str, Dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    pattern_contexts: Dict[str, Dict[str, List[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    label_counts: Dict[str, int] = defaultdict(int)
    validated_words = 0
    unique_receipts = set()

    for match in results.matches:
        metadata = match.metadata

        # Track unique receipts
        receipt_id = metadata.get("receipt_id")
        if receipt_id:
            unique_receipts.add(receipt_id)

        # Extract validated labels
        validated_labels = metadata.get("validated_labels", {})
        word_text = metadata.get("text", "").lower()

        if validated_labels and word_text:
            validated_words += 1

            # Count each label for this word
            for label, value in validated_labels.items():
                if value:  # Only count non-empty labels
                    pattern_counts[word_text][label] += 1
                    label_counts[label] += 1

                    # Collect context (neighboring words if available)
                    context = metadata.get("context", word_text)
                    pattern_contexts[word_text][label].append(context)

    # Build pattern map
    pattern_map = {}

    for word, label_frequencies in pattern_counts.items():
        patterns = []
        total_occurrences = sum(label_frequencies.values())

        for label, frequency in label_frequencies.items():
            if frequency >= min_frequency:
                confidence = frequency / total_occurrences

                if confidence >= min_confidence:
                    pattern = PatternMatch(
                        word=word,
                        suggested_label=label,
                        confidence=confidence,
                        confidence_level=PatternMatch.from_confidence_score(
                            confidence
                        ),
                        frequency=frequency,
                        last_validated=datetime.utcnow(),  # Would need actual date
                        merchant_name=merchant_name,
                        sample_contexts=pattern_contexts[word][label][
                            :5
                        ],  # Top 5
                    )
                    patterns.append(pattern)

        if patterns:
            pattern_map[word] = patterns

    return MerchantPatterns(
        merchant_name=merchant_name,
        canonical_merchant_name=merchant_name,
        total_receipts=len(unique_receipts),
        total_validated_words=validated_words,
        pattern_map=pattern_map,
        common_labels=dict(label_counts),
        last_updated=datetime.utcnow(),
    )


def query_patterns_for_words(
    merchant_name: str,
    words: List[ReceiptWord],
    confidence_threshold: float = 0.7,
    use_embeddings: bool = True,
) -> MerchantPatternResult:
    """
    Query patterns for multiple words using a single Pinecone query.

    This is the main function that demonstrates 99% query reduction by using
    a single merchant-filtered query instead of N individual queries.

    Args:
        merchant_name: Canonical merchant name
        words: List of words to find patterns for
        confidence_threshold: Minimum confidence for pattern matching
        use_embeddings: Whether to use embeddings for similarity matching

    Returns:
        MerchantPatternResult with pattern matches
    """
    if not words:
        return MerchantPatternResult(
            merchant_name=merchant_name,
            pattern_matches={},
            words_with_patterns=[],
            words_without_patterns=[],
            query_reduction_ratio=0.0,
        )

    client_manager = get_client_manager()
    pinecone_client = client_manager.pinecone
    index = pinecone_client.Index("receipt-embeddings")

    # Normalize word texts
    word_texts = [w.text.lower() for w in words]
    word_text_map = {w.text.lower(): w for w in words}

    pattern_matches = {}

    if use_embeddings and words:
        # Create embedding context
        context = EmbeddingContext(
            receipt_id=words[0].receipt_id,
            image_id=words[0].image_id,
            canonical_merchant_name=merchant_name,
        )

        # Get embeddings for all words at once
        embeddings = embed_words_realtime(words, context)

        # Single query for all words with merchant filter
        # We'll use the first word's embedding as the query vector
        # and include all words in the filter
        if embeddings:
            first_word = next(iter(embeddings.keys()))
            query_vector = embeddings[first_word]

            results = index.query(
                vector=query_vector,
                filter={
                    "merchant_name": {"$eq": merchant_name},
                    "embedding_type": {"$eq": "word"},
                    "text": {
                        "$in": word_texts
                    },  # Filter for our specific words
                },
                top_k=100,  # Get top matches
                include_metadata=True,
                namespace="word",
            )

            # Process results to find patterns
            for match in results.matches:
                metadata = match.metadata
                matched_word = metadata.get("text", "").lower()
                validated_labels = metadata.get("validated_labels", {})

                if matched_word in word_texts and validated_labels:
                    # Find the most common label for this word
                    best_label = None
                    best_count = 0

                    for label, value in validated_labels.items():
                        if value:  # Non-empty label
                            # In real implementation, we'd count frequency
                            # For now, just take the first valid label
                            if not best_label:
                                best_label = label

                    if best_label:
                        pattern = PatternMatch(
                            word=matched_word,
                            suggested_label=best_label,
                            confidence=match.score,  # Use similarity score
                            confidence_level=PatternMatch.from_confidence_score(
                                match.score
                            ),
                            frequency=1,  # Would need actual frequency
                            last_validated=datetime.utcnow(),
                            merchant_name=merchant_name,
                            sample_contexts=[
                                matched_word
                            ],  # Would need actual contexts
                        )

                        if pattern.confidence >= confidence_threshold:
                            pattern_matches[matched_word] = pattern

    else:
        # Fallback: Get patterns without embeddings
        merchant_patterns = get_merchant_patterns(merchant_name)

        for word_text in word_texts:
            best_pattern = merchant_patterns.get_best_pattern(word_text)
            if (
                best_pattern
                and best_pattern.confidence >= confidence_threshold
            ):
                pattern_matches[word_text] = best_pattern

    # Calculate results
    words_with_patterns = list(pattern_matches.keys())
    words_without_patterns = [
        w for w in word_texts if w not in pattern_matches
    ]

    # Query reduction: 1 query instead of N
    query_reduction_ratio = 1.0 - (1.0 / len(words)) if len(words) > 1 else 0.0

    return MerchantPatternResult(
        merchant_name=merchant_name,
        pattern_matches=pattern_matches,
        words_with_patterns=words_with_patterns,
        words_without_patterns=words_without_patterns,
        query_reduction_ratio=query_reduction_ratio,
    )
