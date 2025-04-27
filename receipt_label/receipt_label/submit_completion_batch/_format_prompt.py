import os
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
        "name": "validate_label",
        "description": (
            "Decide whether a token's proposed label is "
            "correct, and if not, suggest the correct label "
            "with a brief rationale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "is_valid": {
                    "type": "boolean",
                    "description": (
                        "True if the proposed label is "
                        "correct for the token, else False."
                    ),
                },
                "correct_label": {
                    "type": "string",
                    "description": (
                        "Only return if is_valid is false and "
                        "you are confident the word should be "
                        "labeled with one of the allowed labels. "
                        "Do not return this if unsure."
                    ),
                    "enum": list(CORE_LABELS.keys()),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Only return if is_valid is false and "
                        "you are confident in the correct label. "
                        "Explain briefly why the word fits the "
                        "suggested label."
                    ),
                },
            },
            "required": ["is_valid"],
        },
    }
]


def _make_tagged_example(
    word: ReceiptWord, label: ReceiptWordLabel, window=2
) -> str:
    """Return a short, receipt-style string with <LABEL>…</LABEL> around the word."""
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
    word: ReceiptWord,
    label: ReceiptWordLabel,
    lines: list[ReceiptLine],
    metadata: ReceiptMetadata,
) -> str:
    prompt_lines: list[str] = []
    prompt_lines.append(
        f"You are confirming the label for the word: {word.text} on the "
        f'"{metadata.merchant_name}" receipt'
    )
    prompt_lines.append(
        f'This "{metadata.merchant_name}" location is located at {metadata.address}'
    )
    prompt_lines.append(f"I've marked the word with <TARGET>...</TARGET>")
    prompt_lines.append(f"The receipt is as follows:")
    prompt_lines.append(f"--------------------------------")
    prompt_lines.append(_prompt_receipt_text(word, lines))
    prompt_lines.append(f"--------------------------------")
    prompt_lines.append(f"The label you are confirming is: {label.label}")
    # ----- Allowed labels glossary -----------------------------------
    # allowed_labels_glossary = "\n".join(
    #     f"- {lbl}: {desc}" for lbl, desc in CORE_LABELS.items()
    # )
    # prompt_lines.append("\nAllowed labels:\n" + allowed_labels_glossary + "\n")
    prompt_lines.append("Allowed labels: " + ", ".join(CORE_LABELS.keys()))
    prompt_lines.append(f"If the label is correct, return is_valid = true.")
    prompt_lines.append(
        f"If it is incorrect and you are confident in a better label from the allowed list, return is_valid = false and return correct_label and rationale."
    )
    prompt_lines.append(
        f"If you are not confident in any better label from the allowed list, return is_valid = false only. Do not guess. Leave correct_label and rationale blank."
    )
    return "\n".join(prompt_lines)


