"""Prompt builder for LLM-based label validation."""

import json
from typing import Optional

from receipt_dynamo.entities import ReceiptLine, ReceiptMetadata, ReceiptWord, ReceiptWordLabel

from receipt_label.constants import CORE_LABELS


def _format_receipt_lines(lines: list[ReceiptLine]) -> str:
    """
    Format receipt text by grouping visually contiguous lines and
    prefixing each group with its line ID or ID range.
    """
    if not lines:
        return ""

    # Helper to format ID or ID range
    def format_ids(ids: list[int]) -> str:
        if len(ids) == 1:
            return f"{ids[0]}:"
        return f"{ids[0]}-{ids[-1]}:"

    # Initialize first group
    grouped: list[tuple[list[int], str]] = []
    current_ids = [lines[0].line_id]
    current_text = lines[0].text

    for prev_line, curr_line in zip(lines, lines[1:]):
        curr_id = curr_line.line_id
        centroid = curr_line.calculate_centroid()
        # Decide if on same visual line as previous
        if prev_line.bottom_left["y"] < centroid[1] < prev_line.top_left["y"]:
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
    formatted_lines = [f"{format_ids(ids)} {text}" for ids, text in grouped]
    return "\n".join(formatted_lines)


def _get_nearby_lines(
    lines: list[ReceiptLine], target_line_id: int, window: int = 3
) -> list[ReceiptLine]:
    """Get lines near the target line ID."""
    nearby = []
    for line in lines:
        if abs(line.line_id - target_line_id) <= window:
            nearby.append(line)
    return sorted(nearby, key=lambda x: x.line_id)


def build_validation_prompt(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    lines: list[ReceiptLine],
    receipt_metadata: Optional[ReceiptMetadata] = None,
    chroma_neighbors: Optional[list[dict]] = None,
    all_labels: Optional[list[ReceiptWordLabel]] = None,
    words: Optional[list[ReceiptWord]] = None,
    receipt_count: int = 0,
    label_confidence: Optional[float] = None,
) -> str:
    """
    Build comprehensive prompt for LLM-based label validation.

    Args:
        word: The word being validated
        label: The label to validate
        lines: All receipt lines
        receipt_metadata: ReceiptMetadata if available
        chroma_neighbors: Similar examples from ChromaDB (top 5-10)
        all_labels: All labels on the receipt (for consistency checking)
        words: All words on the receipt (to find other labeled words)
        receipt_count: Number of receipts for this merchant
        label_confidence: Original confidence from labeler

    Returns:
        Formatted prompt string
    """
    prompt_parts = []

    # 1. TASK DEFINITION
    prompt_parts.append("### Task")
    merchant_name = receipt_metadata.merchant_name if receipt_metadata else "a receipt"
    prompt_parts.append(
        f'Validate whether the word "{word.text}" on line {label.line_id} '
        f'should be labeled as "{label.label}" for {merchant_name}.'
    )
    prompt_parts.append("")

    # 2. LABEL DEFINITION
    prompt_parts.append("### Label Definition")
    label_description = CORE_LABELS.get(label.label, "Unknown label")
    prompt_parts.append(f'**{label.label}**: {label_description}')
    prompt_parts.append("")

    # 3. RECEIPT METADATA CONTEXT (if available)
    if receipt_metadata:
        prompt_parts.append("### Merchant Information")
        prompt_parts.append(f"- Merchant Name: {receipt_metadata.merchant_name}")
        if receipt_metadata.address:
            prompt_parts.append(f"- Address: {receipt_metadata.address}")
        if receipt_metadata.phone_number:
            prompt_parts.append(f"- Phone: {receipt_metadata.phone_number}")
        if receipt_metadata.merchant_category:
            prompt_parts.append(f"- Category: {receipt_metadata.merchant_category}")
        if receipt_count > 0:
            prompt_parts.append(f"- Receipts in Database: {receipt_count}")
        prompt_parts.append("")

    # 4. FULL RECEIPT TEXT CONTEXT
    prompt_parts.append("### Full Receipt Text")
    prompt_parts.append(
        "Below is the complete receipt text with line numbers for context:"
    )
    prompt_parts.append("---")
    prompt_parts.append(_format_receipt_lines(lines))
    prompt_parts.append("---")
    prompt_parts.append("")

    # 5. TARGET WORD CONTEXT
    prompt_parts.append("### Target Word to Validate")
    prompt_parts.append(f'- Word: "{word.text}"')
    prompt_parts.append(f'- Line ID: {label.line_id}')
    prompt_parts.append(f'- Proposed Label: {label.label}')
    prompt_parts.append(f'- Current Status: {label.validation_status}')
    if label_confidence is not None:
        prompt_parts.append(f'- Original Confidence: {label_confidence:.2f}')
    if hasattr(label, "reasoning") and label.reasoning:
        prompt_parts.append(f'- Original Reasoning: "{label.reasoning}"')
    prompt_parts.append("")

    # 6. LOCAL CONTEXT (nearby lines)
    prompt_parts.append("### Local Context")
    prompt_parts.append(
        f"Lines near the target word (line {label.line_id}, ±3 lines):"
    )
    nearby_lines = _get_nearby_lines(lines, label.line_id, window=3)
    prompt_parts.append(_format_receipt_lines(nearby_lines))
    prompt_parts.append("")

    # 7. CHROMADB SIMILAR EXAMPLES (if available)
    if chroma_neighbors:
        prompt_parts.append("### Similar Examples from Other Receipts")
        prompt_parts.append(
            f'Here are similar words that were correctly labeled as '
            f'"{label.label}" from other receipts:'
        )
        for i, neighbor in enumerate(chroma_neighbors[:5], 1):  # Top 5 examples
            word_text = neighbor.get("word_text", "N/A")
            merchant = neighbor.get("merchant_name", "N/A")
            similarity = neighbor.get("similarity_score", 0)
            prompt_parts.append(
                f"{i}. Word: \"{word_text}\" | Merchant: {merchant} | "
                f"Similarity: {similarity:.2f}"
            )
        prompt_parts.append("")

    # 8. OTHER LABELS ON RECEIPT (for consistency checking)
    if all_labels and words:
        same_line_labels = [
            l
            for l in all_labels
            if l.line_id == label.line_id
            and l.word_id != label.word_id
            and l.label != label.label
        ]
        if same_line_labels:
            prompt_parts.append("### Other Labels on Same Line")
            for other_label in same_line_labels:
                other_word = next(
                    (w for w in words if w.word_id == other_label.word_id), None
                )
                if other_word:
                    prompt_parts.append(
                        f'- "{other_word.text}" → {other_label.label}'
                    )
            prompt_parts.append("")

        # Show related labels on nearby lines
        nearby_labels = [
            l
            for l in all_labels
            if abs(l.line_id - label.line_id) <= 2
            and l.word_id != label.word_id
            and l.label != label.label
        ]
        if nearby_labels:
            prompt_parts.append("### Nearby Labels (for context)")
            for other_label in nearby_labels[:5]:  # Limit to 5
                other_word = next(
                    (w for w in words if w.word_id == other_label.word_id), None
                )
                if other_word:
                    prompt_parts.append(
                        f'- Line {other_label.line_id}: "{other_word.text}" → '
                        f'{other_label.label}'
                    )
            prompt_parts.append("")

    # 9. VALIDATION INSTRUCTIONS
    prompt_parts.append("### Validation Instructions")
    prompt_parts.append(
        "Determine if the proposed label is correct by considering:\n"
        "1. **Format validation**: Does the word match the expected format for this label type?\n"
        "2. **Context validation**: Does the word make sense in the context of the receipt?\n"
        "3. **Semantic validation**: Is this the most appropriate label for this word?\n"
        "4. **Merchant consistency**: Does this match patterns from similar receipts?\n"
        "5. **Label consistency**: Does this conflict with other labels on the receipt?\n"
    )
    prompt_parts.append("")

    # 10. OUTPUT SCHEMA
    prompt_parts.append("### Output Schema")
    prompt_parts.append("```json")
    schema = {
        "is_valid": "boolean - true if label is correct",
        "confidence": "float 0.0-1.0 - confidence in validation",
        "reasoning": "string - brief explanation",
        "correct_label": "string (optional) - if invalid, the correct label from CORE_LABELS",
        "alternative_labels": "array of strings (optional) - other possible labels",
    }
    prompt_parts.append(json.dumps(schema, indent=2))
    prompt_parts.append("```")
    prompt_parts.append("")

    # 11. ALLOWED LABELS
    prompt_parts.append("### Allowed Labels")
    prompt_parts.append(", ".join(CORE_LABELS.keys()))
    prompt_parts.append("Only labels from the above list are valid.")
    prompt_parts.append("")

    # 12. PATTERN EXAMPLES (for certain label types)
    pattern_examples = _get_pattern_examples(label.label)
    if pattern_examples:
        prompt_parts.append("### Common Patterns")
        prompt_parts.append(pattern_examples)
        prompt_parts.append("")

    prompt_parts.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_parts)


