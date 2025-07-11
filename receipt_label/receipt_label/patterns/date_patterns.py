"""
Date pattern detector for receipt labeling.

Detects various date formats commonly found on receipts without using GPT.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Import will be provided by the parent module to avoid circular imports


class DatePatternDetector:
    """Detects date patterns in receipt text."""

    # Label to use for dates (will be set by parent module)
    DATE_LABEL = "DATE"

    # Common date patterns found on receipts
    DATE_PATTERNS = [
        # MM/DD/YYYY or MM-DD-YYYY
        (r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", "mm_dd_yyyy"),
        # DD/MM/YYYY or DD-MM-YYYY (European format)
        (r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", "dd_mm_yyyy"),
        # YYYY-MM-DD or YYYY/MM/DD (ISO format)
        (r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b", "yyyy_mm_dd"),
        # Month DD, YYYY
        (
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
            "month_dd_yyyy",
        ),
        # DD Month YYYY
        (
            r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})\b",
            "dd_month_yyyy",
        ),
        # MM.DD.YYYY or DD.MM.YYYY
        (r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", "dot_separated"),
        # Compact format MMDDYYYY or DDMMYYYY (8 digits)
        (r"\b(\d{8})\b", "compact"),
        # Two-digit year formats MM/DD/YY
        (r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})\b", "mm_dd_yy"),
    ]

    MONTH_NAMES = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "september": 9,
        "sept": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    def detect(self, words: List[Dict]) -> List[Dict]:
        """
        Detect date patterns in receipt words.

        Args:
            words: List of word dictionaries with 'text', 'word_id', etc.

        Returns:
            List of detected patterns with word_id, label, confidence
        """
        detected_patterns = []

        # Check individual words and neighboring words
        for i, word in enumerate(words):
            text = word.get("text", "")

            # Check single word
            date_info = self._check_date_patterns(text)
            if date_info:
                detected_patterns.append(
                    {
                        "word_id": word.get("word_id"),
                        "text": text,
                        "label": self.DATE_LABEL,
                        "confidence": date_info["confidence"],
                        "pattern_type": date_info["pattern_type"],
                        "parsed_date": date_info["parsed_date"],
                    }
                )
                continue

            # Check combined with next word (for split dates like "12 / 25 / 2023")
            if i < len(words) - 2:
                combined_text = f"{text} {words[i+1].get('text', '')} {words[i+2].get('text', '')}"
                date_info = self._check_date_patterns(combined_text)
                if date_info:
                    # Mark all parts as DATE
                    for j in range(3):
                        if i + j < len(words):
                            detected_patterns.append(
                                {
                                    "word_id": words[i + j].get("word_id"),
                                    "text": words[i + j].get("text", ""),
                                    "label": self.DATE_LABEL,
                                    "confidence": date_info["confidence"]
                                    * 0.9,  # Slightly lower for split
                                    "pattern_type": f"{date_info['pattern_type']}_split",
                                    "parsed_date": date_info["parsed_date"],
                                }
                            )

        return detected_patterns

    def _check_date_patterns(self, text: str) -> Optional[Dict]:
        """Check if text matches any date pattern."""
        for pattern, pattern_type in self.DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                parsed_date = self._parse_date(match, pattern_type)
                if parsed_date:
                    return {
                        "pattern_type": pattern_type,
                        "parsed_date": parsed_date,
                        "confidence": self._calculate_confidence(
                            parsed_date, pattern_type
                        ),
                    }
        return None

    def _parse_date(
        self, match: re.Match, pattern_type: str
    ) -> Optional[datetime]:
        """Parse matched date into datetime object."""
        try:
            if pattern_type == "mm_dd_yyyy":
                month, day, year = match.groups()
                year = self._normalize_year(year)
                return datetime(year, int(month), int(day))

            elif pattern_type == "dd_mm_yyyy":
                day, month, year = match.groups()
                year = self._normalize_year(year)
                # Heuristic: if first number > 12, it's definitely day
                if int(day) > 12:
                    return datetime(year, int(month), int(day))
                # Otherwise could be either format
                return datetime(year, int(month), int(day))

            elif pattern_type == "yyyy_mm_dd":
                year, month, day = match.groups()
                return datetime(int(year), int(month), int(day))

            elif pattern_type in ["month_dd_yyyy", "dd_month_yyyy"]:
                groups = match.groups()
                if pattern_type == "month_dd_yyyy":
                    month_str, day, year = groups
                else:
                    day, month_str, year = groups
                month = self.MONTH_NAMES.get(month_str[:3].lower())
                if month:
                    return datetime(int(year), month, int(day))

            elif pattern_type == "dot_separated":
                # Could be MM.DD.YYYY or DD.MM.YYYY
                first, second, year = match.groups()
                year = self._normalize_year(year)
                # Use same heuristic as slash separated
                if int(first) > 12:
                    return datetime(year, int(second), int(first))
                return datetime(year, int(first), int(second))

            elif pattern_type == "compact":
                # Try MMDDYYYY first
                date_str = match.group(1)
                try:
                    return datetime.strptime(date_str, "%m%d%Y")
                except ValueError:
                    # Try DDMMYYYY
                    try:
                        return datetime.strptime(date_str, "%d%m%Y")
                    except ValueError:
                        pass

            elif pattern_type == "mm_dd_yy":
                month, day, year = match.groups()
                year = self._normalize_year(year)
                return datetime(year, int(month), int(day))

        except (ValueError, TypeError):
            # Invalid date
            pass

        return None

    def _normalize_year(self, year_str: str) -> int:
        """Normalize 2-digit years to 4-digit years."""
        year = int(year_str)
        if year < 100:
            # Assume 00-29 is 2000-2029, 30-99 is 1930-1999
            if year < 30:
                year += 2000
            else:
                year += 1900
        return year

    def _calculate_confidence(
        self, parsed_date: datetime, pattern_type: str
    ) -> float:
        """Calculate confidence score for detected date."""
        confidence = 0.85  # Base confidence

        # Higher confidence for unambiguous formats
        if pattern_type in ["yyyy_mm_dd", "month_dd_yyyy", "dd_month_yyyy"]:
            confidence = 0.95

        # Check if date is reasonable (not too far in past or future)
        current_year = datetime.now().year
        if current_year - 5 <= parsed_date.year <= current_year + 1:
            confidence += 0.05
        else:
            confidence -= 0.10

        # Dates in the future are less likely on receipts
        if parsed_date > datetime.now():
            confidence -= 0.15

        return max(0.0, min(1.0, confidence))
