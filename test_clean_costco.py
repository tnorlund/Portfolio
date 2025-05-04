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

READ_PINECONE = False
UPDATE_PINECONE = True  # Set to True to update Pinecone vectors


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


def get_receipt_metadata_by_image_id_and_receipt_id(
    image_id: str, receipt_id: int, receipt_metadatas: list[ReceiptMetadata]
) -> ReceiptMetadata:
    return next(
        (
            metadata
            for metadata in receipt_metadatas
            if metadata.image_id == image_id and metadata.receipt_id == receipt_id
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

# Get all Costco metadata
costco_metadata = [
    metadata
    for metadata in receipt_metadatas
    if "COSTCO" in metadata.merchant_name.upper()
]

print(f"Found {len(costco_metadata)} Costco metadatas")

# get place IDs from Costco metadata
place_ids = [metadata.place_id for metadata in costco_metadata]
place_ids = list(set(place_ids))
print(f"Found {len(place_ids)} unique place IDs")

from collections import Counter

# 1. Choose the most common place ID and canonical merchant name
place_id_counter = Counter(m.place_id for m in costco_metadata if m.place_id)
name_counter = Counter(
    m.merchant_name.strip().title() for m in costco_metadata if m.merchant_name
)

preferred_place_id = place_id_counter.most_common(1)[0][0]
preferred_name = name_counter.most_common(1)[0][0]

print(f"Preferred place_id: {preferred_place_id}")
print(f"Preferred merchant_name: {preferred_name}")

# 2. Update all Costco records to use the preferred canonical values
for metadata in costco_metadata:
    metadata.canonical_place_id = preferred_place_id
    metadata.canonical_merchant_name = preferred_name

# 3. Persist updates to DynamoDB
dynamo_client.updateReceiptMetadatas(costco_metadata)
print(f"Updated {len(costco_metadata)} Costco metadata records with canonical values.")