def _get_pattern_examples(label_type: str) -> Optional[str]:
    """Get pattern examples for specific label types."""
    patterns = {
        "DATE": (
            "Common date formats: MM/DD/YYYY (01/15/2024), DD-MM-YY (15-01-24), "
            "YYYY-MM-DD (2024-01-15), Jan 15, 2024, 15 Jan 2024"
        ),
        "TIME": (
            "Common time formats: HH:MM (14:30), HH:MM AM/PM (2:30 PM), "
            "HH:MM:SS (14:30:00)"
        ),
        "PHONE_NUMBER": (
            "Common phone formats: (818) 597-3901, 818-597-3901, "
            "818.597.3901, 8185973901, +1 818 597 3901"
        ),
        "GRAND_TOTAL": (
            "Usually appears near the bottom of receipt, often labeled as "
            "'TOTAL', 'AMOUNT DUE', 'TOTAL DUE'. Should be the final amount "
            "after all taxes and discounts."
        ),
        "UNIT_PRICE": (
            "Price per single unit, often appears before quantity and line total. "
            "May include currency symbol ($4.99) or be plain number (4.99)."
        ),
        "LINE_TOTAL": (
            "Extended price for a line item (quantity × unit price). "
            "Usually appears on the same line as product name."
        ),
        "MERCHANT_NAME": (
            "Usually appears at the top of receipt, often in ALL CAPS or large font. "
            "May include location names or store numbers."
        ),
        "PAYMENT_METHOD": (
            "Payment type appears near bottom: VISA, MASTERCARD, DEBIT, CASH, "
            "APPLE PAY, etc. May include last 4 digits of card."
        ),
    }
    return patterns.get(label_type)


