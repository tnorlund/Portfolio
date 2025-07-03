import os

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt, ReceiptWord, ReceiptWordLabel
from receipt_label.constants import CORE_LABELS

DYNAMO_TABLE_NAME = os.getenv("DYNAMO_TABLE_NAME")
LIST_CHUNK_SIZE = 1000


def _get_word(
    label: ReceiptWordLabel, words: list[ReceiptWord]
) -> ReceiptWord:
    """
    Get the word that matches the label
    """
    for word in words:
        if word.word == label.word:
            return word
    return None


def _get_receipt(word: ReceiptWord, receipts: list[Receipt]) -> Receipt:
    """
    Get the receipt that matches the word
    """
    for receipt in receipts:
        if receipt.id == word.receipt_id:
            return receipt


def create_dataset(labels: list[str]) -> list[dict]:
    if not isinstance(labels, list):
        raise ValueError("labels must be a list")
    if not all(isinstance(label, str) for label in labels):
        raise ValueError("labels must be a list of strings")
    if not all(label in list(CORE_LABELS.keys()) for label in labels):
        raise ValueError("labels must be a list of core labels")

    dynamo_client = DynamoClient(DYNAMO_TABLE_NAME)
    # Get all Receipt Words
    words, lek = dynamo_client.listReceiptWords(
        limit=LIST_CHUNK_SIZE,
        lastEvaluatedKey=None,
    )
    while lek is not None:
        next_words, lek = dynamo_client.listReceiptWords(
            limit=LIST_CHUNK_SIZE,
            lastEvaluatedKey=lek,
        )
        words.extend(next_words)

    # Get all Receipts
    receipts, lek = dynamo_client.listReceipts(
        limit=LIST_CHUNK_SIZE,
        lastEvaluatedKey=None,
    )
    while lek is not None:
        next_receipts, lek = dynamo_client.listReceipts(
            limit=LIST_CHUNK_SIZE,
            lastEvaluatedKey=lek,
        )
        receipts.extend(next_receipts)

    dataset: list[dict] = []
    for label in labels:
        word_labels, lek = dynamo_client.getReceiptWordLabelsByLabel(label)
        while lek is not None:
            next_word_labels, lek = dynamo_client.getReceiptWordLabelsByLabel(
                label, lastEvaluatedKey=lek
            )
            word_labels.extend(next_word_labels)

        # Only use the VALID labels
        word_labels = [
            label
            for label in word_labels
            if label.validation_status == "VALID"
        ]

        # Get the words
        words_for_label = [_get_word(label, words) for label in word_labels]

        # Get the receipts
        receipts_for_label = [
            _get_receipt(word, receipts) for word in words_for_label
        ]

        # Calculate scale factor maintaining aspect ratio
        max_dim = 1000
        # If no valid receipts, skip
        if not receipts_for_label or all(
            r is None for r in receipts_for_label
        ):
            continue
        # Use the first non-None receipt for scaling
        first_receipt = next(
            (r for r in receipts_for_label if r is not None), None
        )
        if first_receipt is None:
            continue
        scale = max_dim / max(first_receipt.width, first_receipt.height)
        scaled_width = int(first_receipt.width * scale)
        scaled_height = int(first_receipt.height * scale)

        for label_obj, word, receipt in zip(
            word_labels, words_for_label, receipts_for_label
        ):
            if word is None or receipt is None:
                continue

            tag_label = (
                label_obj.label if label_obj.label in CORE_LABELS else "O"
            )

            x1 = min(word.top_left["x"], word.bottom_left["x"])
            y1 = min(word.top_left["y"], word.top_right["y"])
            x2 = max(word.top_right["x"], word.bottom_right["x"])
            y2 = max(word.bottom_left["y"], word.bottom_right["y"])

            normalized_bboxes = [
                int(x1 * scaled_width),
                int(y1 * scaled_height),
                int(x2 * scaled_width),
                int(y2 * scaled_height),
            ]

            dataset.append(
                {
                    "text": word.word,
                    "bboxes": normalized_bboxes,
                    "label": tag_label,
                    "word_id": word.word_id,
                    "line_id": word.line_id,
                    "image_id": receipt.image_id,
                    "receipt_id": receipt.id,
                    "width": receipt.width,
                    "height": receipt.height,
                }
            )
        print(f"Collected {len(dataset)} examples for label '{label}'")

    # Sort dataset by line_id and word_id to maintain reading order
    dataset.sort(key=lambda x: (x["line_id"], x["word_id"]))

    # Convert labels to IOB format
    processed_dataset = []
    for i, word_entry in enumerate(dataset):
        base_label = word_entry["label"]
        iob_label = "O"

        if base_label != "O":
            is_continuation = False
            if i > 0:
                prev = dataset[i - 1]
                same_label = prev["label"] == base_label
                y_diff = abs(
                    ((word_entry["bboxes"][1] + word_entry["bboxes"][3]) // 2)
                    - ((prev["bboxes"][1] + prev["bboxes"][3]) // 2)
                )
                x_diff = word_entry["bboxes"][0] - prev["bboxes"][2]
                if same_label and y_diff < 20 and x_diff < 50:
                    is_continuation = True

            iob_label = (
                f"I-{base_label}" if is_continuation else f"B-{base_label}"
            )

        word_entry["label"] = iob_label
        processed_dataset.append(word_entry)

    return processed_dataset
