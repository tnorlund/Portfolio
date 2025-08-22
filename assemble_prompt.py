import os
from pathlib import Path
import boto3
import random

from receipt_dynamo.data import dynamo_client
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel, ReceiptLine
from receipt_dynamo.constants import ValidationStatus

from receipt_label.utils.chroma_client import (
    ChromaDBClient,
)

LOCAL_CHROMA_WORD_PATH = Path(__file__).parent / "dev.word_chroma"
LOCAL_CHROMA_LINE_PATH = Path(__file__).parent / "dev.line_chroma"
LABEL_TO_VALIDATE = "DATE"


def _labels_for_word(
    word: ReceiptWord, all_labels: list[ReceiptWordLabel]
) -> list[ReceiptWordLabel]:
    """Returns the labels for a specific word."""
    return [
        label
        for label in all_labels
        if label.image_id == word.image_id
        and label.receipt_id == word.receipt_id
        and label.line_id == word.line_id
        and label.word_id == word.word_id
    ]


def _word_from_label(
    label: ReceiptWordLabel, all_words: list[ReceiptWord]
) -> ReceiptWord | None:
    """Returns the word for a specific label."""
    for word in all_words:
        if (
            word.image_id == label.image_id
            and word.receipt_id == label.receipt_id
            and word.line_id == label.line_id
            and word.word_id == label.word_id
        ):
            return word
    raise ValueError("No matching word found")


def _chroma_exists(local_chroma_path: Path) -> bool:
    """Checks to see if the directory exists. Also sees that a directory is
    inside it and that there's a '.sqlite3' in the directory."""
    if not local_chroma_path.exists():
        return False
    has_subdir = any(item.is_dir() for item in local_chroma_path.iterdir())
    has_sqlite = any(
        item.is_file() and item.suffix == ".sqlite3"
        for item in local_chroma_path.iterdir()
    )
    return has_subdir and has_sqlite


def _word_to_chroma_id(word: ReceiptWord) -> str:
    """Converts a ReceiptWord to its ChromaDB ID.

    Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}

    Args:
        word: ReceiptWord entity with image_id, receipt_id, line_id, and word_id

    Returns:
        Formatted ChromaDB ID string
    """
    return (
        f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#"
        f"{word.line_id:05d}#WORD#{word.word_id:05d}"
    )


def _line_to_chroma_id(line: ReceiptLine) -> str:
    """Converts a ReceiptLine to its ChromaDB ID.

    Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}

    Args:
        line: ReceiptLine entity with image_id, receipt_id, and line_id

    Returns:
        Formatted ChromaDB ID string
    """
    return (
        f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#"
        f"{line.line_id:05d}"
    )


for chroma_path in [LOCAL_CHROMA_WORD_PATH, LOCAL_CHROMA_LINE_PATH]:
    if not _chroma_exists(chroma_path):
        pulumi_env = load_env()
        if pulumi_env == {}:
            raise ValueError(
                "Pulumi environment variables not found, try adding the "
                "Pulumi API key"
            )
        chroma_s3_bucket = pulumi_env.get("embedding_chromadb_bucket_name")
        # TODO store prefix in a variable with the Path
        s3_prefix = (
            "lines/snapshot/latest/"
            if "line" in str(chroma_path)
            else "words/snapshot/latest/"
        )
        # Make the S3 client and download the DB
        s3_client = boto3.client("s3")
        response = s3_client.list_objects_v2(
            Bucket=chroma_s3_bucket, Prefix=s3_prefix
        )
        for obj in response.get("Contents", []):
            key = obj["Key"]
            # Remove the prefix from the key to get the relative path
            rel_path = os.path.relpath(key, s3_prefix)
            local_path = chroma_path / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(chroma_s3_bucket, key, str(local_path))

# Use the refactored client without collection_prefix
# metadata_only=True uses default embedding function (no OpenAI API calls)
chroma_line_client = ChromaDBClient(
    persist_directory=str(LOCAL_CHROMA_LINE_PATH),
    mode="read",
    metadata_only=True,
)
chroma_word_client = ChromaDBClient(
    persist_directory=str(LOCAL_CHROMA_WORD_PATH),
    mode="read",
    metadata_only=True,
)
dynamo_client = DynamoClient(load_env().get("dynamodb_table_name"))

receipt_word_labels, last_evaluated_key = (
    dynamo_client.get_receipt_word_labels_by_label(
        label=LABEL_TO_VALIDATE,
        limit=1000,
    )
)
labels_that_need_validation = [
    label
    for label in receipt_word_labels
    if label.validation_status == ValidationStatus.NONE.value
]
print(f"Found {len(labels_that_need_validation)} labels that need validation")

