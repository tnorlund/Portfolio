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
if READ_PINECONE:
    print("Getting all vectors with merchant name", end="")
    sys.stdout.write(".")
    sys.stdout.flush()
    vectors_with_merchant_name = []
    for ids in pinecone_index.list(prefix="IMAGE", namespace="words"):
        sys.stdout.write(".")
        sys.stdout.flush()
        response = pinecone_index.fetch(ids=ids, namespace="words")
        for vector_id, vector in response.vectors.items():
            metadata = vector.metadata
            if metadata.get("merchant_name"):
                vectors_with_merchant_name.append(vector_id)
    print()
    with open("vectors_with_merchant_name.jsonl", "w") as f:
        for vector_id in vectors_with_merchant_name:
            f.write(json.dumps({"id": vector_id}) + "\n")
else:
    with open("vectors_with_merchant_name.jsonl", "r") as f:
        vectors_with_merchant_name = [json.loads(line)["id"] for line in f]
print(f"Found {len(vectors_with_merchant_name)} vectors with merchant name")

# for vector_id in vectors_with_merchant_name:
#     response = pinecone_index.fetch(ids=vector_id, namespace="words")
#     vector = response.vectors[vector_id]
#     metadata = vector.metadata
#     print(metadata)

if UPDATE_PINECONE:
    print("Updating merchant names in Pinecone vectors...")
    # Process vectors in batches to avoid rate limits
    batch_size = 100
    total_updated = 0

    for i in range(0, len(vectors_with_merchant_name), batch_size):
        batch_ids = vectors_with_merchant_name[i : i + batch_size]
        # Fetch the current vectors to get their metadata
        responses = pinecone_index.fetch(ids=batch_ids, namespace="words")

        updates = []
        for vector_id, vector in responses.vectors.items():
            current_metadata = vector.metadata
            current_merchant = current_metadata.get("merchant_name", "")

            # Extract image_id and receipt_id from the vector ID
            parts = vector_id.split("#")
            image_id = parts[1]
            receipt_id = int(parts[3])
            receipt_metadata = get_receipt_metadata_by_image_id_and_receipt_id(
                image_id, receipt_id, receipt_metadatas
            )

            # Determine the consolidated merchant name
            # Priority: canonical name > regular merchant name > vector metadata
            if receipt_metadata:
                if (
                    hasattr(receipt_metadata, "canonical_merchant_name")
                    and receipt_metadata.canonical_merchant_name
                ):
                    consolidated_merchant = (
                        receipt_metadata.canonical_merchant_name.strip().title()
                    )
                elif (
                    hasattr(receipt_metadata, "merchant_name")
                    and receipt_metadata.merchant_name
                ):
                    consolidated_merchant = (
                        receipt_metadata.merchant_name.strip().title()
                    )
                else:
                    consolidated_merchant = current_merchant.strip().title()
            else:
                consolidated_merchant = current_merchant.strip().title()

            # Only update if there's a change
            if consolidated_merchant != current_merchant:
                # Update metadata with consolidated merchant name
                updated_metadata = current_metadata.copy()
                updated_metadata["merchant_name"] = consolidated_merchant

                # Add to updates list
                updates.append(
                    {
                        "id": vector_id,
                        "metadata": updated_metadata,
                        # Include sparse_values and values if you have them
                    }
                )

        # Update vectors in batch if there are any updates
        if updates:
            for update_item in updates:
                vector_id = update_item["id"]
                updated_metadata = update_item["metadata"]
                pinecone_index.update(
                    id=vector_id, set_metadata=updated_metadata, namespace="words"
                )
                total_updated += 1
            print(f"Updated {len(updates)} vectors in batch {i//batch_size + 1}")

        sys.stdout.write(".")
        sys.stdout.flush()

    print(f"\nCompleted updates. Total vectors updated: {total_updated}")
