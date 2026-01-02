"""Integration tests for metadata-based deletion queries in ChromaDB.

These tests verify that when DynamoDB entities are already deleted, the fallback
metadata-based query correctly identifies and deletes embeddings from ChromaDB.
"""

import tempfile
from uuid import uuid4

import pytest
from receipt_chroma import ChromaClient
from receipt_chroma.compaction.deletions import (
    _delete_receipt_embeddings,
    apply_receipt_deletions,
)

from receipt_dynamo.constants import ChromaDBCollection
from tests.helpers.factories import (
    create_mock_logger,
    create_mock_metrics,
)


class MockStreamMessage:
    """Mock StreamMessage for deletion testing."""

    def __init__(self, entity_type: str, entity_data: dict, event_name: str = "REMOVE"):
        self.entity_type = entity_type
        self.entity_data = entity_data
        self.event_name = event_name


class MockDynamoClient:
    """Mock DynamoClient that returns empty results (simulates deleted entities)."""

    def list_receipt_words_from_receipt(self, image_id: str, receipt_id: int):
        return []

    def list_receipt_lines_from_receipt(self, image_id: str, receipt_id: int):
        return []


@pytest.mark.integration
class TestMetadataBasedDeletion:
    """Integration tests for metadata-based deletion fallback."""

    def test_delete_receipt_embeddings_via_metadata_query_lines(self):
        """Test that line embeddings are deleted via metadata query when DynamoDB is empty."""
        test_image_id = str(uuid4())
        receipt_id = 1

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ChromaDB with lines that have proper metadata
            client = ChromaClient(persist_directory=tmpdir, mode="write")
            client.upsert(
                collection_name="lines",
                ids=[
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00003",
                ],
                embeddings=[[0.1] * 1536, [0.2] * 1536, [0.3] * 1536],
                metadatas=[
                    {"text": "Line 1", "image_id": test_image_id, "receipt_id": receipt_id},
                    {"text": "Line 2", "image_id": test_image_id, "receipt_id": receipt_id},
                    {"text": "Line 3", "image_id": test_image_id, "receipt_id": receipt_id},
                ],
            )

            # Also add a line from a different receipt (should NOT be deleted)
            other_image_id = str(uuid4())
            client.upsert(
                collection_name="lines",
                ids=[f"IMAGE#{other_image_id}#RECEIPT#00002#LINE#00001"],
                embeddings=[[0.9] * 1536],
                metadatas=[
                    {"text": "Other Line", "image_id": other_image_id, "receipt_id": 2}
                ],
            )

            collection = client.get_collection("lines")
            logger = create_mock_logger()
            metrics = create_mock_metrics()
            dynamo_client = MockDynamoClient()  # Returns empty - simulates deleted entities

            # Verify initial state - 4 embeddings total
            all_before = collection.get()
            assert len(all_before["ids"]) == 4

            # Execute deletion - should use metadata query fallback
            result = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=test_image_id,
                receipt_id=receipt_id,
                collection=ChromaDBCollection.LINES,
                logger=logger,
                metrics=metrics,
                dynamo_client=dynamo_client,
            )

            # Verify result
            assert result.deleted_count == 3
            assert result.image_id == test_image_id
            assert result.receipt_id == receipt_id
            assert result.error is None

            # Verify metadata query metric was recorded
            metric_calls = [call[0][0] for call in metrics.count.call_args_list]
            assert "ReceiptDeletionMetadataQuery" in metric_calls

            # Verify only target embeddings were deleted
            all_after = collection.get()
            assert len(all_after["ids"]) == 1
            assert f"IMAGE#{other_image_id}#RECEIPT#00002#LINE#00001" in all_after["ids"]

            # Verify log message about metadata query
            info_calls = [call for call in logger.info.call_args_list]
            metadata_query_logged = any(
                "metadata-based query" in str(call) for call in info_calls
            )
            assert metadata_query_logged

            client.close()

    def test_delete_receipt_embeddings_via_metadata_query_words(self):
        """Test that word embeddings are deleted via metadata query when DynamoDB is empty."""
        test_image_id = str(uuid4())
        receipt_id = 1

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ChromaDB with words that have proper metadata
            client = ChromaClient(persist_directory=tmpdir, mode="write")
            client.upsert(
                collection_name="words",
                ids=[
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00001",
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00002",
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002#WORD#00001",
                ],
                embeddings=[[0.1] * 1536, [0.2] * 1536, [0.3] * 1536],
                metadatas=[
                    {"text": "Word1", "image_id": test_image_id, "receipt_id": receipt_id},
                    {"text": "Word2", "image_id": test_image_id, "receipt_id": receipt_id},
                    {"text": "Word3", "image_id": test_image_id, "receipt_id": receipt_id},
                ],
            )

            collection = client.get_collection("words")
            logger = create_mock_logger()
            dynamo_client = MockDynamoClient()

            # Verify initial state
            all_before = collection.get()
            assert len(all_before["ids"]) == 3

            # Execute deletion
            result = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=test_image_id,
                receipt_id=receipt_id,
                collection=ChromaDBCollection.WORDS,
                logger=logger,
                dynamo_client=dynamo_client,
            )

            # Verify result
            assert result.deleted_count == 3
            assert result.error is None

            # Verify embeddings were deleted
            all_after = collection.get()
            assert len(all_after["ids"]) == 0

            client.close()

    def test_delete_receipt_no_matching_embeddings(self):
        """Test deletion when no embeddings match the receipt."""
        test_image_id = str(uuid4())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ChromaDB with embeddings for a different receipt
            client = ChromaClient(persist_directory=tmpdir, mode="write")
            other_image_id = str(uuid4())
            client.upsert(
                collection_name="lines",
                ids=[f"IMAGE#{other_image_id}#RECEIPT#00099#LINE#00001"],
                embeddings=[[0.1] * 1536],
                metadatas=[
                    {"text": "Other Line", "image_id": other_image_id, "receipt_id": 99}
                ],
            )

            collection = client.get_collection("lines")
            logger = create_mock_logger()
            dynamo_client = MockDynamoClient()

            # Execute deletion for non-existent receipt
            result = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=test_image_id,
                receipt_id=1,
                collection=ChromaDBCollection.LINES,
                logger=logger,
                dynamo_client=dynamo_client,
            )

            # Verify result - no deletions, no error
            assert result.deleted_count == 0
            assert result.error is None

            # Verify original embedding still exists
            all_after = collection.get()
            assert len(all_after["ids"]) == 1

            client.close()

    def test_apply_receipt_deletions_full_workflow(self):
        """Test the full apply_receipt_deletions workflow with metadata fallback."""
        test_image_id = str(uuid4())
        receipt_id = 1

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ChromaDB with embeddings
            client = ChromaClient(persist_directory=tmpdir, mode="write")
            client.upsert(
                collection_name="lines",
                ids=[
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                    f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
                ],
                embeddings=[[0.1] * 1536, [0.2] * 1536],
                metadatas=[
                    {"text": "Line 1", "image_id": test_image_id, "receipt_id": receipt_id},
                    {"text": "Line 2", "image_id": test_image_id, "receipt_id": receipt_id},
                ],
            )

            logger = create_mock_logger()
            metrics = create_mock_metrics()
            dynamo_client = MockDynamoClient()

            # Create RECEIPT deletion message
            msg = MockStreamMessage(
                entity_type="RECEIPT",
                entity_data={
                    "entity_type": "RECEIPT",
                    "image_id": test_image_id,
                    "receipt_id": receipt_id,
                },
            )

            # Execute deletion
            results = apply_receipt_deletions(
                chroma_client=client,
                receipt_messages=[msg],
                collection=ChromaDBCollection.LINES,
                logger=logger,
                metrics=metrics,
                dynamo_client=dynamo_client,
            )

            # Verify results
            assert len(results) == 1
            assert results[0].deleted_count == 2
            assert results[0].image_id == test_image_id
            assert results[0].receipt_id == receipt_id
            assert results[0].error is None

            # Verify embeddings were deleted
            collection = client.get_collection("lines")
            all_after = collection.get()
            assert len(all_after["ids"]) == 0

            client.close()

    def test_delete_multiple_receipts_via_metadata_query(self):
        """Test deleting multiple receipts in sequence via metadata query."""
        image_ids = [str(uuid4()) for _ in range(3)]

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ChromaDB with embeddings for 3 receipts
            client = ChromaClient(persist_directory=tmpdir, mode="write")

            for idx, image_id in enumerate(image_ids):
                receipt_id = idx + 1
                client.upsert(
                    collection_name="lines",
                    ids=[
                        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#00001",
                        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#00002",
                    ],
                    embeddings=[[0.1] * 1536, [0.2] * 1536],
                    metadatas=[
                        {"text": "Line 1", "image_id": image_id, "receipt_id": receipt_id},
                        {"text": "Line 2", "image_id": image_id, "receipt_id": receipt_id},
                    ],
                )

            logger = create_mock_logger()
            dynamo_client = MockDynamoClient()

            # Verify initial state - 6 embeddings total
            collection = client.get_collection("lines")
            all_before = collection.get()
            assert len(all_before["ids"]) == 6

            # Delete first receipt
            result1 = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=image_ids[0],
                receipt_id=1,
                collection=ChromaDBCollection.LINES,
                logger=logger,
                dynamo_client=dynamo_client,
            )
            assert result1.deleted_count == 2

            # Verify 4 embeddings remain
            all_after_1 = collection.get()
            assert len(all_after_1["ids"]) == 4

            # Delete second receipt
            result2 = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=image_ids[1],
                receipt_id=2,
                collection=ChromaDBCollection.LINES,
                logger=logger,
                dynamo_client=dynamo_client,
            )
            assert result2.deleted_count == 2

            # Verify 2 embeddings remain
            all_after_2 = collection.get()
            assert len(all_after_2["ids"]) == 2

            # Delete third receipt
            result3 = _delete_receipt_embeddings(
                chroma_collection=collection,
                image_id=image_ids[2],
                receipt_id=3,
                collection=ChromaDBCollection.LINES,
                logger=logger,
                dynamo_client=dynamo_client,
            )
            assert result3.deleted_count == 2

            # Verify no embeddings remain
            all_after_3 = collection.get()
            assert len(all_after_3["ids"]) == 0

            client.close()
