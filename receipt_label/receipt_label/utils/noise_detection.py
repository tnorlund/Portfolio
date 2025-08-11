"""
Noise word detection utilities for receipt processing.

This module provides functionality to identify noise words (punctuation, separators,
OCR artifacts) that should be stored but not embedded or labeled.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class NoiseDetectionConfig:
    """Configuration for noise word detection.

    Attributes:
        punctuation_patterns: Regex patterns for single punctuation marks
        separator_patterns: Regex patterns for separator characters
        artifact_patterns: Regex patterns for non-alphanumeric artifacts
        min_word_length: Minimum length for a word to be considered meaningful
        preserve_currency: Whether to preserve currency symbols as non-noise
    """

    # Single punctuation: . , ; : ! ? " ' - ( ) [ ] { }
    punctuation_patterns: List[str] = field(
        default_factory=lambda: [r"^[.,;:!?\"\'\-\(\)\[\]\{\}]$"]
    )

    # Separators: | / \ ~ _ = + * & %
    separator_patterns: List[str] = field(
        default_factory=lambda: [r"^[|/\\~_=+*&%]$"]
    )

    # Non-alphanumeric strings
    artifact_patterns: List[str] = field(
        default_factory=lambda: [r"^[^\w\s]+$"]
    )

    # Minimum meaningful length
    min_word_length: int = 2

    # Currency preservation (don't mark as noise)
    preserve_currency: bool = True


# Default configuration instance
DEFAULT_NOISE_CONFIG = NoiseDetectionConfig()


def is_noise_word(
    text: str, config: Optional[NoiseDetectionConfig] = None
) -> bool:
    """
    Determine if a word is noise based on configurable patterns.

    Args:
        text: The word text to check
        config: Optional configuration, uses DEFAULT_NOISE_CONFIG if not provided

    Returns:
        True if the word is considered noise, False otherwise

    Examples:
        >>> is_noise_word(".")  # Single punctuation
        True
        >>> is_noise_word(",")  # Single punctuation
        True
        >>> is_noise_word("|")  # Separator
        True
        >>> is_noise_word("---")  # Multi-character separator
        True
        >>> is_noise_word("TOTAL")  # Meaningful word
        False
        >>> is_noise_word("$5.99")  # Currency (preserved by default)
        False
        >>> is_noise_word("QTY")  # Short but meaningful
        False
    """
    if config is None:
        config = DEFAULT_NOISE_CONFIG

    # Empty or whitespace-only strings are noise
    if not text or text.isspace():
        return True

    # Currency preservation check
    if config.preserve_currency:
        # Common currency symbols and patterns
        currency_patterns = [
            r"^\$[\d,]+\.?\d*$",  # $5.99, $1,234.56
            r"^€[\d,]+\.?\d*$",  # €10, €1,234.56
            r"^£[\d,]+\.?\d*$",  # £15, £1,234.56
            r"^¥[\d,]+\.?\d*$",  # ¥100, ¥1,234
            r"^[\d,]+\.?\d*\$$",  # 5.99$, 1,234.56$
            r"^[\d,]+\.?\d*€$",  # 10€, 1,234.56€
            r"^[\d,]+\.?\d*£$",  # 15£, 1,234.56£
            r"^[\d,]+\.?\d*¥$",  # 100¥, 1,234¥
            r"^\$$",  # Just dollar sign
            r"^€$",  # Just euro sign
            r"^£$",  # Just pound sign
            r"^¥$",  # Just yen sign
        ]

        for pattern in currency_patterns:
            if re.match(pattern, text):
                return False

    # Check punctuation patterns
    for pattern in config.punctuation_patterns:
        if re.match(pattern, text):
            return True

    # Check separator patterns
    for pattern in config.separator_patterns:
        if re.match(pattern, text):
            return True

    # Check artifact patterns (but exclude meaningful short words)
    # First check if it's purely non-alphanumeric
    for pattern in config.artifact_patterns:
        if re.match(pattern, text):
            # Check if it's a meaningful short word before marking as noise
            meaningful_short_words = {
                "@",
                "#",
                "&",
                "+",
                "%",  # Can be part of meaningful context
            }
            if text not in meaningful_short_words:
                return True

    # Length-based check for single characters (excluding numbers and letters)
    if len(text) == 1 and not text.isalnum():
        # Already covered by patterns above, but this is a fallback
        return True

    # Multi-character separators and artifacts
    multi_char_noise_patterns = [
        r"^-+$",  # Multiple dashes: --, ---, ----
        r"^=+$",  # Multiple equals: ==, ===, ====
        r"^\*+$",  # Multiple asterisks: **, ***, ****
        r"^_+$",  # Multiple underscores: __, ___, ____
        r"^\.+$",  # Multiple dots: .., ..., ....
        r"^/+$",  # Multiple slashes: //, ///, ////
        r"^\\+$",  # Multiple backslashes: \\, \\\, \\\\
        r"^~+$",  # Multiple tildes: ~~, ~~~, ~~~~
    ]

    for pattern in multi_char_noise_patterns:
        if re.match(pattern, text):
            return True

    # Not noise - it's a meaningful word
    return False


def is_noise_line(
    text: str, config: Optional[NoiseDetectionConfig] = None
) -> bool:
    """
    Determine if an entire line is noise based on its content.

    A line is considered noise if it:
    - Contains only separator characters (===, ---, etc.)
    - Is empty or only whitespace
    - Contains only noise words (when split into words)
    - Matches common separator line patterns

    Args:
        text: The line text to check
        config: Optional configuration, uses DEFAULT_NOISE_CONFIG if not provided

    Returns:
        True if the line is considered noise, False otherwise

    Examples:
        >>> is_noise_line("==================")  # Separator line
        True
        >>> is_noise_line("--------------------")  # Separator line
        True
        >>> is_noise_line("********************")  # Separator line
        True
        >>> is_noise_line("   ")  # Whitespace only
        True
        >>> is_noise_line("")  # Empty
        True
        >>> is_noise_line("| | | | |")  # All noise words
        True
        >>> is_noise_line("TOTAL: $12.99")  # Meaningful line
        False
        >>> is_noise_line("Big Mac")  # Product name
        False
    """
    if config is None:
        config = DEFAULT_NOISE_CONFIG

    # Empty or whitespace-only lines are noise
    if not text or text.isspace():
        return True

    # Common separator line patterns (entire line is just separators)
    separator_line_patterns = [
        r"^-+$",  # All dashes
        r"^=+$",  # All equals
        r"^\*+$",  # All asterisks
        r"^_+$",  # All underscores
        r"^\.+$",  # All dots
        r"^/+$",  # All forward slashes
        r"^\\+$",  # All backslashes
        r"^~+$",  # All tildes
        r"^\|+$",  # All pipes
        r"^##+$",  # All hashes (##########)
        r"^[+\-=*_./\\~|# ]+$",  # Mix of separator characters with spaces
    ]

    for pattern in separator_line_patterns:
        if re.match(pattern, text.strip()):
            return True

    # Check if all words in the line are noise
    # Split by whitespace and check each word
    words = text.split()
    if words:
        # If there are words, check if ALL of them are noise
        all_noise = all(is_noise_word(word, config) for word in words)
        if all_noise:
            return True

    # Line contains at least some meaningful content
    return False
