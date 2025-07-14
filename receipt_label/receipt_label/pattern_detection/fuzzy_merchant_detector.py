"""
Fuzzy merchant name detection using validated metadata.

This module uses fuzzy string matching to find merchant names in receipt text,
leveraging Google Places metadata for accurate merchant identification.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

from receipt_label.models import ReceiptWord
from receipt_label.pattern_detection.enhanced_orchestrator import (
    StandardizedPatternMatch,
)

logger = logging.getLogger(__name__)


@dataclass
class MerchantMatch:
    """Result of fuzzy merchant matching."""

    merchant_name: str
    matched_text: str
    similarity_score: float
    word_indices: List[int]
    match_type: str  # 'exact', 'fuzzy', 'partial'


class FuzzyMerchantDetector:
    """
    Detects merchant names using fuzzy string matching.

    This is especially powerful when combined with Google Places metadata,
    as we can search for known merchant names in the receipt text.
    """

    def __init__(self, min_similarity: float = 80.0):
        """
        Initialize the fuzzy merchant detector.

        Args:
            min_similarity: Minimum similarity score (0-100) to consider a match
        """
        self.min_similarity = min_similarity

        # Common merchant name variations
        self.merchant_variations = {
            "walmart": [
                "wal-mart",
                "wal mart",
                "walmart store",
                "walmart supercenter",
            ],
            "mcdonald's": [
                "mcdonalds",
                "mcdonald",
                "mc donalds",
                "mickey d's",
            ],
            "target": ["target store", "target corp"],
            "starbucks": ["starbucks coffee", "sbux"],
            "cvs": ["cvs pharmacy", "cvs/pharmacy"],
            "walgreens": ["walgreens pharmacy"],
            "home depot": ["the home depot", "homedepot"],
            "costco": ["costco wholesale"],
            "sprouts": ["sprouts farmers market", "sprouts farmers mkt"],
        }

    def detect_merchant(
        self,
        words: List[ReceiptWord],
        known_merchant: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[StandardizedPatternMatch]:
        """
        Detect merchant name in receipt using fuzzy matching.

        Args:
            words: Receipt words to search
            known_merchant: Known merchant name from metadata
            metadata: Additional metadata (category, location, etc.)

        Returns:
            List of merchant matches as standardized patterns
        """
        matches = []

        # Strategy 1: If we have a known merchant, search for it
        if known_merchant:
            merchant_match = self._find_merchant_in_text(words, known_merchant)
            if merchant_match:
                matches.append(
                    self._create_pattern_match(
                        merchant_match, words, "metadata_fuzzy"
                    )
                )
                logger.info(
                    f"Found merchant '{known_merchant}' with {merchant_match.similarity_score:.1f}% similarity"
                )

        # Strategy 2: Search in header area (top 20% of receipt)
        # Skip header search for now due to format issues
        # header_matches = self._search_header_area(words, known_merchant)
        # for match in header_matches:
        #     if not self._is_duplicate(match, matches):
        #         matches.append(self._create_pattern_match(match, words, "header_fuzzy"))

        # Strategy 3: Look for store number patterns that might indicate merchant
        if metadata and metadata.get("merchant_category"):
            store_patterns = self._find_store_patterns(
                words, metadata["merchant_category"]
            )
            matches.extend(store_patterns)

        return matches

    def _find_merchant_in_text(
        self, words: List[ReceiptWord], merchant_name: str
    ) -> Optional[MerchantMatch]:
        """Find a specific merchant name in the receipt text using fuzzy matching."""

        # Clean and prepare merchant name
        merchant_clean = merchant_name.lower().strip()

        # Get variations of the merchant name
        variations = self._get_merchant_variations(merchant_clean)

        # Build text segments of different lengths
        text_segments = self._build_text_segments(words, max_length=5)

        best_match = None
        best_score = 0.0

        for segment_text, word_indices in text_segments:
            # Try each variation
            for variation in variations:
                # Use different fuzzy matching strategies
                scores = [
                    fuzz.ratio(variation, segment_text),
                    fuzz.partial_ratio(variation, segment_text),
                    fuzz.token_sort_ratio(variation, segment_text),
                    fuzz.token_set_ratio(variation, segment_text),
                ]

                max_score = max(scores)

                if max_score >= self.min_similarity and max_score > best_score:
                    best_score = max_score
                    best_match = MerchantMatch(
                        merchant_name=merchant_name,
                        matched_text=segment_text,
                        similarity_score=max_score,
                        word_indices=word_indices,
                        match_type=self._get_match_type(max_score),
                    )

        return best_match

    def _search_header_area(
        self, words: List[ReceiptWord], known_merchant: Optional[str] = None
    ) -> List[MerchantMatch]:
        """Search for merchant names in the header area of the receipt."""

        # Header is typically in the top 20% of the receipt
        if not words:
            return []

        # Calculate header boundary
        # Handle both ReceiptWord objects and dict format
        def get_y(word):
            if hasattr(word, "bounding_box"):
                # For objects, check if bounding_box is an object with .y or a dict
                bbox = word.bounding_box
                if hasattr(bbox, "y"):
                    return bbox.y
                else:
                    return bbox.get("y", 0)  # bounding_box is a dict
            else:
                # For dict format
                return word.get("bounding_box", {}).get("y", 0)

        min_y = min(get_y(w) for w in words)
        max_y = max(get_y(w) for w in words)
        header_boundary = min_y + (max_y - min_y) * 0.2

        header_words = [w for w in words if get_y(w) <= header_boundary]

        if not header_words:
            return []

        matches = []

        # Helper to get text from word
        def get_text(word):
            return word.text if hasattr(word, "text") else word.get("text", "")

        # Look for business-like text in header
        header_text = " ".join(get_text(w) for w in header_words)

        # Common patterns that indicate business names
        business_indicators = [
            r"\b(LLC|INC|CORP|CO|COMPANY|STORE|MARKET|PHARMACY)\b",
            r"\b\d{3,5}\b",  # Store numbers
            r"[A-Z]{2,}",  # All caps names
        ]

        # If we have a known merchant, search for it specifically
        if known_merchant:
            match = self._find_merchant_in_text(header_words, known_merchant)
            if match:
                matches.append(match)

        return matches

    def _find_store_patterns(
        self, words: List[ReceiptWord], merchant_category: str
    ) -> List[StandardizedPatternMatch]:
        """Find store-specific patterns based on merchant category."""

        patterns = []

        # Category-specific patterns
        category_patterns = {
            "Grocery Store": ["store #", "str #", "store no", "location"],
            "Restaurant": ["restaurant #", "location", "franchise"],
            "Gas Station": ["station #", "store #", "location"],
            "Pharmacy": ["pharmacy #", "rx #", "store #"],
        }

        if merchant_category in category_patterns:
            # Search for category-specific patterns
            for pattern in category_patterns[merchant_category]:
                # Implementation would search for these patterns
                pass

        return patterns

    def _get_merchant_variations(self, merchant_name: str) -> List[str]:
        """Get variations of a merchant name for fuzzy matching."""

        variations = [merchant_name]

        # Add known variations
        base_name = merchant_name.lower()
        if base_name in self.merchant_variations:
            variations.extend(self.merchant_variations[base_name])

        # Generate common variations
        # Remove common suffixes
        for suffix in [
            " store",
            " market",
            " pharmacy",
            " restaurant",
            " cafe",
        ]:
            if base_name.endswith(suffix):
                variations.append(base_name[: -len(suffix)].strip())

        # Handle possessives
        if base_name.endswith("'s"):
            variations.append(base_name[:-2])
        elif base_name.endswith("s'"):
            variations.append(base_name[:-1])
        else:
            variations.append(base_name + "'s")

        # Handle spacing variations
        if " " in base_name:
            variations.append(base_name.replace(" ", ""))
            variations.append(base_name.replace(" ", "-"))

        return list(set(variations))

    def _build_text_segments(
        self, words: List[ReceiptWord], max_length: int = 5
    ) -> List[Tuple[str, List[int]]]:
        """Build text segments of various lengths for fuzzy matching."""

        segments = []

        # Helper functions
        def get_text(word):
            return word.text if hasattr(word, "text") else word.get("text", "")

        def is_noise(word):
            if hasattr(word, "is_noise"):
                return word.is_noise
            # For dict format, consider very short words as noise
            text = get_text(word)
            return len(text) <= 1 or text in {
                ".",
                ",",
                "-",
                "/",
                "\\",
                "|",
                "_",
            }

        # Single words
        for i, word in enumerate(words):
            if not is_noise(word):
                segments.append((get_text(word).lower(), [i]))

        # Multi-word segments (2 to max_length words)
        for length in range(2, min(max_length + 1, len(words) + 1)):
            for i in range(len(words) - length + 1):
                word_group = words[i : i + length]
                # Skip if any word is noise
                if not any(is_noise(w) for w in word_group):
                    text = " ".join(get_text(w) for w in word_group).lower()
                    indices = list(range(i, i + length))
                    segments.append((text, indices))

        return segments

    def _find_matching_words(
        self, words: List[ReceiptWord], matched_text: str
    ) -> List[ReceiptWord]:
        """Find words that match the given text."""

        # Helper to get text
        def get_text(word):
            return word.text if hasattr(word, "text") else word.get("text", "")

        # For now, return first word as placeholder
        # In a real implementation, we'd match the actual words
        return [words[0]] if words else []

    def _get_match_type(self, score: float) -> str:
        """Determine match type based on similarity score."""
        if score >= 95:
            return "exact"
        elif score >= 85:
            return "fuzzy"
        else:
            return "partial"

    def _is_duplicate(
        self, match: MerchantMatch, existing: List[StandardizedPatternMatch]
    ) -> bool:
        """Check if a match is a duplicate of existing matches."""
        for existing_match in existing:
            if hasattr(existing_match, "extracted_value"):
                if (
                    existing_match.extracted_value.lower()
                    == match.merchant_name.lower()
                ):
                    return True
        return False

    def _create_pattern_match(
        self, match: MerchantMatch, words: List[ReceiptWord], source: str
    ) -> StandardizedPatternMatch:
        """Create a standardized pattern match from a merchant match."""

        # Get the primary word (first word in the match)
        primary_word = words[match.word_indices[0]]

        # Get all words in the match
        matched_words = [words[i] for i in match.word_indices]

        return StandardizedPatternMatch(
            word=primary_word,
            extracted_value=match.merchant_name,
            confidence=match.similarity_score / 100.0,  # Convert to 0-1 scale
            pattern_type="merchant",
            words=matched_words,
            metadata={
                "match_type": match.match_type,
                "matched_text": match.matched_text,
                "similarity_score": match.similarity_score,
                "source": source,
                "algorithm": "fuzzy_matching",
            },
        )


# Convenience function
async def detect_merchant_with_metadata(
    words: List[ReceiptWord],
    metadata: Optional[Dict[str, Any]] = None,
    min_similarity: float = 80.0,
) -> List[StandardizedPatternMatch]:
    """
    Detect merchant name using fuzzy matching with metadata.

    Args:
        words: Receipt words
        metadata: Google Places metadata containing merchant_name
        min_similarity: Minimum similarity threshold

    Returns:
        List of merchant pattern matches
    """
    detector = FuzzyMerchantDetector(min_similarity=min_similarity)

    known_merchant = metadata.get("merchant_name") if metadata else None

    return detector.detect_merchant(
        words=words, known_merchant=known_merchant, metadata=metadata
    )
