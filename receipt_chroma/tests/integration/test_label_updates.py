"""Integration tests for label update processing."""

import pytest
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma import ChromaClient
from receipt_chroma.compaction.labels import apply_label_updates
from tests.helpers.factories import create_label_message, create_mock_logger


@pytest.mark.integration
class TestLabelUpdates:
    """Test label update processing with real ChromaDB operations."""

    def test_apply_label_updates_modify_event(self, temp_chromadb_dir, mock_logger):
        """Test applying MODIFY label updates to ChromaDB."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data to words collection
        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                    "invalid_labels": "",
                }
            ],
        )

        # Create label update message
        from receipt_dynamo_stream.models import FieldChange

        update_msg = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="TOTAL",
            event_name="MODIFY",
            changes={
                "validation_status": FieldChange(old="PENDING", new="VALID"),
                "label_confidence": FieldChange(old="0.5", new="0.95"),
            },
            record_snapshot={
                "validation_status": "VALID",
                "label_confidence": 0.95,
            },
        )

        # Apply label updates
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[update_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].updated_count == 1
        assert results[0].chromadb_id == "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"
        assert results[0].event_name == "MODIFY"
        assert "validation_status" in results[0].changes
        assert "label_confidence" in results[0].changes
        assert results[0].error is None

        # Verify label metadata was updated in ChromaDB
        collection = client.get_collection("words")
        embeddings_data = collection.get(
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"]
        )

        # Should have updated label metadata
        metadata = embeddings_data["metadatas"][0]
        assert "validation_status" in metadata or "label_status" in metadata

        client.close()

    def test_apply_label_updates_remove_event(self, temp_chromadb_dir, mock_logger):
        """Test applying REMOVE label updates to ChromaDB."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data with label metadata
        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "validated",
                    "valid_labels": "TOTAL",
                    "invalid_labels": "",
                }
            ],
        )

        # Create label remove message
        remove_msg = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="TOTAL",
            event_name="REMOVE",
            changes={},
        )

        # Apply label removal
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[remove_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].updated_count == 1
        assert results[0].chromadb_id == "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"
        assert results[0].event_name == "REMOVE"
        assert results[0].error is None

        # Verify label metadata was cleared
        collection = client.get_collection("words")
        embeddings_data = collection.get(
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"]
        )

        # Should have label fields cleared
        metadata = embeddings_data["metadatas"][0]
        assert metadata.get("label_status") in ["auto_suggested", ""]
        assert metadata.get("valid_labels") == ""

        client.close()

    def test_apply_label_updates_multiple_words(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test applying label updates to multiple words."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data for multiple words
        client.upsert(
            collection_name="words",
            ids=[
                "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001",
                "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                    "invalid_labels": "",
                },
                {
                    "text": "$10.00",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                    "invalid_labels": "",
                },
            ],
        )

        # Create multiple label update messages
        from receipt_dynamo_stream.models import FieldChange

        update_msg_1 = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="TOTAL",
            event_name="MODIFY",
            changes={
                "validation_status": FieldChange(old="PENDING", new="VALID")
            },
            record_snapshot={"validation_status": "VALID"},
        )

        update_msg_2 = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=2,
            label="AMOUNT",
            event_name="MODIFY",
            changes={
                "validation_status": FieldChange(old="PENDING", new="VALID")
            },
            record_snapshot={"validation_status": "VALID"},
        )

        # Apply label updates
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[update_msg_1, update_msg_2],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Verify results
        assert len(results) == 2
        assert all(r.error is None for r in results)
        assert results[0].chromadb_id == "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"
        assert results[1].chromadb_id == "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00002"

        client.close()

    def test_apply_label_updates_with_snapshot_data(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test label updates using record snapshot data."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data
        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                    "invalid_labels": "",
                }
            ],
        )

        # Create label update with complete snapshot data
        update_msg = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="TOTAL",
            event_name="MODIFY",
            changes={},
            record_snapshot={
                "validation_status": "VALID",
                "label_confidence": 0.95,
                "validated_by": "user@example.com",
                "valid_labels": "TOTAL",
            },
        )

        # Apply label updates
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[update_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Verify results
        assert len(results) == 1
        assert results[0].error is None

        client.close()

    def test_apply_label_updates_collection_not_found(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test handling when collection doesn't exist."""
        # Create an empty ChromaDB client
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Create label update message
        update_msg = create_label_message(
            image_id="test-id",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="TOTAL",
            event_name="MODIFY",
        )

        # Apply label updates to non-existent collection
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[update_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Should return empty results when collection not found
        assert len(results) == 0

        client.close()

    def test_apply_label_updates_with_error_handling(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test error handling when label update fails."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data
        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Word"}],
        )

        # Create an invalid label message (missing required fields)
        from receipt_dynamo_stream.models import StreamMessage
        from datetime import datetime

        invalid_msg = StreamMessage(
            entity_type="RECEIPT_WORD_LABEL",
            entity_data={},  # Missing required fields
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.WORDS,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="invalid-record",
            aws_region="us-east-1",
        )

        # Apply label updates
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[invalid_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Should have error result
        assert len(results) == 1
        assert results[0].error is not None
        assert results[0].updated_count == 0
        assert results[0].chromadb_id == "unknown"

        client.close()

    def test_apply_label_updates_no_messages(self, temp_chromadb_dir, mock_logger):
        """Test applying label updates with no messages."""
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Create words collection
        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Word"}],
        )

        # Apply with empty message list
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Should return empty results
        assert len(results) == 0

        client.close()

    def test_apply_label_updates_chromadb_id_construction(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test that ChromaDB ID is constructed correctly with zero-padding."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        # Add initial data
        client.upsert(
            collection_name="words",
            ids=["IMAGE#img-99#RECEIPT#00123#LINE#00456#WORD#00789"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Word"}],
        )

        # Create label update message
        update_msg = create_label_message(
            image_id="img-99",
            receipt_id=123,
            line_id=456,
            word_id=789,
            label="TEST",
            event_name="MODIFY",
        )

        # Apply label updates
        results = apply_label_updates(
            chroma_client=client,
            label_messages=[update_msg],
            collection=ChromaDBCollection.WORDS,
            logger=mock_logger,
        )

        # Verify ChromaDB ID was constructed correctly
        assert len(results) == 1
        assert results[0].chromadb_id == "IMAGE#img-99#RECEIPT#00123#LINE#00456#WORD#00789"

        client.close()
