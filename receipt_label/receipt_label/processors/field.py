from typing import Dict, List, Optional
import re
import logging
from datetime import datetime
from ..models.receipt import ReceiptWord

logger = logging.getLogger(__name__)


class FieldProcessor:
    """Handles non-GPT field labeling for receipts."""

    def __init__(self):
        """Initialize the field processor."""
        self.field_patterns = {
            "business_name": {
                "keywords": ["receipt", "invoice", "bill", "check"],
                "position": "top",
                "max_lines": 3,
                "confidence": 0.9,
            },
            "address_line": {
                "patterns": [
                    r"\d+\s+[A-Za-z\s,\.]+(?:Avenue|Ave|Street|St|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Circle|Cir|Way|Place|Pl)",
                    r"P\.?O\.?\s*Box\s+\d+",
                    r"\d+\s+[A-Za-z\s,\.]+(?:Suite|Ste|Apt|Apartment)\s+\d+",
                ],
                "position": "top",
                "max_lines": 5,
                "confidence": 0.8,
            },
            "phone": {
                "patterns": [
                    r"\(?\d{3}\)?[-\.]?\d{3}[-\.]?\d{4}",
                    r"\+\d{1,3}[-\.]?\d{3}[-\.]?\d{3}[-\.]?\d{4}",
                ],
                "position": "top",
                "max_lines": 5,
                "confidence": 0.9,
            },
            "date": {
                "patterns": [
                    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}",
                    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}",
                ],
                "position": "top",
                "max_lines": 5,
                "confidence": 0.9,
            },
            "time": {
                "patterns": [
                    r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?",
                    r"\d{1,2}\s*(?:AM|PM)",
                ],
                "position": "top",
                "max_lines": 5,
                "confidence": 0.9,
            },
            "subtotal": {
                "keywords": ["subtotal", "sub-total", "sub total"],
                "patterns": [r"\$?\s*\d+\.\d{2}"],
                "position": "bottom",
                "max_lines": 5,
                "confidence": 0.8,
            },
            "tax": {
                "keywords": ["tax", "vat", "gst", "hst", "pst"],
                "patterns": [r"\$?\s*\d+\.\d{2}"],
                "position": "bottom",
                "max_lines": 5,
                "confidence": 0.8,
            },
            "total": {
                "keywords": ["total", "amount due", "balance", "sum"],
                "patterns": [r"\$?\s*\d+\.\d{2}"],
                "position": "bottom",
                "max_lines": 5,
                "confidence": 0.9,
            },
        }

    def label_fields(
        self,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[Dict],
        structure_analysis: Dict,
    ) -> Dict:
        """Label receipt fields using pattern matching.

        Args:
            receipt_words: List of receipt words
            receipt_lines: List of receipt lines
            structure_analysis: Structure analysis results

        Returns:
            Dict containing field labeling results
        """
        # Group words by line
        words_by_line = {}
        for word in receipt_words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)

        # Get section boundaries
        sections = {
            section["name"]: section
            for section in structure_analysis["discovered_sections"]
        }

        # Label fields
        labels = []
        total_lines = len(receipt_lines)

        for line_id in range(total_lines):
            if line_id not in words_by_line:
                continue

            line_words = words_by_line[line_id]
            line_text = " ".join(word.text for word in line_words)

            # Determine section context
            section_context = self._get_section_context(line_id, sections)

            # Try to label each word in the line
            for word in line_words:
                field_label = self._label_word(
                    word, line_text, section_context, line_id, total_lines
                )
                if field_label:
                    labels.append(
                        {
                            "line_id": line_id,
                            "word_id": word.word_id,
                            "label": field_label["label"],
                            "confidence": field_label["confidence"],
                            "extracted_data": field_label.get("extracted_data"),
                        }
                    )

        # Calculate average confidence
        avg_confidence = (
            sum(label["confidence"] for label in labels) / len(labels)
            if labels
            else 0.0
        )

        return {
            "labels": labels,
            "metadata": {
                "total_words": len(receipt_words),
                "labeled_words": len(labels),
                "average_confidence": avg_confidence,
            },
        }

    def _get_section_context(self, line_id: int, sections: Dict) -> Optional[str]:
        """Get the section context for a line.

        Args:
            line_id: Line number to get context for
            sections: Dictionary of sections

        Returns:
            Section name if line is in a section, None otherwise
        """
        for section_name, section in sections.items():
            if section["start_line"] <= line_id <= section["end_line"]:
                return section_name
        return None

    def _label_word(
        self,
        word: ReceiptWord,
        line_text: str,
        section_context: Optional[str],
        line_id: int,
        total_lines: int,
    ) -> Optional[Dict]:
        """Label a single word in the receipt.

        Args:
            word: Word to label
            line_text: Full text of the line containing the word
            section_context: Section context for the line
            line_id: Line number
            total_lines: Total number of lines

        Returns:
            Dict containing label and confidence if found, None otherwise
        """
        # Try each field pattern
        for field_name, pattern in self.field_patterns.items():
            # Check position constraints
            if pattern["position"] == "top" and line_id > pattern["max_lines"]:
                continue
            if (
                pattern["position"] == "bottom"
                and line_id < total_lines - pattern["max_lines"]
            ):
                continue

            # Check for keyword matches
            if "keywords" in pattern:
                if any(
                    keyword.lower() in line_text.lower()
                    for keyword in pattern["keywords"]
                ):
                    return {
                        "label": field_name,
                        "confidence": pattern["confidence"],
                    }

            # Check for pattern matches
            if "patterns" in pattern:
                for regex in pattern["patterns"]:
                    if re.search(regex, line_text):
                        # Extract data if applicable
                        extracted_data = None
                        if field_name in ["date", "time"]:
                            extracted_data = self._extract_datetime(
                                line_text, field_name
                            )
                        elif field_name in ["subtotal", "tax", "total"]:
                            extracted_data = self._extract_amount(line_text)

                        return {
                            "label": field_name,
                            "confidence": pattern["confidence"],
                            "extracted_data": extracted_data,
                        }

        return None

    def _extract_datetime(self, text: str, field_type: str) -> Optional[Dict]:
        """Extract date or time from text.

        Args:
            text: Text to extract from
            field_type: Type of field ("date" or "time")

        Returns:
            Dict containing extracted data
        """
        try:
            if field_type == "date":
                # Try multiple date formats
                date_formats = [
                    "%Y-%m-%d",
                    "%m/%d/%Y",
                    "%m/%d/%y",
                    "%B %d, %Y",
                    "%b %d, %Y",
                ]

                for fmt in date_formats:
                    try:
                        date = datetime.strptime(text, fmt)
                        return {
                            "value": date.isoformat(),
                            "format": fmt,
                        }
                    except ValueError:
                        continue

            elif field_type == "time":
                # Try multiple time formats
                time_formats = [
                    "%H:%M:%S",
                    "%H:%M",
                    "%I:%M:%S %p",
                    "%I:%M %p",
                ]

                for fmt in time_formats:
                    try:
                        time = datetime.strptime(text, fmt)
                        return {
                            "value": time.strftime("%H:%M:%S"),
                            "format": fmt,
                        }
                    except ValueError:
                        continue

            return None

        except Exception as e:
            logger.error(f"Error extracting {field_type}: {str(e)}")
            return None

    def _extract_amount(self, text: str) -> Optional[Dict]:
        """Extract monetary amount from text.

        Args:
            text: Text to extract from

        Returns:
            Dict containing extracted amount
        """
        try:
            # Remove currency symbols and whitespace
            cleaned = re.sub(r"[$,]", "", text)

            # Find the last number in the text
            matches = re.findall(r"\d+\.\d{2}", cleaned)
            if matches:
                amount = float(matches[-1])
                return {
                    "value": amount,
                    "currency": "USD",  # TODO: Detect currency
                }

            return None

        except Exception as e:
            logger.error(f"Error extracting amount: {str(e)}")
            return None
