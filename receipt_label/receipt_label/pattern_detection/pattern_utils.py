"""
Common utilities for pattern detection and optimization.

This module provides shared functionality used across multiple pattern detectors,
including selective invocation, batch processing, and pattern optimization utilities.
"""

import re
from typing import Dict, List, Set, Tuple, Optional, Any
from receipt_label.pattern_detection.patterns_config import (
    PatternConfig,
    classify_word_type,
    is_noise_word,
)
from receipt_dynamo.entities import ReceiptWord


class PatternOptimizer:
    """Utilities for optimizing pattern detection performance."""

    @staticmethod
    def should_run_detector(
        detector_type: str, words: List[ReceiptWord]
    ) -> bool:
        """
        Determine if a detector should run based on word characteristics.

        This implements selective detector invocation from Phase 2.
        Only run detectors on words that could potentially match.

        Args:
            detector_type: Type of detector ("currency", "contact", "datetime", "quantity")
            words: List of words to analyze

        Returns:
            True if detector should run, False if it can be skipped
        """
        if not words:
            return False

        # Quick scan for relevant word types
        word_types = set()
        has_relevant_words = False

        for word in words:
            if word.is_noise or is_noise_word(word.text):
                continue

            types = classify_word_type(word.text)
            word_types.update(types)

            # Detector-specific quick checks
            if detector_type == "currency" and (
                "currency_like" in types or "numeric" in types
            ):
                has_relevant_words = True
            elif detector_type == "contact" and (
                "contact_like" in types or "@" in word.text
            ):
                has_relevant_words = True
            elif detector_type == "datetime":
                # Dates can be numeric, alphanumeric, or contain symbols like slashes/colons
                # Look for datetime separators or pure numbers
                if (
                    any(char in word.text for char in "/-.:")
                    or "numeric" in types
                    or "alphanumeric" in types
                ):
                    has_relevant_words = True
            elif detector_type == "quantity" and (
                "numeric" in types
                or "@" in word.text
                or "x" in word.text.lower()
            ):
                has_relevant_words = True

        return has_relevant_words

    @staticmethod
    def filter_words_for_detector(
        detector_type: str, words: List[ReceiptWord]
    ) -> List[ReceiptWord]:
        """
        Filter words to only those relevant for a specific detector.

        Args:
            detector_type: Type of detector
            words: All words

        Returns:
            Filtered list of words relevant to the detector
        """
        if detector_type == "currency":
            return [
                w
                for w in words
                if not w.is_noise
                and any(c in w.text for c in "0123456789$€£¥₹¢.,")
            ]

        elif detector_type == "contact":
            return [
                w
                for w in words
                if not w.is_noise
                and (
                    "@" in w.text
                    or any(
                        f".{tld}" in w.text.lower()
                        for tld in PatternConfig.COMMON_TLDS
                    )
                    or any(c in w.text for c in "0123456789()-. +")
                )
            ]

        elif detector_type == "datetime":
            return [
                w
                for w in words
                if not w.is_noise
                and any(c in w.text for c in "0123456789/-.:")
            ]

        elif detector_type == "quantity":
            return [
                w
                for w in words
                if not w.is_noise
                and (
                    any(c in w.text for c in "0123456789@x")
                    or any(
                        unit in w.text.lower()
                        for unit in [
                            "qty",
                            "each",
                            "lbs",
                            "oz",
                            "kg",
                            "pack",
                            "box",
                        ]
                    )
                )
            ]

        return words  # Default: return all words


class BatchPatternMatcher:
    """Utilities for batch regex evaluation using alternation."""

    @staticmethod
    def combine_patterns(
        patterns: Dict[str, str], group_names: bool = True
    ) -> Tuple[re.Pattern, Dict[int, str]]:
        """
        Combine multiple regex patterns into a single alternation pattern.

        Args:
            patterns: Dictionary of pattern_name -> regex_string
            group_names: Whether to create named groups for each pattern

        Returns:
            Tuple of (compiled_pattern, group_index_to_name_mapping)
        """
        if not patterns:
            return re.compile(""), {}

        if group_names:
            # Create named groups: (?P<pattern_name>pattern)
            combined_parts = []
            group_mapping = {}
            group_index = 1

            for name, pattern in patterns.items():
                combined_parts.append(f"(?P<{name}>{pattern})")
                group_mapping[group_index] = name
                group_index += 1

            combined_pattern = "|".join(combined_parts)
        else:
            # Simple alternation with positional groups
            combined_parts = [f"({pattern})" for pattern in patterns.values()]
            combined_pattern = "|".join(combined_parts)
            group_mapping = {
                i + 1: name for i, name in enumerate(patterns.keys())
            }

        return re.compile(combined_pattern, re.IGNORECASE), group_mapping

    @staticmethod
    def batch_match_text(
        combined_pattern: re.Pattern,
        group_mapping: Dict[int, str],
        text_list: List[str],
    ) -> Dict[str, List[Tuple[str, re.Match]]]:
        """
        Run a combined pattern against multiple text strings.

        Args:
            combined_pattern: Compiled alternation pattern
            group_mapping: Maps group indices to pattern names
            text_list: List of text strings to match against

        Returns:
            Dictionary mapping pattern_name -> [(text, match_object), ...]
        """
        results = {name: [] for name in group_mapping.values()}

        for text in text_list:
            match = combined_pattern.search(text)
            if match:
                # Find which group matched
                for group_idx, pattern_name in group_mapping.items():
                    if match.group(group_idx):
                        results[pattern_name].append((text, match))
                        break

        return results


