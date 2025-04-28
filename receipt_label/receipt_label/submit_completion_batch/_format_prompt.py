import os
import json
from receipt_label.utils import get_clients
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptMetadata,
)
from receipt_label.constants import CORE_LABELS


dynamo_client, _, pinecone_index = get_clients()
EXAMPLE_CAP = int(os.getenv("EXAMPLE_CAP", 4))
LINE_WINDOW = int(os.getenv("LINE_WINDOW", 5))

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
                            "text": {
                                "type": "string",
                                "description": "The token text",
                            },
                            "proposed_label": {
                                "type": "string",
                                "description": (
                                    "The current label assigned to the token"
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
                            "invalid_labels": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": list(CORE_LABELS.keys()),
                                },
                                "description": (
                                    "List of labels previously marked invalid "
                                    "for this word"
                                ),
                            },
                        },
                        "required": [
                            "id",
                            "text",
                            "proposed_label",
                            "is_valid",
                            "invalid_labels",
                        ],
                    },
                }
            },
            "required": ["results"],
        },
    }
]


def _make_tagged_example(
    word: ReceiptWord, label: ReceiptWordLabel, window=2
) -> str:
    """
    Return a short, receipt-style string with <LABEL>…</LABEL> around the
    word.
    """
    # Fetch ±window lines around the word
    lines = dynamo_client.getReceiptLinesByIndices(
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
    prompt_receipt = lines[0].text
    for index in range(1, len(lines)):
        previous_line = lines[index - 1]
        current_line = lines[index]
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
                "proposed_label": label.label,
            }
        )

    prompt_lines.append("### Task")

    prompt_lines.append(
        f'Validate these labels on one "{metadata.merchant_name}" receipt. '
        'Return a single function call with a "results" array.\n'
    )
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(targets))
    prompt_lines.append("\n")

    prompt_lines.append("### Receipt")
    prompt_lines.append(_format_receipt_lines(lines))
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

    # Task and schema
    prompt_lines.append("### Task")
    prompt_lines.append("Re-validate these labels on the same receipt.")
    prompt_lines.append(
        "Return **only** a call to the **`validate_labels`** function, "
        "passing your array under the `results` key."
    )
    prompt_lines.append(
        "The function call should have the following signature:"
    )
    prompt_lines.append('- "id": the original label identifier')
    prompt_lines.append('- "text": the token text')
    prompt_lines.append('- "proposed_label": the current label')
    prompt_lines.append(
        '- "invalid_labels": list of labels already marked invalid for this '
        "token"
    )
    prompt_lines.append('- "is_valid": true or false')
    prompt_lines.append('- "correct_label": (only if is_valid is false)')
    prompt_lines.append("")  # blank

    # Examples for format only
    prompt_lines.append("### Examples (for guidance only)")
    prompt_lines.append(
        'SPROUTS → {"results":['
        '{"id":"…",'
        '"text":"SPROUTS",'
        '"proposed_label":"MERCHANT_NAME",'
        '"is_valid":true'
        "}"
        "]"
        "}"
    )
    prompt_lines.append(
        '4.99    → {"results":['
        "{"
        '"id":"…",'
        '"text":"4.99",'
        '"proposed_label":"LINE_TOTAL",'
        '"is_valid":false,'
        '"correct_label":"UNIT_PRICE",'
        '"invalid_labels":["LINE_TOTAL"]'
        "}"
        "]"
        "}"
    )
    prompt_lines.append("")  # blank

    # Allowed labels
    prompt_lines.append("### Allowed labels")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append("")  # blank

    # Build targets array
    targets = []
    for second_pass_label in second_pass_labels:
        # find the matching ReceiptWord
        word = next(
            word
            for word in words
            if word.line_id == second_pass_label.line_id
            and word.word_id == second_pass_label.word_id
        )
        word_labels = [
            label.label
            for label in all_labels
            if label.line_id == second_pass_label.line_id
            and label.word_id == second_pass_label.word_id
            and label.validation_status == ValidationStatus.INVALID.value
        ]
        targets.append(
            {
                "id": (
                    f"IMAGE#{second_pass_label.image_id}#"
                    f"RECEIPT#{second_pass_label.receipt_id:05d}#"
                    f"LINE#{second_pass_label.line_id:05d}#"
                    f"WORD#{second_pass_label.word_id:05d}#"
                    f"LABEL#{second_pass_label.label}#"
                    f"VALIDATION_STATUS#{second_pass_label.validation_status}"
                ),
                "text": word.text,
                "proposed_label": second_pass_label.label,
                "invalid_labels": word_labels,
            }
        )
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(targets))
    prompt_lines.append("")  # blank

    # Receipt context once
    prompt_lines.append("### Receipt")
    prompt_lines.append(_format_receipt_lines(lines))
    prompt_lines.append("")  # blank

    # Final guardrail
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)
