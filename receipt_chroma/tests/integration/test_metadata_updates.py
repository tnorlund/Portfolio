"""Integration tests for metadata update processing."""

import pytest
from uuid import uuid4
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma import ChromaClient
from receipt_chroma.compaction.metadata import apply_metadata_updates
from tests.helpers.factories import (
    create_metadata_message,
    create_mock_logger,
    create_receipt_lines_in_dynamodb,
    create_receipt_words_in_dynamodb,
)


@pytest.mark.integration
class TestMetadataUpdates:
    """Test metadata update processing with real ChromaDB operations."""

    def test_apply_metadata_updates_modify_event(
        self, temp_chromadb_dir, mock_logger, dynamo_client
    ):
        """Test applying MODIFY metadata updates to ChromaDB."""
        # Generate a valid UUID for testing
        test_image_id = str(uuid4())

        # Create receipt lines in DynamoDB first
        create_receipt_lines_in_dynamodb(
            dynamo_client, image_id=test_image_id, receipt_id=1, num_lines=2
        )

        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data to lines collection
        client.upsert(
            collection_name="lines",
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Line 1", "merchant_name": "Old Merchant"},
                {"text": "Line 2", "merchant_name": "Old Merchant"},
            ],
        )

        # Create metadata update message
        from receipt_dynamo_stream.models import FieldChange

        update_msg = create_metadata_message(
            image_id=test_image_id,
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Old Merchant", new="New Merchant")
            },
            collections=(ChromaDBCollection.LINES,),
        )

        # Apply metadata updates
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[update_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            dynamo_client=dynamo_client,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].updated_count == 2  # Updated 2 embeddings
        assert results[0].image_id == test_image_id
        assert results[0].receipt_id == 1
        assert results[0].error is None

        # Verify metadata was updated in ChromaDB
        collection = client.get_collection("lines")
        embeddings_data = collection.get(
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
            ]
        )

        assert embeddings_data["metadatas"][0]["merchant_name"] == "New Merchant"
        assert embeddings_data["metadatas"][1]["merchant_name"] == "New Merchant"

        client.close()

    def test_apply_metadata_updates_remove_event(
        self, temp_chromadb_dir, mock_logger, dynamo_client
    ):
        """Test applying REMOVE metadata updates to ChromaDB."""
        # Generate a valid UUID for testing
        test_image_id = str(uuid4())

        # Create receipt lines in DynamoDB first
        create_receipt_lines_in_dynamodb(
            dynamo_client, image_id=test_image_id, receipt_id=1, num_lines=2
        )

        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data to lines collection
        client.upsert(
            collection_name="lines",
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Line 1", "merchant_name": "Test Merchant"},
                {"text": "Line 2", "merchant_name": "Test Merchant"},
            ],
        )

        # Create metadata remove message
        remove_msg = create_metadata_message(
            image_id=test_image_id,
            receipt_id=1,
            event_name="REMOVE",
            changes={},
            collections=(ChromaDBCollection.LINES,),
        )

        # Apply metadata removal
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[remove_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            dynamo_client=dynamo_client,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].updated_count == 2  # Removed metadata from 2 embeddings
        assert results[0].image_id == test_image_id
        assert results[0].receipt_id == 1
        assert results[0].error is None

        # Verify metadata fields were cleared (should only have text field)
        collection = client.get_collection("lines")
        embeddings_data = collection.get(
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00002",
            ]
        )

        # Merchant metadata should be removed
        assert "merchant_name" not in embeddings_data["metadatas"][0]
        assert "merchant_name" not in embeddings_data["metadatas"][1]

        client.close()

    def test_apply_metadata_updates_to_words_collection(
        self, temp_chromadb_dir, mock_logger, dynamo_client
    ):
        """Test applying metadata updates to words collection."""
        # Generate a valid UUID for testing
        test_image_id = str(uuid4())

        # Create receipt words in DynamoDB first
        create_receipt_words_in_dynamodb(
            dynamo_client, image_id=test_image_id, receipt_id=1, line_id=1, num_words=2
        )

        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data to words collection
        client.upsert(
            collection_name="words",
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Word1", "merchant_name": "Old Merchant"},
                {"text": "Word2", "merchant_name": "Old Merchant"},
            ],
        )

        # Create metadata update message
        from receipt_dynamo_stream.models import FieldChange

        update_msg = create_metadata_message(
            image_id=test_image_id,
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Old Merchant", new="Updated Merchant")
            },
            collections=(ChromaDBCollection.WORDS,),
        )

        # Apply metadata updates
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[update_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
            dynamo_client=dynamo_client,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].updated_count == 2
        assert results[0].collection == "words"

        # Verify metadata was updated
        collection = client.get_collection("words")
        embeddings_data = collection.get(
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001#WORD#00002",
            ]
        )

        assert embeddings_data["metadatas"][0]["merchant_name"] == "Updated Merchant"
        assert embeddings_data["metadatas"][1]["merchant_name"] == "Updated Merchant"

        client.close()

    def test_apply_metadata_updates_multiple_messages(
        self, temp_chromadb_dir, mock_logger, dynamo_client
    ):
        """Test applying multiple metadata update messages."""
        # Generate a valid UUID for testing
        test_image_id = str(uuid4())

        # Create receipt lines in DynamoDB for both receipts
        create_receipt_lines_in_dynamodb(
            dynamo_client, image_id=test_image_id, receipt_id=1, num_lines=1
        )
        create_receipt_lines_in_dynamodb(
            dynamo_client, image_id=test_image_id, receipt_id=2, num_lines=1
        )

        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data for two receipts
        client.upsert(
            collection_name="lines",
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00002#LINE#00001",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Receipt 1", "merchant_name": "Merchant A"},
                {"text": "Receipt 2", "merchant_name": "Merchant B"},
            ],
        )

        # Create multiple metadata update messages
        from receipt_dynamo_stream.models import FieldChange

        update_msg_1 = create_metadata_message(
            image_id=test_image_id,
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Merchant A", new="New Merchant A")
            },
            collections=(ChromaDBCollection.LINES,),
        )

        update_msg_2 = create_metadata_message(
            image_id=test_image_id,
            receipt_id=2,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Merchant B", new="New Merchant B")
            },
            collections=(ChromaDBCollection.LINES,),
        )

        # Apply metadata updates
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[update_msg_1, update_msg_2],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            dynamo_client=dynamo_client,
        )

        # Verify results
        assert len(results) == 2
        assert all(r.error is None for r in results)
        assert results[0].receipt_id == 1
        assert results[1].receipt_id == 2

        # Verify both updates were applied
        collection = client.get_collection("lines")
        embeddings_data = collection.get(
            ids=[
                f"IMAGE#{test_image_id}#RECEIPT#00001#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00002#LINE#00001",
            ]
        )

        assert embeddings_data["metadatas"][0]["merchant_name"] == "New Merchant A"
        assert embeddings_data["metadatas"][1]["merchant_name"] == "New Merchant B"

        client.close()

    def test_apply_metadata_updates_collection_not_found(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test handling when collection doesn't exist."""
        # Create an empty ChromaDB client
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Create metadata update message
        update_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
        )

        # Apply metadata updates to non-existent collection
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[update_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
        )

        # Should return empty results when collection not found
        assert len(results) == 0

        client.close()

    def test_apply_metadata_updates_with_error_handling(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test error handling when metadata update fails."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data
        client.upsert(
            collection_name="lines",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Line 1"}],
        )

        # Create an invalid metadata message (missing required fields)
        from receipt_dynamo_stream.models import StreamMessage
        from datetime import datetime

        invalid_msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={},  # Missing image_id and receipt_id
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="invalid-record",
            aws_region="us-east-1",
        )

        # Apply metadata updates
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[invalid_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
        )

        # Should have error result
        assert len(results) == 1
        assert results[0].error is not None
        assert results[0].updated_count == 0

        client.close()

    def test_apply_metadata_updates_no_messages(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test applying metadata updates with no messages."""
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Create lines collection
        client.upsert(
            collection_name="lines",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Line 1"}],
        )

        # Apply with empty message list
        results = apply_metadata_updates(
            chroma_client=client,
            metadata_messages=[],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
        )

        # Should return empty results
        assert len(results) == 0

        client.close()