def _format_second_pass_prompt(
    word: ReceiptWord,
    invalid_label: ReceiptWordLabel,
    similar_words: list[ReceiptWord],
    similar_labels: list[ReceiptWordLabel],
    metadata: ReceiptMetadata,
) -> str:
    # ---- Build the prompt in a single string --------------------
    prompt_lines: list[str] = []

    prompt_lines.append("### Task")
    prompt_lines.append("You are re-validating a single label on a receipt.")
    prompt_lines.append(
        "Return **only** a JSON object in one of the following formats:"
    )
    prompt_lines.append("1. If the proposed label is correct:")
    prompt_lines.append('    {"is_valid": true}')
    prompt_lines.append("2. If the proposed label is incorrect:")
    prompt_lines.append(
        '    {"is_valid": false, "correct_label": "<ONE_OF_CORE_LABELS>", "rationale": "<BRIEF_RATIONALE>"}'
    )
    prompt_lines.append("")  # blank line
    prompt_lines.append("No additional keys, no prose.")
    prompt_lines.append("")  # blank line

    # --- Core labels ------------------------------------------------
    prompt_lines.append("### Core Labels")
    prompt_lines.append(
        "\n".join(f"- {lbl}: {desc}" for lbl, desc in CORE_LABELS.items())
    )
    prompt_lines.append("")  # blank line

    # --- Prior attempts --------------------------------------------
    prompt_lines.append("### Prior Attempts")
    prompt_lines.append(
        f"The previous model proposed **{invalid_label.label}** and was then marked **{invalid_label.validation_status}**."
    )

    # --- Current attempt -------------------------------------------
    prompt_lines.append("### Current Attempt")
    prompt_lines.append(f"<TARGET>{word.text}</TARGET>")
    prompt_lines.append("")  # blank line

    # --- Few‑shot examples -----------------------------------------
    prompt_lines.append("### Examples (already validated)")
    EXAMPLE_CAP = 4
    seen: set[str] = set()
    for word_ex, label_ex in zip(similar_words, similar_labels):
        token_key = word_ex.text.lower()
        if token_key in seen:
            continue
        seen.add(token_key)
        example = _make_tagged_example(word_ex, label_ex)
        prompt_lines.append(example)
        prompt_lines.append("---")
        if len(seen) >= EXAMPLE_CAP:
            break
    prompt_lines.append("")  # blank line
    # --- Receipt context -------------------------------------------
    prompt_lines.append("### Receipt Context")
    prompt_lines.append(f"This is from a {metadata.merchant_name} receipt:")
    if metadata.address:
        prompt_lines.append(f"{metadata.address}")
    if metadata.phone_number:
        prompt_lines.append(f"{metadata.phone_number}")
    prompt_lines.append("---")
    prompt_lines.append(
        _prompt_receipt_text(
            word,
            dynamo_client.getReceiptLinesByIndices(
                indices=[
                    (invalid_label.image_id, invalid_label.receipt_id, line_id)
                    for line_id in range(
                        invalid_label.line_id - LINE_WINDOW,
                        invalid_label.line_id + LINE_WINDOW + 1,
                    )
                    if line_id >= 0
                ]
            ),
        )
    )
    prompt_lines.append("---")
    prompt_lines.append("")  # final blank line
    # --- Similar words --------------------------------------------
    vector_id = (
        f"IMAGE#{invalid_label.image_id}#"
        f"RECEIPT#{invalid_label.receipt_id:05d}#"
        f"LINE#{invalid_label.line_id:05d}#"
        f"WORD#{invalid_label.word_id:05d}"
    )
    fetch_response = pinecone_index.fetch(ids=[vector_id], namespace="words")
    if not fetch_response.vectors:
        raise ValueError(f"Vector {vector_id} not found in Pinecone.")
    query_vector = list(fetch_response.vectors.values())[0].values

    # metadata_filter = {"valid_labels": {"$exists": True}}
    metadata_filter = {"valid_labels": {"$in": [invalid_label.label]}}

    search_response = pinecone_index.query(
        vector=query_vector,
        top_k=10,
        filter=metadata_filter,
        namespace="words",  # change if you use a different namespace
        include_metadata=True,  # so we can inspect the labels
    )

    # Prune out any context examples with OOV or already-invalid labels
    labels, _ = dynamo_client.getReceiptWordLabelsForWord(
        image_id=invalid_label.image_id,
        receipt_id=invalid_label.receipt_id,
        line_id=invalid_label.line_id,
        word_id=invalid_label.word_id,
    )
    invalid_labels_for_word = [
        l.label
        for l in labels
        if l.validation_status == ValidationStatus.INVALID.value
    ]
    prompt_lines.append(f"### Similar words")
    # Filter matches to only those sharing the target label and excluding invalid or OOV labels
    filtered_matches = []
    for vector in search_response.matches:
        valid_labels = vector.metadata.get("valid_labels", [])
        # keep only examples that include the target label and exclude any already-invalid or non-core labels
        if invalid_label.label not in valid_labels:
            continue
        if any(lbl in invalid_labels_for_word for lbl in valid_labels):
            continue
        if not all(lbl in CORE_LABELS for lbl in valid_labels):
            continue
        filtered_matches.append(vector)

    for vector in filtered_matches:
        metadata = vector.metadata
        text = metadata.get("text", "").strip()
        merchant_name = metadata.get("merchant_name", "").strip()
        valid_labels = metadata.get("valid_labels", [])
        prompt_lines.append(
            f" - score={vector.score:.4f} {text} ({merchant_name}) {valid_labels}"
        )
    prompt_lines.append("")  # blank line
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)
