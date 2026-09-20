"""
Receipt Lifecycle Manager

Main entry point for creating and deleting receipts in DynamoDB.
"""

from dataclasses import dataclass
from typing import List, Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_agent.lifecycle.ndjson_manager import export_receipt_ndjson


@dataclass
class ReceiptCreationResult:
    """Result of creating a receipt."""

    receipt_id: int
    success: bool = True
    error: Optional[str] = None


@dataclass
class ReceiptDeletionResult:
    """Result of deleting a receipt."""

    receipt_id: int
    dynamodb_deleted: bool = False
    success: bool = True
    error: Optional[str] = None


def create_receipt(
    client: DynamoClient,
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    receipt_letters: Optional[List[ReceiptLetter]] = None,
    receipt_labels: Optional[List[ReceiptWordLabel]] = None,
    artifacts_bucket: Optional[str] = None,
    export_ndjson_flag: bool = True,
    dry_run: bool = False,
) -> ReceiptCreationResult:
    """
    Create a receipt with all associated entities in DynamoDB.

    This is the main function for creating receipts. It handles:
    1. Saving to DynamoDB (Receipt, ReceiptLine, ReceiptWord, ReceiptLetter,
       ReceiptWordLabel)
    2. Exporting NDJSON to S3 (if export_ndjson_flag is True)

    Embeddings are produced downstream by the DynamoDB stream processor, so
    this function no longer writes them directly.

    Args:
        client: DynamoDB client
        receipt: Receipt entity to create
        receipt_lines: List of ReceiptLine entities
        receipt_words: List of ReceiptWord entities
        receipt_letters: Optional list of ReceiptLetter entities
        receipt_labels: Optional list of ReceiptWordLabel entities
        artifacts_bucket: S3 bucket for artifacts/NDJSON (required if
            export_ndjson_flag is True)
        export_ndjson_flag: If True, export NDJSON files to S3
        dry_run: If True, don't save to DynamoDB

    Returns:
        ReceiptCreationResult with receipt_id
    """
    try:
        image_id = receipt.image_id
        receipt_id = receipt.receipt_id

        # 1. Save to DynamoDB (unless dry_run)
        if not dry_run:
            client.add_receipt(receipt)
            client.add_receipt_lines(receipt_lines)
            client.add_receipt_words(receipt_words)
            if receipt_letters:
                client.add_receipt_letters(receipt_letters)
            if receipt_labels:
                for label in receipt_labels:
                    client.add_receipt_word_label(label)
            print(f"✅ Saved receipt {receipt_id} to DynamoDB")

        # 2. Export NDJSON (if requested)
        if export_ndjson_flag and artifacts_bucket:
            export_receipt_ndjson(
                client=client,
                artifacts_bucket=artifacts_bucket,
                image_id=image_id,
                receipt_id=receipt_id,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
            )

        return ReceiptCreationResult(
            receipt_id=receipt_id,
            success=True,
        )

    except Exception as e:
        return ReceiptCreationResult(
            receipt_id=receipt.receipt_id,
            success=False,
            error=str(e),
        )


def delete_receipt(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    receipt_labels: Optional[List[ReceiptWordLabel]] = None,
    receipt_letters: Optional[List[ReceiptLetter]] = None,
) -> ReceiptDeletionResult:
    """
    Delete a receipt from DynamoDB.

    This function only deletes the Receipt entity itself. It does not
    delete child records (ReceiptWordLabel, ReceiptWord, ReceiptLine,
    ReceiptLetter, ReceiptPlace); callers that need a full cascade must
    remove those separately.

    Args:
        client: DynamoDB client
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_labels: Deprecated - no longer used (kept for backward
            compatibility)
        receipt_letters: Deprecated - no longer used (kept for backward
            compatibility)

    Returns:
        ReceiptDeletionResult with deletion status
    """
    try:
        dynamodb_deleted = False

        print(f"   Deleting receipt {receipt_id}...")
        # Get receipt entity to pass to delete method
        receipt = client.get_receipt(image_id, receipt_id)
        client.delete_receipt(receipt)
        print(f"      ✅ Deleted receipt {receipt_id}")

        dynamodb_deleted = True

        return ReceiptDeletionResult(
            receipt_id=receipt_id,
            dynamodb_deleted=dynamodb_deleted,
            success=True,
        )

    except Exception as e:
        return ReceiptDeletionResult(
            receipt_id=receipt_id,
            dynamodb_deleted=dynamodb_deleted,
            success=False,
            error=str(e),
        )
