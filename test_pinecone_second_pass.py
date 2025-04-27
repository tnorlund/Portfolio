"""
Example: find the 10 nearest neighbours for a given word *and*
return only those vectors whose metadata already contains a
`valid_labels` key.

Run with:  python test_pinecone_second_pass.py
"""

import json
import random
from dataclasses import asdict, dataclass

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_label.utils import get_clients
from receipt_dynamo.constants import ValidationStatus
from receipt_label.submit_completion_batch.submit_completion import (
    _prompt_receipt_text,
    CORE_LABELS,
)

dynamo_client, openai_client, pinecone_index = get_clients()

LINE_WINDOW = 5


def make_tagged_example(word: ReceiptWord, label: ReceiptWordLabel, window=2) -> str:
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


invalid_labels, _ = dynamo_client.getReceiptWordLabelsByValidationStatus(
    validation_status=ValidationStatus.INVALID.value
    # limit = 100
)

# Randomly select one invalid label
invalid_label = random.choice(invalid_labels)
similar_labels, _ = dynamo_client.getReceiptWordLabelsByLabel(
    label=invalid_label.label,
    limit=100,
)
similar_labels = [
    label
    for label in similar_labels
    if label.validation_status == ValidationStatus.VALID.value
][0:5]
similar_words = dynamo_client.getReceiptWordsByIndices(
    indices=[
        (
            label.image_id,
            label.receipt_id,
            label.line_id,
            label.word_id,
        )
        for label in similar_labels
    ]
)
prompt_receipts = []
for similar_word in similar_words:
    prompt_receipt = _prompt_receipt_text(
        similar_word,
        dynamo_client.getReceiptLinesByIndices(
            indices=[
                (
                    similar_word.image_id,
                    similar_word.receipt_id,
                    line_id,
                )
                for line_id in range(
                    similar_word.line_id - 2,
                    similar_word.line_id + 2 + 1,
                )
                if line_id >= 0
            ]
        ),
    )
    prompt_receipts.append(prompt_receipt)

word = dynamo_client.getReceiptWord(
    image_id=invalid_label.image_id,
    receipt_id=invalid_label.receipt_id,
    line_id=invalid_label.line_id,
    word_id=invalid_label.word_id,
)
labels, _ = dynamo_client.getReceiptWordLabelsForWord(
    image_id=invalid_label.image_id,
    receipt_id=invalid_label.receipt_id,
    line_id=invalid_label.line_id,
    word_id=invalid_label.word_id,
)


metadata = dynamo_client.getReceiptMetadata(
    image_id=invalid_label.image_id,
    receipt_id=invalid_label.receipt_id,
)
# remove the invalid label from the list
labels = [
    label
    for label in labels
    if label.label != invalid_label.label
    # and (
    #     label.line_id != invalid_label.line_id or label.word_id != invalid_label.word_id
    # )
]

# Get +/- 3 lines of context
line_id = invalid_label.line_id
surrounding_lines = dynamo_client.getReceiptLinesByIndices(
    indices=[
        (invalid_label.image_id, invalid_label.receipt_id, line_id)
        for line_id in range(line_id - LINE_WINDOW, line_id + LINE_WINDOW + 1)
        if line_id >= 0
    ]
)

prompt_receipt = _prompt_receipt_text(word, surrounding_lines)


# ---- Build the prompt in a single string --------------------
prompt_lines: list[str] = []

prompt_lines.append("### Task")
prompt_lines.append("You are re-validating a single label on a receipt.")
prompt_lines.append("Return **only** a JSON object in one of the following formats:")
prompt_lines.append("1. If the proposed label is correct:")
prompt_lines.append("    {{'is_valid': true}}")
prompt_lines.append("2. If the proposed label is incorrect:")
prompt_lines.append(
    "    {{'is_valid': false, 'correct_label': \"<ONE_OF_CORE_LABELS>\"}}"
)
prompt_lines.append("")  # blank line
prompt_lines.append("No additional keys, no prose.")
prompt_lines.append("")  # blank line

# --- Core labels ------------------------------------------------
prompt_lines.append("### Core Labels")
prompt_lines.append("\n".join(f"- {lbl}: {desc}" for lbl, desc in CORE_LABELS.items()))
prompt_lines.append("")  # blank line

# --- Prior attempts --------------------------------------------
prompt_lines.append("### Prior Attempts")
prompt_lines.append(
    f"The previous model proposed **{invalid_label.label}** and was then marked **{invalid_label.validation_status}**."
)
for label in labels:
    prompt_lines.append(
        f"The previous model proposed **{label.label}** and was then marked **{label.validation_status}**."
    )
prompt_lines.append("")  # blank line

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
    example = make_tagged_example(word_ex, label_ex)
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
prompt_lines.append(prompt_receipt)
prompt_lines.append("---")
prompt_lines.append("")  # final blank line

# Print the assembled prompt once


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

prompt_lines.append(f"Similar words found: {len(search_response.matches)}")
for vector in search_response.matches:
    metadata = vector.metadata
    text = metadata.get("text", "").strip()
    merchant_name = metadata.get("merchant_name", "").strip()
    valid_labels = metadata.get("valid_labels", [])
    prompt_lines.append(
        f" - score={vector.score:.4f} {text} ({merchant_name}) {valid_labels}"
    )

prompt_string = "\n".join(prompt_lines)

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
receipt, lines, words, letters, tags, labels = dynamo_client.getReceiptDetails(
    image_id=word.image_id,
    receipt_id=word.receipt_id,
)
messages = [
    {"role": "system", "content": prompt_string},
    {"role": "user", "content": "Here is the receipt text:"},
    {
        "role": "user",
        "content": _prompt_receipt_text(
            word,
            lines,
        ),
    },
]

response = openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    functions=functions,
)
print(prompt_string)
print()
print(response.choices[0].message.function_call)
