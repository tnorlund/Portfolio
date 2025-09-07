"""
Receipt text reconstruction from ReceiptLine entities.
Extracted from costco_label_discovery.py to improve modularity.
"""

import re
from typing import List, Dict, Tuple
from receipt_dynamo.entities import ReceiptLine
from receipt_label.langchain.models import ReceiptTextGroup


class ReceiptTextReconstructor:
    """Reconstructs readable receipt text from ReceiptLine entities using proper grouping."""

    def format_receipt_lines_exactly(self, lines: List[ReceiptLine]) -> str:
        """
        Format receipt text by grouping visually contiguous lines and
        prefixing each group with its line ID or ID range.
        """
        if not lines:
            return ""

        # Sort lines by line_id to ensure proper order
        sorted_lines = sorted(lines, key=lambda x: x.line_id)

        # Helper to format ID or ID range
        def format_ids(ids: list[int]) -> str:
            if len(ids) == 1:
                return f"{ids[0]}:"
            return f"{ids[0]}-{ids[-1]}:"

        # Initialize first group
        grouped: list[tuple[list[int], str]] = []
        current_ids = [sorted_lines[0].line_id]
        current_text = sorted_lines[0].text

        for prev_line, curr_line in zip(sorted_lines, sorted_lines[1:]):
            curr_id = curr_line.line_id
            centroid = curr_line.calculate_centroid()
            # Decide if on same visual line as previous
            if (
                prev_line.bottom_left["y"]
                < centroid[1]
                < prev_line.top_left["y"]
            ):
                # Same group: append text
                current_ids.append(curr_id)
                current_text += " " + curr_line.text
            else:
                # Flush previous group
                grouped.append((current_ids, current_text))
                # Start new group
                current_ids = [curr_id]
                current_text = curr_line.text

        # Flush final group
        grouped.append((current_ids, current_text))

        # Build formatted lines
        formatted_lines = [
            f"{format_ids(ids)} {text}" for ids, text in grouped
        ]
        return "\n".join(formatted_lines)

    def reconstruct_receipt(
        self, lines: List[ReceiptLine]
    ) -> Tuple[str, List[ReceiptTextGroup]]:
        """
        Reconstruct receipt text from ReceiptLine entities using _format_receipt_lines approach.
        Groups visually contiguous lines based on Y coordinates.

        Returns:
            Tuple of (formatted_text, list_of_text_groups)
        """
        if not lines:
            return "", []

        # Sort lines by Y position (top to bottom) then by line_id
        sorted_lines = sorted(
            lines, key=lambda l: (-l.calculate_centroid()[1], l.line_id)
        )

        # Group visually contiguous lines using the same logic as _format_receipt_lines
        grouped = self._group_visual_lines(sorted_lines)

        # Helper to format IDs: use range only if strictly consecutive; otherwise comma-separated
        def _format_ids_smart(ids: List[int]) -> str:
            if not ids:
                return ":"
            unique_sorted = sorted(set(ids))
            if len(unique_sorted) == 1:
                return f"{unique_sorted[0]}:"
            is_consecutive = all(
                (b - a) == 1 for a, b in zip(unique_sorted, unique_sorted[1:])
            )
            if is_consecutive:
                return f"{unique_sorted[0]}-{unique_sorted[-1]}:"
            return f"{','.join(str(i) for i in unique_sorted)}:"

        # Build formatted text with smart ID display
        formatted_lines = []
        for ids, text in grouped:
            formatted_lines.append(f"{_format_ids_smart(ids)} {text}")

        formatted_text = "\n".join(formatted_lines)

        # Convert to ReceiptTextGroup objects
        text_groups = []
        for ids, text in grouped:
            # Calculate average Y position for this group
            group_lines = [
                line for line in sorted_lines if line.line_id in ids
            ]
            avg_y = sum(
                line.calculate_centroid()[1] for line in group_lines
            ) / len(group_lines)

            text_groups.append(
                ReceiptTextGroup(line_ids=ids, text=text, centroid_y=avg_y)
            )

        return formatted_text, text_groups

    def _group_visual_lines(
        self, lines: List[ReceiptLine]
    ) -> List[Tuple[List[int], str]]:
        """Group visually contiguous lines based on Y coordinate overlap.

        Important: This function preserves the input order. Callers should
        pre-sort `lines` into visual order (e.g., by descending centroid Y).
        We do NOT re-sort by `line_id` here because those IDs are not
        guaranteed to reflect visual placement and doing so can scramble rows.
        """
        if not lines:
            return []

        # Initialize with the first line; we will keep actual line objects in
        # the current group so we can sort left->right before rendering.
        grouped_pairs: List[Tuple[List[int], str]] = []
        current_group: List[ReceiptLine] = [lines[0]]

        for prev_line, curr_line in zip(lines, lines[1:]):
            _cx, cy = curr_line.calculate_centroid()

            # Same visual row if the current centroid Y falls within the
            # previous line's vertical span. In this coordinate system, higher
            # rows have larger Y values.
            if prev_line.bottom_left["y"] < cy < prev_line.top_left["y"]:
                current_group.append(curr_line)
            else:
                # Flush the completed group, sorting tokens left->right
                current_group_sorted = sorted(
                    current_group,
                    key=lambda l: l.calculate_centroid()[0],
                )
                ids = [l.line_id for l in current_group_sorted]
                text = " ".join(l.text for l in current_group_sorted)
                grouped_pairs.append((ids, text))

                # Start a new group
                current_group = [curr_line]

        # Flush the final group
        current_group_sorted = sorted(
            current_group, key=lambda l: l.calculate_centroid()[0]
        )
        ids = [l.line_id for l in current_group_sorted]
        text = " ".join(l.text for l in current_group_sorted)
        grouped_pairs.append((ids, text))

        return grouped_pairs

    def add_spatial_markers(
        self, formatted_text: str, text_groups: List[ReceiptTextGroup]
    ) -> str:
        """Add spatial position markers to help LLM understand receipt layout."""
        if not text_groups:
            return formatted_text

        # Calculate spatial regions
        min_y = min(group.centroid_y for group in text_groups)
        max_y = max(group.centroid_y for group in text_groups)
        y_range = max_y - min_y if max_y > min_y else 1.0

        # Add markers at key positions
        text_lines = formatted_text.split("\n")
        enhanced_lines = []

        for text_line, group in zip(text_lines, text_groups):
            # Calculate relative position (0.0 = top, 1.0 = bottom)
            relative_pos = (
                (group.centroid_y - min_y) / y_range if y_range > 0 else 0.0
            )

            # Add spatial markers
            if relative_pos <= 0.2:
                marker = "📄 TOP"
            elif relative_pos >= 0.8:
                marker = "📄 BOTTOM"
            elif 0.4 <= relative_pos <= 0.6:
                marker = "📄 MIDDLE"
            else:
                marker = ""

            if marker:
                enhanced_lines.append(f"{text_line} {marker}")
            else:
                enhanced_lines.append(text_line)

        return "\n".join(enhanced_lines)

    def extract_currency_context(
        self, text_groups: List[ReceiptTextGroup]
    ) -> List[Dict]:
        """Extract all currency amounts with their context from grouped text."""

        currency_pattern = re.compile(r"\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
        currency_contexts = []

        for group_idx, group in enumerate(text_groups):
            matches = currency_pattern.finditer(group.text)

            for match in matches:
                try:
                    # Extract numeric value
                    value_text = match.group(0)
                    value = float(value_text.replace("$", "").replace(",", ""))

                    # Get context (surrounding text)
                    start = max(0, match.start() - 15)
                    end = min(len(group.text), match.end() + 15)
                    context = group.text[start:end]

                    currency_contexts.append(
                        {
                            "text": value_text,
                            "value": value,
                            "group_number": group_idx + 1,
                            "line_ids": group.line_ids,
                            "context": context,
                            "full_line": group.text,
                            "y_position": group.centroid_y,
                        }
                    )
                except ValueError:
                    continue

        return currency_contexts
