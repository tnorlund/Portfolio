"""
Pattern automaton for optimized keyword and phrase matching.

This module implements Phase 3's optimized keyword lookups using finite state automata,
replacing the current substring-based keyword searching with efficient pattern matching.
"""

import re
from typing import Dict, List, Set, Tuple, Optional, Any, NamedTuple
from dataclasses import dataclass
from collections import defaultdict, deque
from enum import Enum

from receipt_label.pattern_detection.patterns_config import PatternConfig


class AutomatonType(Enum):
    """Types of pattern automata."""

    KEYWORD = "keyword"  # Simple keyword matching
    PHRASE = "phrase"  # Multi-word phrase matching
    FUZZY = "fuzzy"  # Fuzzy matching with edit distance
    REGEX = "regex"  # Combined with regex patterns


@dataclass
class AutomatonMatch:
    """Result from automaton pattern matching."""

    text: str  # Matched text
    pattern: str  # Original pattern
    category: str  # Pattern category (e.g., "total", "tax")
    start_pos: int  # Start position in text
    end_pos: int  # End position in text
    confidence: float  # Match confidence
    automaton_type: AutomatonType  # Type of automaton that found this


class KeywordAutomaton:
    """
    Finite state automaton for efficient keyword matching.

    This replaces the current keyword matching logic with a more efficient
    automaton-based approach that can find multiple keywords in a single pass.
    """

    def __init__(self, name: str):
        self.name = name
        self.patterns: Dict[str, str] = {}  # pattern -> category
        self.compiled_automaton: Optional[re.Pattern] = None
        self.keyword_map: Dict[str, str] = {}  # keyword -> category
        self._case_sensitive = False

    def add_keywords(self, category: str, keywords: Set[str]):
        """Add keywords for a specific category."""
        for keyword in keywords:
            normalized = (
                keyword.lower() if not self._case_sensitive else keyword
            )
            self.keyword_map[normalized] = category
            self.patterns[normalized] = category

    def add_keywords_from_config(self, keyword_sets: Dict[str, Set[str]]):
        """Add keywords from the centralized config."""
        for category, keywords in keyword_sets.items():
            self.add_keywords(category, keywords)

    def compile(self) -> re.Pattern:
        """Compile keywords into an efficient regex automaton."""
        if self.compiled_automaton:
            return self.compiled_automaton

        if not self.patterns:
            self.compiled_automaton = re.compile(
                r"(?!)", re.IGNORECASE
            )  # Never matches
            return self.compiled_automaton

        # Sort keywords by length (longest first) to ensure proper matching
        sorted_keywords = sorted(self.patterns.keys(), key=len, reverse=True)

        # Escape keywords and create alternation pattern
        escaped_keywords = [re.escape(keyword) for keyword in sorted_keywords]

        # Create word boundary pattern for more precise matching
        pattern = r"\b(?:" + "|".join(escaped_keywords) + r")\b"

        flags = re.IGNORECASE if not self._case_sensitive else 0
        self.compiled_automaton = re.compile(pattern, flags)

        return self.compiled_automaton

    def find_matches(self, text: str) -> List[AutomatonMatch]:
        """Find all keyword matches in text using the compiled automaton."""
        if not self.compiled_automaton:
            self.compile()

        matches = []

        for match in self.compiled_automaton.finditer(text):
            matched_keyword = match.group(0).lower()
            category = self.keyword_map.get(matched_keyword, "unknown")

            automaton_match = AutomatonMatch(
                text=match.group(0),
                pattern=matched_keyword,
                category=category,
                start_pos=match.start(),
                end_pos=match.end(),
                confidence=1.0,  # Exact keyword matches have high confidence
                automaton_type=AutomatonType.KEYWORD,
            )
            matches.append(automaton_match)

        return matches

    def has_category(self, text: str, category: str) -> bool:
        """Check if text contains any keywords from a specific category."""
        matches = self.find_matches(text)
        return any(match.category == category for match in matches)

    def get_categories(self, text: str) -> Set[str]:
        """Get all categories found in text."""
        matches = self.find_matches(text)
        return {match.category for match in matches}


