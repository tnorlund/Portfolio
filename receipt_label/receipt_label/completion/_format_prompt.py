import json
import os

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_label.constants import CORE_LABELS
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

# A mini JSON schema snippet for validate_labels
VALIDATE_LABELS_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "is_valid": {"type": "boolean"},
                    "correct_label": {
                        "type": "string",
                        "enum": list(CORE_LABELS.keys()),
                    },
                },
                "required": ["id", "is_valid"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
}
EXAMPLE_CAP = int(os.getenv("EXAMPLE_CAP", "4"))
LINE_WINDOW = int(os.getenv("LINE_WINDOW", "5"))

functions = [
    {
        "name": "validate_labels",
        "description": "Validate multiple receipt-word labels in one batch.",
        "parameters": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": (
                                    "The original label identifier"
                                ),
                            },
                            "is_valid": {
                                "type": "boolean",
                                "description": (
                                    "True if the proposed label is correct"
                                ),
                            },
                            "correct_label": {
                                "type": "string",
                                "description": (
                                    "If invalid, the suggested correct label"
                                ),
                                "enum": list(CORE_LABELS.keys()),
                            },
                        },
                        "required": [
                            "id",
                            "is_valid",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
        },
    }
]


def _make_tagged_example(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    window=2,
    client_manager: ClientManager = None,
) -> str:
    """
    Return a short, receipt-style string with <LABEL>…</LABEL> around the
    word.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    # Fetch ±window lines around the word
    lines = client_manager.dynamo.getReceiptLinesByIndices(
        indices=[
            (word.image_id, word.receipt_id, lid)
            for lid in range(word.line_id - window, word.line_id + window + 1)
            if lid >= 1
        ]
    )
    snippet = _prompt_receipt_text(word, lines).strip()
    # Wrap the target token
    tagged = snippet.replace(
        f"<TARGET>{word.text}</TARGET>",
        f"<{label.label}>{word.text}</{label.label}>",
        1,
    )
    return tagged


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


def _prompt_receipt_text(word: ReceiptWord, lines: list[ReceiptLine]) -> str:
    """Format the receipt text for the prompt."""
    if word.line_id == lines[0].line_id:
        prompt_receipt = lines[0].text
        prompt_receipt = prompt_receipt.replace(
            word.text, f"<TARGET>{word.text}</TARGET>"
        )
    else:
        prompt_receipt = lines[0].text

    for index in range(1, len(lines)):
        previous_line = lines[index - 1]
        current_line = lines[index]
        if current_line.line_id == word.line_id:
            # Replace the word in the line text with <TARGET>text</TARGET>
            line_text = current_line.text
            line_text = line_text.replace(
                word.text, f"<TARGET>{word.text}</TARGET>"
            )
        else:
            line_text = current_line.text
        current_line_centroid = current_line.calculate_centroid()
        if (
            current_line_centroid[1] < previous_line.top_left["y"]
            and current_line_centroid[1] > previous_line.bottom_left["y"]
        ):
            prompt_receipt += f" {line_text}"
        else:
            prompt_receipt += f"\n{line_text}"
    return prompt_receipt


def _format_first_pass_prompt(
    words: list[ReceiptWord],
    labels: list[ReceiptWordLabel],
    lines: list[ReceiptLine],
    metadata: ReceiptMetadata,
) -> str:
    prompt_lines: list[str] = []
    # Assemble the targets
    targets: list[dict] = []
    for label in labels:
        word = next(
            w
            for w in words
            if w.line_id == label.line_id and w.word_id == label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {label}")
        targets.append(
            {
                "id": (
                    f"IMAGE#{label.image_id}#"
                    f"RECEIPT#{label.receipt_id:05d}#"
                    f"LINE#{label.line_id:05d}#"
                    f"WORD#{label.word_id:05d}#"
                    f"LABEL#{label.label}#"
                    f"VALIDATION_STATUS#{label.validation_status}"
                ),
                "text": word.text,
                "line_id": label.line_id,
                "proposed_label": label.label,
            }
        )

    prompt_lines.append("### Schema")
    prompt_lines.append("```json")
    prompt_lines.append(json.dumps(VALIDATE_LABELS_SCHEMA, indent=2))
    prompt_lines.append("```")
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Task")

    prompt_lines.append(
        f'Validate these labels on one "{metadata.merchant_name}" receipt. '
        "Return **only** a call to the **`validate_labels`** function with a "
        "`results` array, and do NOT renumber the `id` field.\n"
    )
    prompt_lines.append("Each result object must include:")
    prompt_lines.append('- `"id"`: the original label identifier')
    prompt_lines.append('- `"is_valid"`: true or false')
    prompt_lines.append('- `"correct_label"`: (only if `"is_valid": false`)')
    prompt_lines.append("### Examples (for guidance only)")
    prompt_lines.append(
        'SPROUTS → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#'
        '00003#WORD#00001#LABEL#MERCHANT_NAME#VALIDATION_STATUS#NONE",'
        '"is_valid":true}]}'
    )
    prompt_lines.append(
        '4.99 → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#00010#'
        'WORD#00002#LABEL#LINE_TOTAL#VALIDATION_STATUS#NONE","is_valid":'
        'false,"correct_label":"UNIT_PRICE"}]}'
    )
    prompt_lines.append("### Allowed labels")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append(
        "Only labels from the above list are valid; do NOT propose any other "
        "label."
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(targets))
    prompt_lines.append("\n")

    prompt_lines.append("### Receipt")
    prompt_lines.append(
        "Below is the receipt for context. Each line determines the range of "
        "line IDs\n"
    )
    prompt_lines.append("---")
    prompt_lines.append(_format_receipt_lines(lines))
    prompt_lines.append("---")
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)


def _format_prompt(  # pylint: disable=too-many-positional-arguments
    first_pass_labels: list[ReceiptWordLabel],
    second_pass_labels: list[ReceiptWordLabel],
    words: list[ReceiptWord],
    lines: list[ReceiptLine],
    all_labels: list[ReceiptWordLabel],
    metadata: ReceiptMetadata,
) -> str:
    """
    Format the prompt for the completion batch.
    """
    prompt_lines: list[str] = []
    # Assemble the targets
    targets: list[dict] = []
    for first_pass_label in first_pass_labels:
        word = next(
            w
            for w in words
            if w.line_id == first_pass_label.line_id
            and w.word_id == first_pass_label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {first_pass_label}")
        targets.append(
            {
                "id": (
                    f"IMAGE#{first_pass_label.image_id}#"
                    f"RECEIPT#{first_pass_label.receipt_id:05d}#"
                    f"LINE#{first_pass_label.line_id:05d}#"
                    f"WORD#{first_pass_label.word_id:05d}#"
                    f"LABEL#{first_pass_label.label}#"
                    f"VALIDATION_STATUS#{first_pass_label.validation_status}"
                ),
                "text": word.text,
                "line_id": first_pass_label.line_id,
                "proposed_label": first_pass_label.label,
            }
        )
    for second_pass_label in second_pass_labels:
        word = next(
            w
            for w in words
            if w.line_id == second_pass_label.line_id
            and w.word_id == second_pass_label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {second_pass_label}")
        target = {
            "id": (
                f"IMAGE#{second_pass_label.image_id}#"
                f"RECEIPT#{second_pass_label.receipt_id:05d}#"
                f"LINE#{second_pass_label.line_id:05d}#"
                f"WORD#{second_pass_label.word_id:05d}#"
                f"LABEL#{second_pass_label.label}#"
                f"VALIDATION_STATUS#{second_pass_label.validation_status}"
            ),
            "text": word.text,
            "line_id": second_pass_label.line_id,
            "proposed_label": second_pass_label.label,
            "invalid_labels": [
                label.label
                for label in all_labels
                if label.line_id == second_pass_label.line_id
                and label.word_id == second_pass_label.word_id
                and label.validation_status == ValidationStatus.INVALID.value
            ],
        }
        targets.append(target)

    # Sort targets by line_id
    targets.sort(key=lambda x: (x["line_id"]))

    # Task and schema
    prompt_lines.append("### Schema")
    prompt_lines.append("```json")
    prompt_lines.append(json.dumps(VALIDATE_LABELS_SCHEMA, indent=2))
    prompt_lines.append("```")
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Task")
    prompt_lines.append(
        f'Revalidate these labels on one "{metadata.merchant_name}" receipt. '
        "Return **only** a call to the **`validate_labels`** function with a "
        "`results` array, and do NOT renumber the `id` field.\n"
    )
    prompt_lines.append("Each result object must include:")
    prompt_lines.append('- `"id"`: the original label identifier')
    prompt_lines.append('- `"is_valid"`: true or false')
    prompt_lines.append('- `"correct_label"`: (only if `"is_valid": false`)')
    prompt_lines.append("### Examples (for guidance only)")
    prompt_lines.append(
        'SPROUTS → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#'
        '00003#WORD#00001#LABEL#MERCHANT_NAME#VALIDATION_STATUS#NONE",'
        '"is_valid":true}]}'
    )
    prompt_lines.append(
        '4.99 → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#00010#'
        'WORD#00002#LABEL#LINE_TOTAL#VALIDATION_STATUS#NONE","is_valid":'
        'false,"correct_label":"UNIT_PRICE"}]}'
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Allowed labels")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append(
        "Only labels from the above list are valid; do NOT propose or accept "
        "any other label."
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(targets))
    prompt_lines.append("\n")

    # Receipt context once
    prompt_lines.append("### Receipt")
    prompt_lines.append(
        "Below is the receipt for context. Each line determines the range of "
        "line IDs\n"
    )
    prompt_lines.append("---")
    prompt_lines.append(_format_receipt_lines(lines))
    prompt_lines.append("---")
    prompt_lines.append("")  # blank

    # Final guardrail
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)


def _format_second_pass_prompt(
    second_pass_labels: list[ReceiptWordLabel],
    words: list[ReceiptWord],
    lines: list[ReceiptLine],
    metadata: ReceiptMetadata,
    all_labels: list[ReceiptWordLabel],
) -> str:
    prompt_lines: list[str] = []
    targets: list[dict] = []
    for second_pass_label in second_pass_labels:
        word = next(
            w
            for w in words
            if w.line_id == second_pass_label.line_id
            and w.word_id == second_pass_label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {second_pass_label}")
        target = {
            "id": (
                f"IMAGE#{second_pass_label.image_id}#"
                f"RECEIPT#{second_pass_label.receipt_id:05d}#"
                f"LINE#{second_pass_label.line_id:05d}#"
                f"WORD#{second_pass_label.word_id:05d}#"
                f"LABEL#{second_pass_label.label}#"
                f"VALIDATION_STATUS#{second_pass_label.validation_status}"
            ),
            "text": word.text,
            "line_id": second_pass_label.line_id,
            "proposed_label": second_pass_label.label,
            "invalid_labels": [
                label.label
                for label in all_labels
                if label.line_id == second_pass_label.line_id
                and label.word_id == second_pass_label.word_id
                and label.validation_status == ValidationStatus.INVALID.value
            ],
        }
        targets.append(target)

    # Task and schema
    prompt_lines.append("### Schema")
    prompt_lines.append("```json")
    prompt_lines.append(json.dumps(VALIDATE_LABELS_SCHEMA, indent=2))
    prompt_lines.append("```")
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Task")
    prompt_lines.append(
        f'Revalidate these labels on one "{metadata.merchant_name}" receipt. '
        "Return **only** a call to the **`validate_labels`** function with a "
        "`results` array, and do NOT renumber the `id` field.\n"
    )
    prompt_lines.append("Each result object must include:")
    prompt_lines.append('- `"id"`: the original label identifier')
    prompt_lines.append('- `"is_valid"`: true or false')
    prompt_lines.append('- `"correct_label"`: (only if `"is_valid": false`)')
    prompt_lines.append("### Examples (for guidance only)")
    prompt_lines.append(
        'SPROUTS → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#'
        '00003#WORD#00001#LABEL#MERCHANT_NAME#VALIDATION_STATUS#NONE",'
        '"is_valid":true}]}'
    )
    prompt_lines.append(
        '4.99 → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#00010#'
        'WORD#00002#LABEL#LINE_TOTAL#VALIDATION_STATUS#NONE","is_valid":'
        'false,"correct_label":"UNIT_PRICE"}]}'
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Allowed labels")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append(
        "Only labels from the above list are valid; do NOT propose or accept "
        "any other label."
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(targets))
    prompt_lines.append("\n")

    # Receipt context once
    prompt_lines.append("### Receipt")
    prompt_lines.append(
        "Below is the receipt for context. Each line determines the range of "
        "line IDs\n"
    )
    prompt_lines.append("---")
    prompt_lines.append(_format_receipt_lines(lines))
    prompt_lines.append("---")
    prompt_lines.append("")  # blank

    # Final guardrail
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)