# Iterate over the labels that need to be validated
for i, label_that_needs_validation in enumerate(
    # labels_that_need_validation[:5], 1
    labels_that_need_validation,
    1,
):
    # Get receipt details
    receipt_details = dynamo_client.get_receipt_details(
        image_id=label_that_needs_validation.image_id,
        receipt_id=label_that_needs_validation.receipt_id,
    )
    word_that_needs_validation = _word_from_label(
        label_that_needs_validation, receipt_details.words
    )

    # Get the word's embedding from ChromaDB
    response = chroma_word_client.get_by_ids(
        collection_name="words",
        ids=[_word_to_chroma_id(word_that_needs_validation)],
        include=["embeddings", "documents", "metadatas"],
    )

    if not response["ids"]:
        print(
            f"\n{i}. Word '{word_that_needs_validation.text}' not found in ChromaDB"
        )
        continue

    print(
        f"\n{i}. Word needing validation: '{word_that_needs_validation.text}'"
    )
    print(f"   Image: {word_that_needs_validation.image_id[:8]}...")

    # Get the embedding for similarity search
    word_embedding = response["embeddings"][0]
    current_word_id = _word_to_chroma_id(word_that_needs_validation)

    # Query for similar words
    similar_results = chroma_word_client.query_collection(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=20,  # Get more results to find examples with our label
        include=["documents", "metadatas", "distances"],
    )

    # Filter results for words that have LABEL_TO_VALIDATE in valid or invalid labels
    words_with_label_valid = []
    words_with_label_invalid = []

    for j, (id_, doc, metadata, distance) in enumerate(
        zip(
            similar_results["ids"][0],
            similar_results["documents"][0],
            (
                similar_results["metadatas"][0]
                if similar_results["metadatas"]
                else [{}] * len(similar_results["ids"][0])
            ),
            similar_results["distances"][0],
        )
    ):
        # Skip the same word by comparing IDs
        if id_ == current_word_id:
            continue

        # Check if metadata contains label info
        if metadata:
            valid_labels = metadata.get("validated_labels", "")
            invalid_labels = metadata.get("invalid_labels", "")

            # Check if LABEL_TO_VALIDATE is in valid labels
            if LABEL_TO_VALIDATE in valid_labels:
                words_with_label_valid.append(
                    {
                        "text": doc,  # Document is already the plain text
                        "distance": distance,
                        "id": id_,
                        "merchant": metadata.get("merchant_name", "Unknown"),
                    }
                )

            # Check if LABEL_TO_VALIDATE is in invalid labels
            elif LABEL_TO_VALIDATE in invalid_labels:
                words_with_label_invalid.append(
                    {
                        "text": doc,  # Document is already the plain text
                        "distance": distance,
                        "id": id_,
                        "merchant": metadata.get("merchant_name", "Unknown"),
                    }
                )

    # Display results
    print(f"\n   Similar words where '{LABEL_TO_VALIDATE}' was VALID:")
    if words_with_label_valid:
        for word_info in words_with_label_valid[:3]:
            print(
                f"     ✓ '{word_info['text']}' (distance: {word_info['distance']:.3f})"
            )
            print(f"       Merchant: {word_info['merchant']}")
    else:
        print("     None found")

    print(f"\n   Similar words where '{LABEL_TO_VALIDATE}' was INVALID:")
    if words_with_label_invalid:
        for word_info in words_with_label_invalid[:3]:
            print(
                f"     ✗ '{word_info['text']}' (distance: {word_info['distance']:.3f})"
            )
            print(f"       Merchant: {word_info['merchant']}")
    else:
        print("     None found")

    # Suggest validation based on similar words
    if len(words_with_label_valid) > len(words_with_label_invalid):
        print(
            f"\n   → Suggestion: '{LABEL_TO_VALIDATE}' is likely VALID for '{word_that_needs_validation.text}'"
        )
        print(
            f"     (Based on {len(words_with_label_valid)} valid vs {len(words_with_label_invalid)} invalid similar examples)"
        )
    elif len(words_with_label_invalid) > len(words_with_label_valid):
        print(
            f"\n   → Suggestion: '{LABEL_TO_VALIDATE}' is likely INVALID for '{word_that_needs_validation.text}'"
        )
        print(
            f"     (Based on {len(words_with_label_invalid)} invalid vs {len(words_with_label_valid)} valid similar examples)"
        )
    else:
        print(f"\n   → No clear suggestion - needs manual review")
        print(
            f"     ({len(words_with_label_valid)} valid vs {len(words_with_label_invalid)} invalid similar examples)"
        )

# Pick a random receipt
# random_receipt = random.choice(dynamo_client.list_receipts(limit=100)[0])
# receipt_details = dynamo_client.get_receipt_details(
#     image_id=random_receipt.image_id, receipt_id=random_receipt.receipt_id
# )
# # Get the words with no valid labels that has invalid labels
# invalid_words = [
#     word
#     for word in receipt_details.words
#     if word.valid_label_count < 1 and word.invalid_label_count > 0
# ]

# for invalid_word in invalid_words:
#     invalid_word_labels = _labels_for_word(
#         invalid_word, receipt_details.labels
#     )
#     labels_need_validation = [
#         label
#         for label in _labels_for_word(invalid_word, receipt_details.labels)
#         if label.validation_status == ValidationStatus.NONE.value
#     ]
#     if not labels_need_validation:
#         print(f"No labels needing validation for word '{invalid_word.text}'")
#         continue
#     chroma_id = _word_to_chroma_id(invalid_word)
#     response = chroma_word_client.get_by_ids(
#         collection_name="receipt_words", ids=[chroma_id]
#     )
#     print(f"ChromaDB response for ID {chroma_id}: {response}")


# # Query ChromaDB for the invalid words
# # chroma_ids = [_word_to_chroma_id(word) for word in invalid_words]
# # response = chroma_word_client.get_by_ids(
# #     collection_name="receipt_words", ids=chroma_ids
# # )
# # print(response)
