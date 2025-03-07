from typing import Dict, List, Optional
import logging
from ..models.receipt import ReceiptWord, ReceiptSection

logger = logging.getLogger(__name__)


class StructureProcessor:
    """Handles non-GPT structure analysis for receipts."""

    def __init__(self):
        """Initialize the structure processor."""
        self.section_patterns = {
            "header": {
                "keywords": ["receipt", "invoice", "bill", "check"],
                "position": "top",
                "max_lines": 5,
            },
            "business_info": {
                "keywords": ["address", "phone", "fax", "email", "www", "http"],
                "position": "top",
                "max_lines": 10,
            },
            "items": {
                "keywords": [
                    "item",
                    "description",
                    "qty",
                    "quantity",
                    "price",
                    "amount",
                ],
                "position": "middle",
                "min_lines": 3,
            },
            "totals": {
                "keywords": ["subtotal", "tax", "total", "amount due", "balance"],
                "position": "bottom",
                "max_lines": 5,
            },
            "footer": {
                "keywords": ["thank you", "thanks", "visit again", "receipt"],
                "position": "bottom",
                "max_lines": 3,
            },
        }

    def analyze_structure(
        self, receipt_words: List[ReceiptWord], receipt_lines: List[Dict]
    ) -> Dict:
        """Analyze receipt structure using pattern matching.

        Args:
            receipt_words: List of receipt words
            receipt_lines: List of receipt lines

        Returns:
            Dict containing structure analysis results
        """
        # Group words by line
        words_by_line = {}
        for word in receipt_words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)

        # Analyze each section
        sections = []
        total_lines = len(receipt_lines)

        for section_name, pattern in self.section_patterns.items():
            section = self._analyze_section(
                section_name, pattern, words_by_line, total_lines
            )
            if section:
                sections.append(section)

        # Calculate overall confidence
        overall_confidence = (
            sum(section.confidence for section in sections) / len(sections)
            if sections
            else 0.0
        )

        return {
            "discovered_sections": [
                {
                    "name": section.name,
                    "confidence": section.confidence,
                    "line_ids": section.line_ids,
                    "spatial_patterns": section.spatial_patterns,
                    "content_patterns": section.content_patterns,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "metadata": section.metadata,
                }
                for section in sections
            ],
            "overall_confidence": overall_confidence,
        }

    def _analyze_section(
        self,
        section_name: str,
        pattern: Dict,
        words_by_line: Dict[int, List[ReceiptWord]],
        total_lines: int,
    ) -> Optional[ReceiptSection]:
        """Analyze a specific section of the receipt.

        Args:
            section_name: Name of the section to analyze
            pattern: Pattern to match against
            words_by_line: Words grouped by line number
            total_lines: Total number of lines in receipt

        Returns:
            ReceiptSection if found, None otherwise
        """
        # Determine line range based on position
        if pattern["position"] == "top":
            start_line = 0
            end_line = min(pattern.get("max_lines", 5), total_lines)
        elif pattern["position"] == "bottom":
            end_line = total_lines
            start_line = max(0, end_line - pattern.get("max_lines", 5))
        else:  # middle
            start_line = max(0, total_lines // 4)
            end_line = min(total_lines, start_line + pattern.get("min_lines", 3))

        # Find matching lines
        matching_lines = []
        spatial_patterns = []
        content_patterns = []

        for line_id in range(start_line, end_line):
            if line_id not in words_by_line:
                continue

            line_words = words_by_line[line_id]
            line_text = " ".join(word.text.lower() for word in line_words)

            # Check for keyword matches
            if any(keyword in line_text for keyword in pattern["keywords"]):
                matching_lines.append(line_id)
                content_patterns.append(line_text)

                # Analyze spatial patterns
                if len(line_words) > 0:
                    first_word = line_words[0]
                    last_word = line_words[-1]

                    # Check for indentation
                    if first_word.bounding_box and last_word.bounding_box:
                        left_margin = first_word.bounding_box.get("left", 0)
                        right_margin = last_word.bounding_box.get("right", 0)

                        if left_margin > 50:  # Indented
                            spatial_patterns.append("indented")
                        elif right_margin < 500:  # Right-aligned
                            spatial_patterns.append("right_aligned")
                        else:  # Full width
                            spatial_patterns.append("full_width")

        if not matching_lines:
            return None

        # Calculate confidence based on matches
        confidence = len(matching_lines) / (end_line - start_line)

        return ReceiptSection(
            name=section_name,
            confidence=confidence,
            line_ids=matching_lines,
            spatial_patterns=list(set(spatial_patterns)),
            content_patterns=list(set(content_patterns)),
            start_line=min(matching_lines),
            end_line=max(matching_lines),
            metadata={
                "position": pattern["position"],
                "keywords": pattern["keywords"],
            },
        )
