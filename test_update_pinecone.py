import json
from receipt_label.utils import get_clients
from datetime import datetime
import sys
from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    ReceiptLine,
    ReceiptMetadata,
)
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


def get_receipt_metadata(
    label: ReceiptWordLabel, receipt_metadatas: list[ReceiptMetadata]
) -> ReceiptMetadata:
    return next(
        (
            metadata
            for metadata in receipt_metadatas
            if metadata.receipt_id == label.receipt_id
            and metadata.image_id == label.image_id
        ),
        None,
    )


with open("words.ndjson", "r") as f:
    all_words = [ReceiptWord(**json.loads(line)) for line in f]

with open("receipt_word_labels.ndjson", "r") as f:
    receipt_word_labels = [ReceiptWordLabel(**json.loads(line)) for line in f]

with open("receipt_lines.ndjson", "r") as f:
    receipt_lines = [ReceiptLine(**json.loads(line)) for line in f]

with open("receipt_metadatas.ndjson", "r") as f:
    receipt_metadatas = [
        ReceiptMetadata(
            **{
                k: (datetime.fromisoformat(v) if k == "timestamp" else v)
                for k, v in json.loads(line).items()
                if k != "validation_status"
            }
        )
        for line in f
    ]

dynamo_client.updateReceiptMetadatas(
    receipt_metadatas=receipt_metadatas,
)
