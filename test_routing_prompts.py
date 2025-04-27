import json

from receipt_dynamo.constants import ValidationStatus
from receipt_label.constants import CORE_LABELS
from receipt_label.submit_completion_batch.submit_completion import (
    chunk_into_completion_batches,
    list_labels_that_need_validation,
    get_receipt_details,
    _prompt_receipt_text,
)
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel, ReceiptMetadata
from receipt_label.utils import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

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

EXAMPLE_CAP = 4
LINE_WINDOW = 5


def _make_tagged_example(word: ReceiptWord, label: ReceiptWordLabel, window=2) -> str:
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


def _second_pass_prompt(
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
        l.label for l in labels if l.validation_status == ValidationStatus.INVALID.value
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

    prompt_lines.append(f"Similar words after pruning: {len(filtered_matches)}")
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


chunks = chunk_into_completion_batches(list_labels_that_need_validation())

image_id = list(chunks.keys())[0]
receipt_id = list(chunks[image_id].keys())[0]
labels_need_validation = chunks[image_id][receipt_id]
print(f"labels_need_validation: {len(labels_need_validation)}")

lines, words, metadata, all_labels = get_receipt_details(image_id, receipt_id)
for label in labels_need_validation:
    line_id = label.line_id
    word_id = label.word_id
    word = next(
        (w for w in words if w.word_id == word_id and w.line_id == line_id), None
    )
    if word is None:
        print(f"word not found for label: {label}")
        continue
    # Get all labels for this word
    other_word_labels = [
        l
        for l in all_labels
        if l.word_id == word_id and l.line_id == line_id and l.label != label.label
    ]
    # If all word labels have a validation_status of NONE we do the first pass
    if all(
        l.validation_status == ValidationStatus.NONE.value for l in other_word_labels
    ):
        continue
        print("First pass")
        print(f"{word.text}: {label.label}")
        print(f"other_word_labels: {[l.label for l in other_word_labels]}")

    # If there are any labels with a validation_status of INVALID we do the second pass
    elif any(
        l.validation_status == ValidationStatus.INVALID.value for l in other_word_labels
    ):
        print("Second pass")
        print(f"{word.text}: {label.label}")
        print(f"other_word_labels: {[l.label for l in other_word_labels]}")
        similar_labels, _ = dynamo_client.getReceiptWordLabelsByLabel(
            label=label.label,
            limit=100,
        )
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
        messages = [
            {
                "role": "system",
                "content": _second_pass_prompt(
                    word, label, similar_words, similar_labels, metadata
                ),
            },
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
        # Grab the first choice’s function call
        func_call = response.choices[0].message.function_call

        # The raw JSON is in .arguments
        raw_json = (
            func_call.arguments
        )  # e.g. '{"is_valid":true}' or '{"is_valid":false,"correct_label":"ADDRESS_LINE"}'

        # Parse it into a dict
        result: dict = json.loads(raw_json)
        if not result.get("is_valid"):
            if label.label == result.get("correct_label"):
                # False negative marked as correct
                print("VALID")
                print(f"Correct label: {result.get('correct_label')}")

            else:
                print("INVALID")
                print(f"Invalid label: {label.label}")
                print(f"Correct label: {result.get('correct_label')}")
                print(f"Rationale: {result.get('rationale')}")
        else:
            print("VALID")
            print(f"Correct label: {label.label}")

    # Otherwise we update the validation status to NEEDS_REVIEW
    else:
        continue
        print("Final pass")
        print("Need to Update to Needs Review")
        print(f"{word.text}: {label.label}")
    print()