class ContextAnalyzer:
    """Utilities for analyzing word context and relationships."""

    @staticmethod
    def get_line_context(
        target_word: ReceiptWord,
        all_words: List[ReceiptWord],
        y_tolerance: float = 10.0,
    ) -> List[ReceiptWord]:
        """
        Get words that appear on the same line as the target word.

        Args:
            target_word: The word to find context for
            all_words: All words in the receipt
            y_tolerance: Vertical tolerance for considering words on the same line

        Returns:
            List of words on the same line
        """
        if not (target_word.bounding_box and "y" in target_word.bounding_box):
            return [target_word]

        line_words = []
        target_y = target_word.bounding_box["y"]

        for word in all_words:
            if (
                word.bounding_box
                and "y" in word.bounding_box
                and abs(word.bounding_box["y"] - target_y) <= y_tolerance
            ):
                line_words.append(word)

        # Sort by x position for proper reading order
        line_words.sort(
            key=lambda w: w.bounding_box.get("x", 0) if w.bounding_box else 0
        )
        return line_words

    @staticmethod
    def get_nearby_words(
        target_word: ReceiptWord,
        all_words: List[ReceiptWord],
        max_distance: float = 50.0,
        max_count: int = 5,
    ) -> List[Tuple[ReceiptWord, float]]:
        """
        Get words near the target word, sorted by distance.

        Args:
            target_word: The word to find neighbors for
            all_words: All words in the receipt
            max_distance: Maximum pixel distance to consider
            max_count: Maximum number of neighbors to return

        Returns:
            List of (word, distance) tuples, sorted by distance
        """
        if not (
            target_word.bounding_box
            and "x" in target_word.bounding_box
            and "y" in target_word.bounding_box
        ):
            return []

        neighbors = []
        target_x, target_y = (
            target_word.bounding_box["x"],
            target_word.bounding_box["y"],
        )

        for word in all_words:
            if word.word_id == target_word.word_id or not (
                word.bounding_box
                and "x" in word.bounding_box
                and "y" in word.bounding_box
            ):
                continue

            distance = (
                (word.bounding_box["x"] - target_x) ** 2
                + (word.bounding_box["y"] - target_y) ** 2
            ) ** 0.5

            if distance <= max_distance:
                neighbors.append((word, distance))

        # Sort by distance and limit count
        neighbors.sort(key=lambda x: x[1])
        return neighbors[:max_count]

    @staticmethod
    def calculate_position_percentile(
        word: ReceiptWord, all_words: List[ReceiptWord]
    ) -> Dict[str, float]:
        """
        Calculate where a word appears relative to the overall receipt layout.

        Args:
            word: The word to analyze
            all_words: All words in the receipt

        Returns:
            Dictionary with position percentiles: {"x_percentile": 0.0-1.0, "y_percentile": 0.0-1.0}
        """
        if not (
            word.bounding_box
            and "x" in word.bounding_box
            and "y" in word.bounding_box
        ):
            return {"x_percentile": 0.5, "y_percentile": 0.5}

        # Get min/max positions
        x_positions = [
            w.bounding_box["x"]
            for w in all_words
            if w.bounding_box and "x" in w.bounding_box
        ]
        y_positions = [
            w.bounding_box["y"]
            for w in all_words
            if w.bounding_box and "y" in w.bounding_box
        ]

        if not x_positions or not y_positions:
            return {"x_percentile": 0.5, "y_percentile": 0.5}

        min_x, max_x = min(x_positions), max(x_positions)
        min_y, max_y = min(y_positions), max(y_positions)

        x_percentile = (
            (word.bounding_box["x"] - min_x) / (max_x - min_x)
            if max_x > min_x
            else 0.5
        )
        y_percentile = (
            (word.bounding_box["y"] - min_y) / (max_y - min_y)
            if max_y > min_y
            else 0.5
        )

        return {
            "x_percentile": max(0.0, min(1.0, x_percentile)),
            "y_percentile": max(0.0, min(1.0, y_percentile)),
        }


class KeywordMatcher:
    """Optimized keyword matching using compiled patterns."""

    def __init__(self, keyword_sets: Dict[str, Set[str]]):
        """
        Initialize with keyword sets.

        Args:
            keyword_sets: Dictionary mapping category -> set of keywords
        """
        self.keyword_sets = keyword_sets
        self._compiled_patterns = {}

        # Pre-compile patterns for each keyword set
        for category, keywords in keyword_sets.items():
            # Sort by length (longer first) to match "grand total" before "total"
            sorted_keywords = sorted(keywords, key=len, reverse=True)
            escaped_keywords = [re.escape(kw) for kw in sorted_keywords]
            pattern = r"\b(?:" + "|".join(escaped_keywords) + r")\b"
            self._compiled_patterns[category] = re.compile(
                pattern, re.IGNORECASE
            )

    def find_keywords(
        self, text: str, categories: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """
        Find keywords in text using compiled patterns.

        Args:
            text: Text to search
            categories: Specific categories to search (None = all)

        Returns:
            Dictionary mapping category -> list of found keywords
        """
        results = {}
        categories_to_check = categories or list(
            self._compiled_patterns.keys()
        )

        for category in categories_to_check:
            if category in self._compiled_patterns:
                matches = self._compiled_patterns[category].findall(text)
                results[category] = [match.lower() for match in matches]
            else:
                results[category] = []

        return results

    def has_keywords(self, text: str, category: str) -> bool:
        """
        Check if text contains any keywords from a category.

        Args:
            text: Text to search
            category: Category to check

        Returns:
            True if any keywords found
        """
        if category not in self._compiled_patterns:
            return False
        return bool(self._compiled_patterns[category].search(text))


# Global instances for common use cases
CURRENCY_KEYWORD_MATCHER = KeywordMatcher(PatternConfig.CURRENCY_KEYWORDS)
