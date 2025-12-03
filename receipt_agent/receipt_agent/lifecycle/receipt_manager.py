"""
Receipt Lifecycle Manager

Main entry point for creating and deleting receipts across DynamoDB and ChromaDB.
"""

from dataclasses import dataclass
from typing import List, Optional

from receipt_agent.lifecycle.compaction_manager import (
    check_compaction_status,
    wait_for_compaction,
)
from receipt_agent.lifecycle.embedding_manager import create_embeddings
from receipt_agent.lifecycle.ndjson_manager import export_receipt_ndjson
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)


@dataclass
class ReceiptCreationResult:
    """Result of creating a receipt."""

    receipt_id: int
    compaction_run_id: Optional[str] = None
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
    receipt_metadata: Optional[ReceiptMetadata] = None,
    chromadb_bucket: Optional[str] = None,
    artifacts_bucket: Optional[str] = None,
    embed_ndjson_queue_url: Optional[str] = None,
    merchant_name: Optional[str] = None,
    create_embeddings_flag: bool = True,
    export_ndjson_flag: bool = True,
    wait_for_compaction_flag: bool = False,
    dry_run: bool = False,
) -> ReceiptCreationResult:
    """
    Create a receipt with all associated entities in DynamoDB and ChromaDB.

    This is the main function for creating receipts. It handles:
    1. Saving to DynamoDB (Receipt, ReceiptLine, ReceiptWord, ReceiptLetter, ReceiptWordLabel, ReceiptMetadata)
    2. Creating embeddings in ChromaDB (if create_embeddings_flag is True)
    3. Exporting NDJSON to S3 (if export_ndjson_flag is True)
    4. Waiting for compaction (if wait_for_compaction_flag is True)

    Args:
        client: DynamoDB client
        receipt: Receipt entity to create
        receipt_lines: List of ReceiptLine entities
        receipt_words: List of ReceiptWord entities
        receipt_letters: Optional list of ReceiptLetter entities
        receipt_labels: Optional list of ReceiptWordLabel entities
        receipt_metadata: Optional ReceiptMetadata entity
        chromadb_bucket: S3 bucket for ChromaDB deltas (required if create_embeddings_flag is True)
        artifacts_bucket: S3 bucket for artifacts/NDJSON (required if export_ndjson_flag is True)
        embed_ndjson_queue_url: Optional queue URL for NDJSON processing (not used if create_embeddings_flag is True)
        merchant_name: Optional merchant name for embedding context
        create_embeddings_flag: If True, create embeddings and CompactionRun
        export_ndjson_flag: If True, export NDJSON files to S3
        wait_for_compaction_flag: If True, wait for compaction to complete before returning
        dry_run: If True, don't save to DynamoDB (but still create embeddings if requested)

    Returns:
        ReceiptCreationResult with receipt_id and compaction_run_id
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
            if receipt_metadata:
                client.add_receipt_metadata(receipt_metadata)
            if receipt_labels:
                for label in receipt_labels:
                    client.add_receipt_word_label(label)
            print(f"✅ Saved receipt {receipt_id} to DynamoDB")

        # 2. Create embeddings (if requested)
        compaction_run_id = None
        if create_embeddings_flag and chromadb_bucket:
            compaction_run_id = create_embeddings(
                client=client,
                chromadb_bucket=chromadb_bucket,
                image_id=image_id,
                receipt_id=receipt_id,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                merchant_name=merchant_name,
            )

        # 3. Export NDJSON (if requested)
        if export_ndjson_flag and artifacts_bucket:
            export_receipt_ndjson(
                client=client,
                artifacts_bucket=artifacts_bucket,
                image_id=image_id,
                receipt_id=receipt_id,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
            )

        # 4. Wait for compaction (if requested)
        if wait_for_compaction_flag and compaction_run_id:
            try:
                wait_for_compaction(
                    client=client,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
            except (TimeoutError, RuntimeError) as e:
                print(f"⚠️  Compaction wait failed: {e}")
                # Don't fail the entire operation if compaction times out

        return ReceiptCreationResult(
            receipt_id=receipt_id,
            compaction_run_id=compaction_run_id,
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
    Delete a receipt and all associated entities from DynamoDB.

    **ChromaDB Embedding Deletion**: The enhanced compactor automatically deletes
    embeddings when the Receipt entity is deleted from DynamoDB (via DynamoDB streams).
    The compactor is the source of truth for ChromaDB - do not manually delete embeddings.

    This handles deletion in the correct order:
    1. Delete from DynamoDB in reverse order of creation:
       - ReceiptWordLabel
       - ReceiptWord
       - ReceiptLine
       - ReceiptLetter
       - ReceiptMetadata
       - CompactionRun
       - Receipt (triggers compactor to delete embeddings automatically)

    Args:
        client: DynamoDB client
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_labels: Optional list of ReceiptWordLabel entities (fetched if not provided)
        receipt_letters: Optional list of ReceiptLetter entities (fetched if not provided)

    Returns:
        ReceiptDeletionResult with deletion status

    Note:
        The enhanced compactor queries DynamoDB for ReceiptLine/ReceiptWord entities to
        construct ChromaDB IDs. If you delete lines/words before the Receipt, the compactor
        won't be able to find them. To ensure proper deletion, delete the Receipt entity
        while lines/words still exist, or delete the Receipt first (if foreign key constraints allow).
        The compactor will handle embedding deletion automatically via DynamoDB streams.
    """
    try:
        dynamodb_deleted = False

        # Delete from DynamoDB in reverse order of creation
        # Fetch data if not provided
        if receipt_labels is None:
            try:
                receipt_labels, _ = (
                    client.list_receipt_word_labels_for_receipt(
                        image_id, receipt_id
                    )
                )
            except Exception:
                receipt_labels = []

        if receipt_letters is None:
            try:
                # Note: There may not be a direct method for listing letters, so we'll skip if not available
                receipt_letters = []
            except Exception:
                receipt_letters = []

        # Delete labels first
        if receipt_labels:
            print(f"   Deleting {len(receipt_labels)} labels...")
            try:
                # Use batch delete method (consistent with delete_receipt_words/delete_receipt_lines)
                client.delete_receipt_word_labels(receipt_labels)
                print(f"      ✅ Deleted labels")
            except Exception as e:
                print(f"      ⚠️  Error deleting labels: {e}")

        # Delete words
        receipt_words = client.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
        if receipt_words:
            print(f"   Deleting {len(receipt_words)} words...")
            client.delete_receipt_words(receipt_words)
            print(f"      ✅ Deleted words")

        # Delete lines
        receipt_lines = client.list_receipt_lines_from_receipt(
            image_id, receipt_id
        )
        if receipt_lines:
            print(f"   Deleting {len(receipt_lines)} lines...")
            client.delete_receipt_lines(receipt_lines)
            print(f"      ✅ Deleted lines")

        # Delete letters
        if receipt_letters:
            print(f"   Deleting {len(receipt_letters)} letters...")
            try:
                client.delete_receipt_letters(receipt_letters)
                print(f"      ✅ Deleted letters")
            except AttributeError:
                print(f"      ⚠️  Letter deletion not supported")

        # Delete metadata
        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                print(f"   Deleting metadata...")
                client.delete_receipt_metadata(image_id, receipt_id)
                print(f"      ✅ Deleted metadata")
        except Exception:
            pass  # Metadata might not exist

        # Delete CompactionRun
        try:
            runs, _ = client.list_compaction_runs_for_receipt(
                image_id, receipt_id
            )
            if runs:
                print(f"   Deleting {len(runs)} compaction runs...")
                for run in runs:
                    client.delete_compaction_run(
                        image_id, receipt_id, run.run_id
                    )
                print(f"      ✅ Deleted compaction runs")
        except Exception:
            pass  # Compaction runs might not exist

        # Delete receipt (last)
        # This triggers the enhanced compactor to automatically delete embeddings via DynamoDB streams
        # The compactor is the source of truth for ChromaDB - it queries DynamoDB for lines/words
        # to construct ChromaDB IDs and deletes embeddings from S3/EFS snapshots
        print(f"   Deleting receipt...")
        # Get receipt entity to pass to delete method (consistent with other delete methods)
        receipt = client.get_receipt(image_id, receipt_id)
        client.delete_receipt(receipt)
        print(f"      ✅ Deleted receipt {receipt_id}")
        print(
            f"      ℹ️  Enhanced compactor will automatically delete embeddings from ChromaDB via DynamoDB streams"
        )

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
