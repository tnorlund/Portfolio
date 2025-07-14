"""
Trie-based multi-word pattern detection using Aho-Corasick algorithm.

This module implements Phase 3's advanced pattern matching for multi-word sequences
like "Big Mac", "grand total", "sales tax", and merchant-specific product names.
The Aho-Corasick algorithm enables efficient searching for multiple patterns simultaneously.
"""

from typing import Dict, List, Set, Tuple, Optional, Any, NamedTuple
from dataclasses import dataclass
from collections import defaultdict, deque
import re
from enum import Enum

from receipt_label.pattern_detection.base import PatternType
from receipt_label.pattern_detection.patterns_config import PatternConfig
from receipt_dynamo.entities import ReceiptWord


class MatchType(Enum):
    """Types of multi-word matches."""
    EXACT = "exact"              # Exact phrase match: "grand total"
    FUZZY = "fuzzy"              # Fuzzy match with OCR errors: "grand fotal"
    PARTIAL = "partial"          # Partial match: "total" matches "grand total"
    COMPOUND = "compound"        # Compound product: "Big Mac"


@dataclass
class MultiWordMatch:
    """Represents a multi-word pattern match."""
    words: List[ReceiptWord]     # Words that make up the match
    pattern: str                 # Original pattern that matched
    pattern_type: PatternType    # Semantic type of the pattern
    match_type: MatchType        # How the pattern matched
    confidence: float            # Match confidence (0.0-1.0)
    start_position: int          # Start word index in receipt
    end_position: int            # End word index in receipt
    metadata: Dict[str, Any]     # Additional match information


class TrieNode:
    """Node in the pattern trie for efficient multi-word matching."""
    
    def __init__(self):
        self.children: Dict[str, 'TrieNode'] = {}
        self.is_end_of_pattern = False
        self.patterns: List[Tuple[str, PatternType]] = []  # (pattern, type) pairs
        self.failure_link: Optional['TrieNode'] = None    # For Aho-Corasick
        self.output_link: Optional['TrieNode'] = None     # For Aho-Corasick
    
    def add_pattern(self, words: List[str], pattern: str, pattern_type: PatternType):
        """Add a multi-word pattern to the trie."""
        current = self
        for word in words:
            word_normalized = word.lower().strip()
            if word_normalized not in current.children:
                current.children[word_normalized] = TrieNode()
            current = current.children[word_normalized]
        
        current.is_end_of_pattern = True
        current.patterns.append((pattern, pattern_type))


