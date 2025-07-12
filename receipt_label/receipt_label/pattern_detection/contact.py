"""Contact information pattern detection for receipts."""

import re
from typing import Dict, List, Optional

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)

# Common top-level domains for website detection
COMMON_TLDS = [
    "com",
    "org",
    "net",
    "edu",
    "gov",
    "mil",
    "int",
    "co",
    "io",
    "ai",
    "app",
    "dev",
    "tech",
    "store",
    "shop",
    "biz",
    "info",
    "name",
    "mobi",
    "tv",
    "uk",
    "us",
    "ca",
    "au",
    "de",
    "fr",
    "jp",
    "cn",
    "in",
    "br",
    "ru",
    "eu",
    "asia",
    "africa",
]


class ContactPatternDetector(PatternDetector):
    """Detects phone numbers, emails, and websites in receipt text."""

    def _initialize_patterns(self) -> None:
        """Compile regex patterns for contact information detection."""
        self._compiled_patterns = {
            # Phone number patterns
            # US/Canada: (555) 123-4567, 555-123-4567, 555.123.4567, 5551234567
            "phone_us": re.compile(
                r"(?:\+?1[-.\s]?)?"  # Optional country code
                r"(?:\(?\d{3}\)?[-.\s]?)"  # Area code
                r"(?:\d{3}[-.\s]?)"  # First 3 digits
                r"(?:\d{4})"  # Last 4 digits
            ),
            # International: +44 20 1234 5678, +33 1 23 45 67 89
            "phone_intl": re.compile(
                r"\+\d{1,3}[-.\s]?"  # Country code
                r"(?:\d{1,4}[-.\s]?)+"  # Groups of digits
                r"\d{2,4}"  # Final group
            ),
            # Generic phone pattern
            "phone_generic": re.compile(
                r"(?:tel:|phone:|ph:|t:|p:)?\s*"  # Optional prefix
                r"[\d\s\-\(\)\.+]{10,20}"  # Digits with separators
            ),
            # Email patterns
            "email": re.compile(
                r"[a-zA-Z0-9._%+-]+"  # Local part
                r"@"  # At symbol
                r"[a-zA-Z0-9.-]+"  # Domain
                r"\."  # Dot
                r"[a-zA-Z]{2,}"  # TLD
            ),
            # Website patterns
            # With protocol: http://example.com, https://example.com
            "website_protocol": re.compile(
                r"https?://"  # Protocol
                r"(?:www\.)?"  # Optional www
                r"[a-zA-Z0-9-]+"  # Domain
                r"(?:\.[a-zA-Z0-9-]+)*"  # Subdomains
                r"\.[a-zA-Z]{2,}"  # TLD
                r"(?:/[^\s]*)?"  # Optional path
            ),
            # Without protocol: www.example.com, example.com
            "website_no_protocol": re.compile(
                r"(?:www\.)?"  # Optional www
                r"[a-zA-Z0-9-]+"  # Domain
                r"(?:\.[a-zA-Z0-9-]+)*"  # Subdomains
                r"\."  # Dot
                r"(?:" + "|".join(COMMON_TLDS) + ")"  # Known TLDs
                r"(?:/[^\s]*)?"  # Optional path
            ),
            # Short URLs: bit.ly/abc123
            "website_short": re.compile(
                r"(?:bit\.ly|goo\.gl|tinyurl\.com|t\.co)"  # Short URL services
                r"/[a-zA-Z0-9]+"  # Path
            ),
        }

    async def detect(self, words: List[ReceiptWord]) -> List[PatternMatch]:
        """Detect contact patterns in receipt words."""
        matches = []

        for word in words:
            if word.is_noise:
                continue

            # Phone number detection
            phone_match = self._match_phone_pattern(word.text)
            if phone_match:
                match = PatternMatch(
                    word=word,
                    pattern_type=PatternType.PHONE_NUMBER,
                    confidence=phone_match["confidence"],
                    matched_text=phone_match["matched_text"],
                    extracted_value=phone_match["normalized"],
                    metadata={
                        "format": phone_match["format"],
                        "country": phone_match.get("country", "US"),
                        **self._calculate_position_context(word, words),
                    },
                )
                matches.append(match)

            # Email detection
            email_match = self._match_email_pattern(word.text)
            if email_match:
                match = PatternMatch(
                    word=word,
                    pattern_type=PatternType.EMAIL,
                    confidence=email_match["confidence"],
                    matched_text=email_match["matched_text"],
                    extracted_value=email_match["email"],
                    metadata={
                        "domain": email_match["domain"],
                        **self._calculate_position_context(word, words),
                    },
                )
                matches.append(match)

            # Website detection
            website_match = self._match_website_pattern(word.text)
            if website_match:
                match = PatternMatch(
                    word=word,
                    pattern_type=PatternType.WEBSITE,
                    confidence=website_match["confidence"],
                    matched_text=website_match["matched_text"],
                    extracted_value=website_match["url"],
                    metadata={
                        "has_protocol": website_match["has_protocol"],
                        "domain": website_match["domain"],
                        **self._calculate_position_context(word, words),
                    },
                )
                matches.append(match)

        return matches

    def get_supported_patterns(self) -> List[PatternType]:
        """Get supported pattern types."""
        return [
            PatternType.PHONE_NUMBER,
            PatternType.EMAIL,
            PatternType.WEBSITE,
        ]

    def _match_phone_pattern(self, text: str) -> Optional[Dict]:
        """Match phone number patterns."""
        text = text.strip()

        # Remove common prefixes
        cleaned_text = re.sub(
            r"^(tel:|phone:|ph:|t:|p:)\s*", "", text, flags=re.IGNORECASE
        )

        # Try international format first
        if match := self._compiled_patterns["phone_intl"].search(cleaned_text):
            digits = re.sub(r"[^\d+]", "", match.group(0))
            if 10 <= len(digits) <= 15:  # Valid phone length
                return {
                    "matched_text": match.group(0),
                    "normalized": self._normalize_phone(match.group(0)),
                    "format": "INTERNATIONAL",
                    "country": "INTL",
                    "confidence": 0.9,
                }

        # Try US/Canada format
        if match := self._compiled_patterns["phone_us"].search(cleaned_text):
            digits = re.sub(r"[^\d]", "", match.group(0))
            if len(digits) == 10 or (len(digits) == 11 and digits[0] == "1"):
                return {
                    "matched_text": match.group(0),
                    "normalized": self._normalize_phone(match.group(0)),
                    "format": "US",
                    "country": "US",
                    "confidence": 0.95,
                }

        # Try generic pattern (lower confidence)
        if match := self._compiled_patterns["phone_generic"].search(text):
            digits = re.sub(r"[^\d]", "", match.group(0))
            if 10 <= len(digits) <= 15:
                return {
                    "matched_text": match.group(0),
                    "normalized": self._normalize_phone(match.group(0)),
                    "format": "GENERIC",
                    "confidence": 0.7,
                }

        return None

    def _match_email_pattern(self, text: str) -> Optional[Dict]:
        """Match email patterns."""
        text = text.strip()

        if match := self._compiled_patterns["email"].search(text):
            email = match.group(0).lower()
            domain = email.split("@")[1]

            # Validate email structure
            if self._is_valid_email(email):
                return {
                    "matched_text": match.group(0),
                    "email": email,
                    "domain": domain,
                    "confidence": 0.95,
                }

        return None

    def _match_website_pattern(self, text: str) -> Optional[Dict]:
        """Match website patterns."""
        text = text.strip().lower()

        # Try with protocol
        if match := self._compiled_patterns["website_protocol"].search(text):
            return {
                "matched_text": match.group(0),
                "url": match.group(0),
                "domain": self._extract_domain(match.group(0)),
                "has_protocol": True,
                "confidence": 0.95,
            }

        # Try short URLs
        if match := self._compiled_patterns["website_short"].search(text):
            return {
                "matched_text": match.group(0),
                "url": f"https://{match.group(0)}",
                "domain": match.group(0).split("/")[0],
                "has_protocol": False,
                "confidence": 0.9,
            }

        # Try without protocol
        if match := self._compiled_patterns["website_no_protocol"].search(
            text
        ):
            # Check it's not part of an email
            if "@" not in text[: match.start()]:
                return {
                    "matched_text": match.group(0),
                    "url": f"https://{match.group(0)}",
                    "domain": self._extract_domain(match.group(0)),
                    "has_protocol": False,
                    "confidence": 0.8,
                }

        return None

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to consistent format."""
        # Extract digits
        digits = re.sub(r"[^\d+]", "", phone)

        # Handle international
        if digits.startswith("+"):
            return digits

        # Handle US with country code
        if len(digits) == 11 and digits[0] == "1":
            digits = digits[1:]

        # Format US numbers
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

        return digits

    def _is_valid_email(self, email: str) -> bool:
        """Validate email format."""
        parts = email.split("@")
        if len(parts) != 2:
            return False

        local, domain = parts

        # Basic validation
        if not local or not domain:
            return False

        # Check domain has at least one dot
        if "." not in domain:
            return False

        # Check TLD length
        tld = domain.split(".")[-1]
        if len(tld) < 2:
            return False

        return True

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        # Remove protocol
        domain = re.sub(r"^https?://", "", url)
        # Remove www
        domain = re.sub(r"^www\.", "", domain)
        # Remove path
        domain = domain.split("/")[0]
        return domain
