"""Query patterns from Pinecone using merchant filtering."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import ReceiptWord

from receipt_label.client_manager import get_client_manager

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
) -> MerchantPatternResult:
    """
    Query patterns for multiple words using a single merchant-filtered Pinecone query.

    This function achieves true query reduction by using a single metadata-filtered
    query to retrieve all patterns for the specified words from the merchant's data.
    This is much more efficient than N embedding-based queries.

    Args:
        merchant_name: Canonical merchant name
        words: List of words to find patterns for
        confidence_threshold: Minimum confidence for pattern matching

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

    # Keep both original and lowercase for matching
    word_texts_original = [w.text for w in words]
    word_texts_lower = [w.text.lower() for w in words]
    word_map_lower = {w.text.lower(): w.text for w in words}

    pattern_matches = {}

    # Use single query approach - query by metadata filter only, no embedding needed
    # This is much more efficient than N embedding queries when we know exact text
    
    # Prepare text variants for all words (original and lowercase)
    text_variants = []
    word_text_map = {}  # Map text variant back to original word
    
    for word in words:
        # Add both original and lowercase variants
        text_variants.extend([word.text, word.text.lower()])
        word_text_map[word.text] = word
        word_text_map[word.text.lower()] = word
    
    # Single query to get all patterns for this merchant and these words
    # Use dummy vector since we're filtering by metadata only
    dummy_vector = [0.0] * 1536  # Standard embedding dimension
    
    results = index.query(
        vector=dummy_vector,
        filter={
            "merchant_name": {"$eq": merchant_name},
            "embedding_type": {"$eq": "word"},
            "text": {"$in": text_variants},  # Filter for all our words at once
        },
        top_k=10000,  # Get all matches for this merchant and these words
        include_metadata=True,
        namespace="word",
    )
    
    # Group results by word text (case-insensitive)
    word_results = defaultdict(list)
    for match in results.matches:
        metadata = match.metadata
        matched_text = metadata.get("text", "")
        validated_labels = metadata.get("validated_labels", {})
        
        if matched_text and validated_labels:
            # Group by lowercase for consistent matching
            word_key = matched_text.lower()
            if word_key in [w.text.lower() for w in words]:
                word_results[word_key].append((match, validated_labels))
    
    # Process each word's results to find patterns
    for word in words:
        word_key = word.text.lower()
        matches_for_word = word_results.get(word_key, [])
        
        if not matches_for_word:
            continue
            
        # Collect label frequencies
        label_counts = defaultdict(int)
        for match, validated_labels in matches_for_word:
            for label, value in validated_labels.items():
                if value:  # Non-empty label
                    label_counts[label] += 1
        
        if label_counts:
            # Find most common label
            best_label = max(label_counts.items(), key=lambda x: x[1])[0]
            best_count = label_counts[best_label]
            total_occurrences = sum(label_counts.values())
            
            # Calculate frequency-based confidence
            frequency_confidence = best_count / total_occurrences if total_occurrences > 0 else 0
            
            # Since we're not using embedding similarity, use frequency as primary confidence
            # with a small boost for having multiple occurrences
            occurrence_boost = min(len(matches_for_word) / 10.0, 0.2)  # Up to 20% boost
            combined_confidence = frequency_confidence + occurrence_boost
            
            # Cap at 1.0
            combined_confidence = min(combined_confidence, 1.0)
            
            # Only keep patterns above threshold
            if combined_confidence >= confidence_threshold:
                pattern = PatternMatch(
                    word=word.text,  # Keep original case for display
                    suggested_label=best_label,
                    confidence=combined_confidence,
                    confidence_level=PatternMatch.from_confidence_score(
                        combined_confidence
                    ),
                    frequency=best_count,
                    last_validated=datetime.utcnow(),
                    merchant_name=merchant_name,
                    sample_contexts=[word.text],
                )
                
                pattern_matches[word_key] = pattern

    # Calculate results
    words_with_patterns = list(pattern_matches.keys())
    words_without_patterns = [
        w.lower()
        for w in word_texts_original
        if w.lower() not in pattern_matches
    ]

    # Query reduction: True single query approach
    # Traditional approach: N queries (one per word) across entire index
    # Our approach: 1 query with merchant + text filters
    query_reduction_ratio = 1.0 - (1.0 / len(words)) if len(words) > 1 else 0.0

    return MerchantPatternResult(
        merchant_name=merchant_name,
        pattern_matches=pattern_matches,
        words_with_patterns=words_with_patterns,
        words_without_patterns=words_without_patterns,
        query_reduction_ratio=query_reduction_ratio,
    )
