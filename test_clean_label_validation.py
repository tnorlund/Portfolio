import json
from receipt_label.utils import get_clients
import sys
from receipt_dynamo.entities import ReceiptWordLabel, ReceiptWord, ReceiptLine
from receipt_dynamo.constants import ValidationStatus

dynamo_client, openai_client, pinecone_index = get_clients()


def get_word(label: ReceiptWordLabel, words: list[ReceiptWord]) -> ReceiptWord:
    return next(
        (
            word
            for word in words
            if word.image_id == label.image_id
            and word.receipt_id == label.receipt_id
            and word.line_id == label.line_id
            and word.word_id == label.word_id
        ),
        None,
    )


def get_labels(
    word: ReceiptWord, labels: list[ReceiptWordLabel]
) -> list[ReceiptWordLabel]:
    return [
        label
        for label in labels
        if label.receipt_id == word.receipt_id
        and label.image_id == word.image_id
        and label.line_id == word.line_id
        and label.word_id == word.word_id
    ]


with open("words.ndjson", "r") as f:
    words = [ReceiptWord(**json.loads(line)) for line in f]

with open("receipt_word_labels.ndjson", "r") as f:
    receipt_word_labels = [ReceiptWordLabel(**json.loads(line)) for line in f]

with open("receipt_lines.ndjson", "r") as f:
    receipt_lines = [ReceiptLine(**json.loads(line)) for line in f]

with open("receipt_metadatas.ndjson", "r") as f:
    receipt_metadatas = [ReceiptMetadata(**json.loads(line)) for line in f]

# Group together labels by receipt_id, image_id, line_id, and word_id
labels_by_word: dict[tuple[int, str, int, int], list[ReceiptWordLabel]] = {}
for label in receipt_word_labels:
    if (
        label.receipt_id,
        label.image_id,
        label.line_id,
        label.word_id,
    ) not in labels_by_word:
        labels_by_word[
            (label.receipt_id, label.image_id, label.line_id, label.word_id)
        ] = []
    labels_by_word[
        (label.receipt_id, label.image_id, label.line_id, label.word_id)
    ].append(label)

print(f"Found {len(labels_by_word)} words with labels")

both_valid_invalid_words: list[ReceiptWord] = []

for key, labels in labels_by_word.items():
    statuses = {label.validation_status for label in labels}
    if ValidationStatus.VALID in statuses and ValidationStatus.INVALID in statuses:
        word = get_word(labels[0], words)
        if word:
            both_valid_invalid_words.append(word)

print(
    f"Found {len(both_valid_invalid_words)} words with both VALID and INVALID labels."
)

# Get the labels for each word
for word in both_valid_invalid_words[0:10]:
    labels = get_labels(word, receipt_word_labels)
    # Split by validation status
    valid_labels = [
        label for label in labels if label.validation_status == ValidationStatus.VALID
    ]
    invalid_labels = [
        label for label in labels if label.validation_status == ValidationStatus.INVALID
    ]
    pending_labels = [
        label for label in labels if label.validation_status == ValidationStatus.PENDING
    ]
    needs_review_labels = [
        label
        for label in labels
        if label.validation_status == ValidationStatus.NEEDS_REVIEW
    ]
    print(f"{word.text}")
    if valid_labels:
        valid_labels_text = ", ".join([label.label for label in valid_labels])
        print(f"VALID: {valid_labels_text}")
    if invalid_labels:
        invalid_labels_text = ", ".join([label.label for label in invalid_labels])
        print(f"INVALID: {invalid_labels_text}")
    if pending_labels:
        pending_labels_text = ", ".join([label.label for label in pending_labels])
        print(f"PENDING: {pending_labels_text}")
    if needs_review_labels:
        needs_review_labels_text = ", ".join(
            [label.label for label in needs_review_labels]
        )
        print(f"NEEDS_REVIEW: {needs_review_labels_text}")
    print()