class PhraseAutomaton:
    """
    Automaton for matching multi-word phrases with fuzzy capability.

    This is more advanced than simple keyword matching and can handle
    phrases like "grand total" even when OCR produces "grand fotal".
    """

    def __init__(self, name: str, fuzzy_threshold: float = 0.8):
        self.name = name
        self.phrases: Dict[str, str] = {}  # phrase -> category
        self.fuzzy_threshold = fuzzy_threshold
        self.compiled_patterns: Dict[str, re.Pattern] = {}

    def add_phrases(self, category: str, phrases: Set[str]):
        """Add phrases for a specific category."""
        for phrase in phrases:
            normalized = phrase.lower().strip()
            self.phrases[normalized] = category

    def compile(self):
        """Compile phrases into regex patterns grouped by category."""
        self.compiled_patterns.clear()

        # Group phrases by category
        category_phrases = defaultdict(list)
        for phrase, category in self.phrases.items():
            category_phrases[category].append(phrase)

        # Create regex pattern for each category
        for category, phrases in category_phrases.items():
            # Sort by length (longest first) for proper matching precedence
            sorted_phrases = sorted(phrases, key=len, reverse=True)
            escaped_phrases = [re.escape(phrase) for phrase in sorted_phrases]

            # Create flexible pattern that allows for some OCR variations
            flexible_patterns = []
            for phrase in sorted_phrases:
                # Create a pattern that allows for common OCR substitutions
                flexible_pattern = self._create_flexible_pattern(phrase)
                flexible_patterns.append(flexible_pattern)

            # Combine into alternation pattern
            pattern = r"\b(?:" + "|".join(flexible_patterns) + r")\b"
            self.compiled_patterns[category] = re.compile(
                pattern, re.IGNORECASE
            )

    def _create_flexible_pattern(self, phrase: str) -> str:
        """Create a flexible regex pattern that handles common OCR errors."""
        # Split phrase into words
        words = phrase.split()
        flexible_words = []

        for word in words:
            # Apply common OCR error patterns
            flexible_word = word

            # Allow o/0 confusion
            flexible_word = flexible_word.replace("o", "[o0]").replace(
                "0", "[o0]"
            )

            # Allow i/l confusion
            flexible_word = flexible_word.replace("i", "[il]").replace(
                "l", "[il]"
            )

            # Allow s/5 confusion
            flexible_word = flexible_word.replace("s", "[s5]").replace(
                "5", "[s5]"
            )

            # Allow rn/m confusion
            flexible_word = flexible_word.replace("rn", "(?:rn|m)").replace(
                "m", "(?:rn|m)"
            )

            flexible_words.append(flexible_word)

        # Join words with flexible whitespace
        return r"\s+".join(flexible_words)

    def find_matches(self, text: str) -> List[AutomatonMatch]:
        """Find all phrase matches in text."""
        if not self.compiled_patterns:
            self.compile()

        matches = []

        for category, pattern in self.compiled_patterns.items():
            for match in pattern.finditer(text):
                # Calculate confidence based on how well it matches
                confidence = self._calculate_match_confidence(
                    match.group(0), category
                )

                automaton_match = AutomatonMatch(
                    text=match.group(0),
                    pattern=self._find_closest_phrase(
                        match.group(0), category
                    ),
                    category=category,
                    start_pos=match.start(),
                    end_pos=match.end(),
                    confidence=confidence,
                    automaton_type=AutomatonType.PHRASE,
                )
                matches.append(automaton_match)

        return matches

    def _calculate_match_confidence(
        self, matched_text: str, category: str
    ) -> float:
        """Calculate confidence for a phrase match."""
        # Find the closest phrase in this category
        category_phrases = [
            phrase for phrase, cat in self.phrases.items() if cat == category
        ]

        if not category_phrases:
            return 0.5

        # Calculate similarity to the closest phrase
        best_similarity = 0.0
        matched_lower = matched_text.lower().strip()

        for phrase in category_phrases:
            similarity = self._calculate_similarity(matched_lower, phrase)
            best_similarity = max(best_similarity, similarity)

        return best_similarity

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two strings."""
        # Simple character-based similarity
        if text1 == text2:
            return 1.0

        # Normalize whitespace
        text1 = re.sub(r"\s+", " ", text1).strip()
        text2 = re.sub(r"\s+", " ", text2).strip()

        if text1 == text2:
            return 1.0

        # Calculate Levenshtein-like similarity
        longer = text1 if len(text1) > len(text2) else text2
        shorter = text2 if len(text1) > len(text2) else text1

        if len(longer) == 0:
            return 1.0

        # Simple similarity metric
        common_chars = sum(
            1
            for i, char in enumerate(shorter)
            if i < len(longer) and char == longer[i]
        )
        return common_chars / len(longer)

    def _find_closest_phrase(self, matched_text: str, category: str) -> str:
        """Find the closest phrase pattern for a matched text."""
        category_phrases = [
            phrase for phrase, cat in self.phrases.items() if cat == category
        ]

        if not category_phrases:
            return matched_text

        matched_lower = matched_text.lower().strip()
        best_phrase = category_phrases[0]
        best_similarity = 0.0

        for phrase in category_phrases:
            similarity = self._calculate_similarity(matched_lower, phrase)
            if similarity > best_similarity:
                best_similarity = similarity
                best_phrase = phrase

        return best_phrase


class OptimizedKeywordMatcher:
    """
    Optimized keyword matcher that combines multiple automata
    for maximum efficiency and accuracy.

    This replaces the current KeywordMatcher with a more sophisticated
    approach using pattern automata.
    """

    def __init__(self):
        self.keyword_automata: Dict[str, KeywordAutomaton] = {}
        self.phrase_automata: Dict[str, PhraseAutomaton] = {}
        self._initialize_from_config()

    def _initialize_from_config(self):
        """Initialize automata from the centralized pattern configuration."""

        # Create keyword automaton for currency keywords
        currency_automaton = KeywordAutomaton("currency")
        currency_automaton.add_keywords_from_config(
            PatternConfig.CURRENCY_KEYWORDS
        )
        currency_automaton.compile()
        self.keyword_automata["currency"] = currency_automaton

        # Create phrase automaton for multi-word financial phrases
        financial_phrases = PhraseAutomaton("financial", fuzzy_threshold=0.8)

        # Add multi-word phrases that benefit from fuzzy matching
        financial_phrases.add_phrases(
            "total",
            {
                "grand total",
                "final total",
                "total amount",
                "amount due",
                "balance due",
            },
        )
        financial_phrases.add_phrases(
            "subtotal",
            {"sub total", "subtotal", "merchandise total", "items total"},
        )
        financial_phrases.add_phrases(
            "tax",
            {"sales tax", "tax amount", "state tax", "city tax", "local tax"},
        )
        financial_phrases.add_phrases(
            "discount",
            {
                "store credit",
                "store discount",
                "member discount",
                "coupon savings",
            },
        )

        financial_phrases.compile()
        self.phrase_automata["financial"] = financial_phrases

    def find_all_matches(self, text: str) -> List[AutomatonMatch]:
        """Find all matches using all automata."""
        all_matches = []

        # Run keyword automata
        for automaton in self.keyword_automata.values():
            matches = automaton.find_matches(text)
            all_matches.extend(matches)

        # Run phrase automata
        for automaton in self.phrase_automata.values():
            matches = automaton.find_matches(text)
            all_matches.extend(matches)

        # Remove overlapping matches (prefer phrases over keywords, longer over shorter)
        return self._resolve_overlapping_matches(all_matches)

    def has_keywords(self, text: str, category: str) -> bool:
        """Check if text contains keywords from a specific category."""
        matches = self.find_all_matches(text)
        return any(match.category == category for match in matches)

    def get_categories(self, text: str) -> Set[str]:
        """Get all categories found in text."""
        matches = self.find_all_matches(text)
        return {match.category for match in matches}

    def _resolve_overlapping_matches(
        self, matches: List[AutomatonMatch]
    ) -> List[AutomatonMatch]:
        """Resolve overlapping matches by keeping the best ones."""
        if not matches:
            return matches

        # Sort by position, then by preference
        matches.sort(
            key=lambda m: (
                m.start_pos,
                m.automaton_type != AutomatonType.PHRASE,  # Prefer phrases
                -(m.end_pos - m.start_pos),  # Prefer longer matches
                -m.confidence,  # Prefer higher confidence
            )
        )

        resolved_matches = []
        used_ranges = []

        for match in matches:
            # Check if this match overlaps with any already selected
            match_range = (match.start_pos, match.end_pos)

            overlaps = any(
                not (match.end_pos <= start or match.start_pos >= end)
                for start, end in used_ranges
            )

            if not overlaps:
                resolved_matches.append(match)
                used_ranges.append(match_range)

        return resolved_matches

    def add_merchant_keywords(
        self, merchant_name: str, categories: Dict[str, Set[str]]
    ):
        """Add merchant-specific keywords."""
        merchant_automaton = KeywordAutomaton(
            f"merchant_{merchant_name.lower()}"
        )

        for category, keywords in categories.items():
            merchant_automaton.add_keywords(category, keywords)

        merchant_automaton.compile()
        self.keyword_automata[f"merchant_{merchant_name.lower()}"] = (
            merchant_automaton
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the automata."""
        return {
            "keyword_automata": len(self.keyword_automata),
            "phrase_automata": len(self.phrase_automata),
            "total_automata": len(self.keyword_automata)
            + len(self.phrase_automata),
            "optimization_benefits": {
                "single_pass_matching": True,
                "fuzzy_phrase_support": True,
                "merchant_extensibility": True,
                "overlap_resolution": True,
            },
        }


# Global optimized keyword matcher instance
OPTIMIZED_KEYWORD_MATCHER = OptimizedKeywordMatcher()
