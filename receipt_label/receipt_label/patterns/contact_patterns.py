"""
Contact pattern detector for receipt labeling.

Detects phone numbers, email addresses, and websites in receipt text.
"""

import re
from typing import Dict, List, Optional

# Import will be provided by the parent module to avoid circular imports


class ContactPatternDetector:
    """Detects contact information patterns in receipt text."""

    # Labels to use (will be set by parent module)
    PHONE_NUMBER_LABEL = "PHONE_NUMBER"
    EMAIL_LABEL = "EMAIL"
    WEBSITE_LABEL = "WEBSITE"

    # Phone number patterns
    PHONE_PATTERNS = [
        # US format with area code: (123) 456-7890, 123-456-7890, 123.456.7890
        (
            r"\b(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b",
            "us_phone",
        ),
        # International format: +1-123-456-7890, +44 20 7123 4567
        (
            r"\b\+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,4}\b",
            "international",
        ),
        # Compact format: 1234567890
        (r"\b1?\d{10}\b", "compact"),
        # With extension: 123-456-7890 x123
        (
            r"\b(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})[-.\s]?(?:x|ext|extension)[-.\s]?(\d+)\b",
            "with_extension",
        ),
    ]

    # Email patterns
    EMAIL_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

    # Website patterns
    WEBSITE_PATTERNS = [
        # Full URL with protocol
        (
            r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
            "full_url",
        ),
        # www.domain.com
        (
            r"\bwww\.[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
            "www_url",
        ),
        # domain.com (common TLDs)
        (
            r"\b(?!www\.)[-a-zA-Z0-9@:%._\+~#=]{1,256}\.(?:com|org|net|edu|gov|biz|info|io|co|us|uk|ca|au|de|fr|jp|cn)\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
            "domain_only",
        ),
    ]

    def detect(self, words: List[Dict]) -> List[Dict]:
        """
        Detect contact patterns in receipt words.

        Args:
            words: List of word dictionaries with 'text', 'word_id', etc.

        Returns:
            List of detected patterns with word_id, label, confidence
        """
        detected_patterns = []

        # Process each word
        for i, word in enumerate(words):
            text = word.get("text", "")
            word_id = word.get("word_id")

            # Check for phone numbers
            phone_info = self._check_phone_patterns(text)
            if phone_info:
                detected_patterns.append(
                    {
                        "word_id": word_id,
                        "text": text,
                        "label": self.PHONE_NUMBER_LABEL,
                        "confidence": phone_info["confidence"],
                        "pattern_type": phone_info["pattern_type"],
                        "normalized": phone_info.get("normalized"),
                    }
                )
                continue

            # Check for email
            if self._is_email(text):
                detected_patterns.append(
                    {
                        "word_id": word_id,
                        "text": text,
                        "label": self.EMAIL_LABEL,
                        "confidence": 0.95,
                        "pattern_type": "email",
                    }
                )
                continue

            # Check for website
            website_info = self._check_website_patterns(text)
            if website_info:
                detected_patterns.append(
                    {
                        "word_id": word_id,
                        "text": text,
                        "label": self.WEBSITE_LABEL,
                        "confidence": website_info["confidence"],
                        "pattern_type": website_info["pattern_type"],
                    }
                )
                continue

            # Check for split phone numbers (e.g., "(" "555" ")" "123-4567")
            if i < len(words) - 3 and text == "(":
                combined = self._combine_phone_parts(words[i : i + 5])
                if combined:
                    phone_info = self._check_phone_patterns(combined)
                    if phone_info:
                        # Mark all parts as PHONE_NUMBER
                        for j in range(min(5, len(words) - i)):
                            detected_patterns.append(
                                {
                                    "word_id": words[i + j].get("word_id"),
                                    "text": words[i + j].get("text", ""),
                                    "label": self.PHONE_NUMBER_LABEL,
                                    "confidence": phone_info["confidence"]
                                    * 0.9,
                                    "pattern_type": f"{phone_info['pattern_type']}_split",
                                }
                            )

        return detected_patterns

    def _check_phone_patterns(self, text: str) -> Optional[Dict]:
        """Check if text matches any phone pattern."""
        for pattern, pattern_type in self.PHONE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                # Validate it's actually a phone number
                digits = re.sub(r"\D", "", text)

                # US phone numbers
                if len(digits) == 10 or (
                    len(digits) == 11 and digits[0] == "1"
                ):
                    normalized = self._normalize_phone(digits)
                    return {
                        "pattern_type": pattern_type,
                        "normalized": normalized,
                        "confidence": self._calculate_phone_confidence(
                            text, pattern_type
                        ),
                    }

                # International numbers (more flexible)
                elif (
                    pattern_type == "international" and 7 <= len(digits) <= 15
                ):
                    return {
                        "pattern_type": pattern_type,
                        "confidence": 0.85,
                    }

        return None

    def _is_email(self, text: str) -> bool:
        """Check if text is an email address."""
        return bool(re.match(self.EMAIL_PATTERN, text))

    def _check_website_patterns(self, text: str) -> Optional[Dict]:
        """Check if text matches any website pattern."""
        # Skip if it looks like a price or measurement
        if "$" in text or any(c.isdigit() and "." in text for c in text):
            return None

        for pattern, pattern_type in self.WEBSITE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return {
                    "pattern_type": pattern_type,
                    "confidence": self._calculate_website_confidence(
                        text, pattern_type
                    ),
                }
        return None

    def _combine_phone_parts(self, words: List[Dict]) -> Optional[str]:
        """Try to combine words into a phone number."""
        if not words:
            return None

        # Common patterns for split phone numbers
        texts = [w.get("text", "") for w in words[:5]]

        # Pattern: "(" "555" ")" "123-4567"
        if len(texts) >= 4 and texts[0] == "(" and texts[2] == ")":
            return f"({texts[1]}){texts[3]}"

        # Pattern: "555" "-" "123" "-" "4567"
        if len(texts) >= 5 and texts[1] == "-" and texts[3] == "-":
            return f"{texts[0]}-{texts[2]}-{texts[4]}"

        return None

    def _normalize_phone(self, digits: str) -> str:
        """Normalize phone number to standard format."""
        # Remove leading 1 for US numbers
        if len(digits) == 11 and digits[0] == "1":
            digits = digits[1:]

        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

        return digits

    def _calculate_phone_confidence(
        self, text: str, pattern_type: str
    ) -> float:
        """Calculate confidence score for detected phone number."""
        confidence = 0.90  # Base confidence

        # Well-formatted numbers get higher confidence
        if pattern_type in ["us_phone", "with_extension"]:
            confidence = 0.95
        elif pattern_type == "compact":
            confidence = 0.85  # Could be other number types

        # Check for phone keywords nearby (would need context)
        phone_keywords = ["tel", "phone", "ph", "fax", "call"]
        if any(keyword in text.lower() for keyword in phone_keywords):
            confidence += 0.05

        return min(1.0, confidence)

    def _calculate_website_confidence(
        self, text: str, pattern_type: str
    ) -> float:
        """Calculate confidence score for detected website."""
        confidence = 0.85  # Base confidence

        if pattern_type == "full_url":
            confidence = 0.95
        elif pattern_type == "www_url":
            confidence = 0.93
        elif pattern_type == "domain_only":
            confidence = 0.85

        # Common receipt domains get higher confidence
        common_domains = ["com", "net", "org", "store", "shop"]
        if any(f".{domain}" in text.lower() for domain in common_domains):
            confidence += 0.05

        return min(1.0, confidence)