class AhoCorasickMatcher:
    """
    Aho-Corasick algorithm implementation for efficient multi-word pattern matching.
    
    This enables simultaneous searching for hundreds of patterns in linear time,
    making it ideal for merchant-specific product names and receipt phrases.
    """
    
    def __init__(self):
        self.root = TrieNode()
        self._compiled = False
        self.pattern_count = 0
    
    def add_patterns(self, patterns: Dict[str, PatternType]):
        """
        Add multiple patterns to the matcher.
        
        Args:
            patterns: Dictionary mapping pattern_string -> PatternType
        """
        for pattern, pattern_type in patterns.items():
            self.add_pattern(pattern, pattern_type)
    
    def add_pattern(self, pattern: str, pattern_type: PatternType):
        """Add a single multi-word pattern."""
        words = self._tokenize_pattern(pattern)
        if words:  # Only add non-empty patterns
            self.root.add_pattern(words, pattern, pattern_type)
            self.pattern_count += 1
            self._compiled = False  # Mark as needing recompilation
    
    def _tokenize_pattern(self, pattern: str) -> List[str]:
        """Tokenize pattern into words, handling punctuation and normalization."""
        # Remove extra whitespace and normalize
        pattern = re.sub(r'\s+', ' ', pattern.strip())
        
        # Split on whitespace and common punctuation
        words = re.split(r'[\s\-_]+', pattern)
        
        # Filter out empty strings and normalize
        return [word.lower() for word in words if word.strip()]
    
    def compile(self):
        """Build failure links for Aho-Corasick algorithm."""
        if self._compiled:
            return
        
        # Build failure links using BFS
        queue = deque([self.root])
        self.root.failure_link = self.root
        
        # First level - all children point to root
        for child in self.root.children.values():
            child.failure_link = self.root
            queue.append(child)
        
        # Build failure links for deeper levels
        while queue:
            current = queue.popleft()
            
            for char, child in current.children.items():
                queue.append(child)
                
                # Find the failure link
                failure = current.failure_link
                while failure != self.root and char not in failure.children:
                    failure = failure.failure_link
                
                if char in failure.children and failure.children[char] != child:
                    child.failure_link = failure.children[char]
                else:
                    child.failure_link = self.root
                
                # Build output links for suffix patterns
                if child.failure_link.is_end_of_pattern:
                    child.output_link = child.failure_link
                else:
                    child.output_link = child.failure_link.output_link
        
        self._compiled = True
    
    def find_matches(self, words: List[ReceiptWord]) -> List[MultiWordMatch]:
        """
        Find all multi-word pattern matches in the word sequence.
        
        Args:
            words: Sequence of receipt words
            
        Returns:
            List of multi-word matches found
        """
        if not self._compiled:
            self.compile()
        
        matches = []
        current_node = self.root
        
        # Track positions of non-noise words for accurate span calculation
        non_noise_positions = []
        
        for i, word in enumerate(words):
            if word.is_noise:
                continue
            
            non_noise_positions.append(i)
            word_text = word.text.lower().strip()
            
            # Follow failure links until we find a match or reach root
            while current_node != self.root and word_text not in current_node.children:
                current_node = current_node.failure_link
            
            # Move to next state if possible
            if word_text in current_node.children:
                current_node = current_node.children[word_text]
            
            # Check for matches at current position
            # Pass both the actual position and the non-noise positions
            matches.extend(self._collect_matches_at_position(
                current_node, words, i, non_noise_positions
            ))
        
        return matches
    
    def _collect_matches_at_position(self, node: TrieNode, words: List[ReceiptWord], 
                                   end_position: int, non_noise_positions: List[int]) -> List[MultiWordMatch]:
        """Collect all pattern matches ending at the current position."""
        matches = []
        current = node
        
        while current:
            if current.is_end_of_pattern:
                for pattern, pattern_type in current.patterns:
                    # Calculate how many non-noise words are in the pattern
                    pattern_words = self._tokenize_pattern(pattern)
                    pattern_length = len(pattern_words)
                    
                    # Find the current position in the non-noise sequence
                    current_non_noise_idx = non_noise_positions.index(end_position)
                    
                    # Calculate the start index in the non-noise sequence
                    start_non_noise_idx = max(0, current_non_noise_idx - pattern_length + 1)
                    
                    # Extract matching words using actual positions
                    matched_words = []
                    for idx in range(start_non_noise_idx, current_non_noise_idx + 1):
                        if idx < len(non_noise_positions):
                            word_position = non_noise_positions[idx]
                            if word_position < len(words):
                                matched_words.append(words[word_position])
                    
                    if matched_words:
                        confidence = self._calculate_match_confidence(
                            pattern, matched_words, MatchType.EXACT
                        )
                        
                        # Get actual word positions in the original words list
                        # These are the indices where matched_words appear in the words list
                        if start_non_noise_idx < len(non_noise_positions):
                            start_position = non_noise_positions[start_non_noise_idx]
                        else:
                            start_position = 0
                            
                        # end_position is already the correct index in the words list
                        
                        match = MultiWordMatch(
                            words=matched_words,
                            pattern=pattern,
                            pattern_type=pattern_type,
                            match_type=MatchType.EXACT,
                            confidence=confidence,
                            start_position=start_position,
                            end_position=end_position,
                            metadata={
                                "algorithm": "aho_corasick",
                                "pattern_length": len(pattern_words),
                                "word_span": len(matched_words)
                            }
                        )
                        matches.append(match)
            
            current = current.output_link
        
        return matches
    
    def _calculate_match_confidence(self, pattern: str, words: List[ReceiptWord], 
                                  match_type: MatchType) -> float:
        """Calculate confidence score for a multi-word match."""
        base_confidence = 0.9  # High confidence for exact trie matches
        
        # Adjust based on match type
        if match_type == MatchType.EXACT:
            confidence = base_confidence
        elif match_type == MatchType.FUZZY:
            confidence = base_confidence * 0.8
        elif match_type == MatchType.PARTIAL:
            confidence = base_confidence * 0.7
        else:
            confidence = base_confidence * 0.6
        
        # Boost confidence for longer patterns (more specific)
        pattern_length = len(self._tokenize_pattern(pattern))
        if pattern_length >= 3:
            confidence *= 1.1
        elif pattern_length >= 2:
            confidence *= 1.05
        
        # Reduce confidence for very short words that might be spurious
        if any(len(w.text) <= 2 for w in words):
            confidence *= 0.9
        
        return min(confidence, 1.0)


class MultiWordPatternDetector:
    """
    High-level multi-word pattern detector that combines trie matching
    with fuzzy matching and context analysis.
    """
    
    def __init__(self):
        self.exact_matcher = AhoCorasickMatcher()
        self.fuzzy_matcher = AhoCorasickMatcher()
        self._initialize_patterns()
    
    def _initialize_patterns(self):
        """Initialize with common multi-word patterns."""
        
        # Financial patterns
        financial_patterns = {
            "grand total": PatternType.GRAND_TOTAL,
            "final total": PatternType.GRAND_TOTAL,
            "total amount": PatternType.GRAND_TOTAL,
            "amount due": PatternType.GRAND_TOTAL,
            "balance due": PatternType.GRAND_TOTAL,
            "sub total": PatternType.SUBTOTAL,
            "subtotal": PatternType.SUBTOTAL,
            "merchandise total": PatternType.SUBTOTAL,
            "sales tax": PatternType.TAX,
            "tax amount": PatternType.TAX,
            "state tax": PatternType.TAX,
            "city tax": PatternType.TAX,
            "local tax": PatternType.TAX,
        }
        
        # Common product patterns (these would typically come from merchant data)
        product_patterns = {
            "big mac": PatternType.PRODUCT_NAME,
            "quarter pounder": PatternType.PRODUCT_NAME,
            "mc chicken": PatternType.PRODUCT_NAME,
            "mc nuggets": PatternType.PRODUCT_NAME,
            "french fries": PatternType.PRODUCT_NAME,
            "coca cola": PatternType.PRODUCT_NAME,
            "diet coke": PatternType.PRODUCT_NAME,
            "ice cream": PatternType.PRODUCT_NAME,
            "chicken sandwich": PatternType.PRODUCT_NAME,
            "cheese burger": PatternType.PRODUCT_NAME,
        }
        
        # Quantity and measurement patterns
        quantity_patterns = {
            "per pound": PatternType.UNIT_PRICE,
            "per lb": PatternType.UNIT_PRICE,
            "per ounce": PatternType.UNIT_PRICE,
            "per oz": PatternType.UNIT_PRICE,
            "per gallon": PatternType.UNIT_PRICE,
            "per gal": PatternType.UNIT_PRICE,
            "each item": PatternType.QUANTITY,
            "per item": PatternType.UNIT_PRICE,
        }
        
        # Combine all patterns
        all_patterns = {**financial_patterns, **product_patterns, **quantity_patterns}
        
        # Add to exact matcher
        self.exact_matcher.add_patterns(all_patterns)
        
        # Create fuzzy variants for common OCR errors
        fuzzy_patterns = self._generate_fuzzy_variants(all_patterns)
        self.fuzzy_matcher.add_patterns(fuzzy_patterns)
    
    def _generate_fuzzy_variants(self, patterns: Dict[str, PatternType]) -> Dict[str, PatternType]:
        """Generate fuzzy variants for common OCR errors."""
        fuzzy_patterns = {}
        
        # Common OCR substitutions
        ocr_substitutions = {
            'o': '0', '0': 'o',  # o/0 confusion
            'i': 'l', 'l': 'i',  # i/l confusion  
            'rn': 'm',           # rn -> m
            's': '5', '5': 's',  # s/5 confusion
            'g': 'q', 'q': 'g',  # g/q confusion
            'cl': 'd',           # cl -> d
        }
        
        for pattern, pattern_type in patterns.items():
            # Generate a few fuzzy variants for each pattern
            for original, replacement in ocr_substitutions.items():
                if original in pattern:
                    fuzzy_pattern = pattern.replace(original, replacement)
                    if fuzzy_pattern != pattern:  # Only add if different
                        fuzzy_patterns[fuzzy_pattern] = pattern_type
        
        return fuzzy_patterns
    
    def detect_multi_word_patterns(self, words: List[ReceiptWord]) -> List[MultiWordMatch]:
        """
        Detect all multi-word patterns in the receipt.
        
        Args:
            words: Receipt words to analyze
            
        Returns:
            List of multi-word pattern matches
        """
        matches = []
        
        # Exact matching
        exact_matches = self.exact_matcher.find_matches(words)
        matches.extend(exact_matches)
        
        # Fuzzy matching (only if exact matching didn't find much)
        if len(exact_matches) < 3:  # Threshold for "not much found"
            fuzzy_matches = self.fuzzy_matcher.find_matches(words)
            # Mark fuzzy matches and adjust confidence
            for match in fuzzy_matches:
                match.match_type = MatchType.FUZZY
                match.confidence *= 0.8  # Reduce confidence for fuzzy matches
            matches.extend(fuzzy_matches)
        
        # Remove overlapping matches (prefer exact over fuzzy, longer over shorter)
        matches = self._resolve_overlapping_matches(matches)
        
        return matches
    
    def _resolve_overlapping_matches(self, matches: List[MultiWordMatch]) -> List[MultiWordMatch]:
        """Resolve overlapping matches by keeping the best ones."""
        if not matches:
            return matches
        
        # Sort by start position, then by preference (exact > fuzzy, longer > shorter)
        matches.sort(key=lambda m: (
            m.start_position,
            m.match_type != MatchType.EXACT,  # Exact matches first
            -(m.end_position - m.start_position),  # Longer matches first
            -m.confidence  # Higher confidence first
        ))
        
        resolved_matches = []
        used_positions = set()
        
        for match in matches:
            # Check if this match overlaps with any already selected
            match_positions = set(range(match.start_position, match.end_position + 1))
            if not match_positions.intersection(used_positions):
                resolved_matches.append(match)
                used_positions.update(match_positions)
        
        return resolved_matches
    
    def add_merchant_patterns(self, merchant_name: str, patterns: Dict[str, PatternType]):
        """
        Add merchant-specific patterns dynamically.
        
        Args:
            merchant_name: Name of the merchant
            patterns: Dictionary of pattern -> PatternType
        """
        # Add merchant prefix to avoid conflicts
        prefixed_patterns = {
            f"{merchant_name.lower()}_{pattern}": pattern_type
            for pattern, pattern_type in patterns.items()
        }
        
        self.exact_matcher.add_patterns(prefixed_patterns)
        # Force recompilation
        self.exact_matcher._compiled = False
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about loaded patterns."""
        return {
            "exact_patterns": self.exact_matcher.pattern_count,
            "fuzzy_patterns": self.fuzzy_matcher.pattern_count,
            "total_patterns": self.exact_matcher.pattern_count + self.fuzzy_matcher.pattern_count,
            "algorithm": "aho_corasick",
            "capabilities": [
                "multi_word_detection",
                "fuzzy_matching", 
                "merchant_specific_patterns",
                "overlap_resolution"
            ]
        }


# Global multi-word detector instance
MULTI_WORD_DETECTOR = MultiWordPatternDetector()