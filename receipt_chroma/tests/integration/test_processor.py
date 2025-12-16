"""Integration tests for the main compaction processor."""

import os
import tempfile

import pytest
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma import ChromaClient
from receipt_chroma.compaction import process_collection_updates
from tests.helpers.factories import (
    create_metadata_message,
    create_label_message,
    create_compaction_run_message,
    create_mock_logger,
    create_mock_metrics,
)


@pytest.mark.integration
class TestProcessor:
    """Test the main compaction processor."""

    def test_process_collection_updates_metadata_only(
        self, temp_chromadb_dir, mock_logger, mock_metrics
    ):
        """Test processing only metadata updates."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        client.upsert(
            collection_name="lines",
            ids=[
                "IMAGE#test-id#RECEIPT#00001#LINE#00001",
                "IMAGE#test-id#RECEIPT#00001#LINE#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Line 1", "merchant_name": "Old Merchant"},
                {"text": "Line 2", "merchant_name": "Old Merchant"},
            ],
        )

        # Create metadata update message
        from receipt_dynamo_stream.models import FieldChange

        metadata_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Old Merchant", new="New Merchant")
            },
            collections=(ChromaDBCollection.LINES,),
        )

        # Process updates
        result = process_collection_updates(
            stream_messages=[metadata_msg],
            collection=ChromaDBCollection.LINES,
            chroma_client=client,
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify result
        assert result.collection == ChromaDBCollection.LINES
        assert result.total_metadata_updated == 2
        assert result.total_labels_updated == 0
        assert result.delta_merge_count == 0
        assert not result.has_errors

        # Verify metadata was updated
        collection = client.get_collection("lines")
        data = collection.get(
            ids=[
                "IMAGE#test-id#RECEIPT#00001#LINE#00001",
                "IMAGE#test-id#RECEIPT#00001#LINE#00002",
            ]
        )

        assert data["metadatas"][0]["merchant_name"] == "New Merchant"
        assert data["metadatas"][1]["merchant_name"] == "New Merchant"

        # Verify metrics were recorded
        mock_metrics.count.assert_called()
        mock_metrics.gauge.assert_called()

        client.close()

    def test_process_collection_updates_labels_only(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test processing only label updates."""
        # Create a ChromaDB snapshot with test data
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                }
            ],
        )

        # Create label update message
        from receipt_dynamo_stream.models import FieldChange

        label_msg = create_label_message(
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

        # Process updates
        result = process_collection_updates(
            stream_messages=[label_msg],
            collection=ChromaDBCollection.WORDS,
            chroma_client=client,
            logger=mock_logger,
        )

        # Verify result
        assert result.collection == ChromaDBCollection.WORDS
        assert result.total_metadata_updated == 0
        assert result.total_labels_updated == 1
        assert result.delta_merge_count == 0
        assert not result.has_errors

        client.close()

    def test_process_collection_updates_mixed_messages(
        self, temp_chromadb_dir, mock_logger, mock_s3_bucket_compaction, monkeypatch
    ):
        """Test processing a mix of metadata, label, and delta messages."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Set environment variable for S3 bucket
        monkeypatch.setenv("CHROMADB_BUCKET", bucket_name)

        # Create a delta and upload to S3
        delta_dir = tempfile.mkdtemp()
        delta_client = ChromaClient(persist_directory=delta_dir, mode="write")
        delta_client.upsert(
            collection_name="lines",
            ids=["IMAGE#delta-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.9] * 1536],
            metadatas=[{"text": "Delta line"}],
        )
        delta_client.close()

        # Upload delta to S3
        delta_prefix = "deltas/run-123"
        for root, dirs, files in os.walk(delta_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, delta_dir)
                s3_key = f"{delta_prefix}/{relative_path}"
                s3_client.upload_file(local_path, bucket_name, s3_key)

        # Create main snapshot
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")
        client.upsert(
            collection_name="lines",
            ids=[
                "IMAGE#test-id#RECEIPT#00001#LINE#00001",
                "IMAGE#test-id#RECEIPT#00001#LINE#00002",
            ],
            embeddings=[[0.1] * 1536, [0.2] * 1536],
            metadatas=[
                {"text": "Line 1", "merchant_name": "Old Merchant"},
                {"text": "Line 2", "merchant_name": "Old Merchant"},
            ],
        )

        # Create mixed messages
        from receipt_dynamo_stream.models import FieldChange

        metadata_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Old Merchant", new="New Merchant")
            },
            collections=(ChromaDBCollection.LINES,),
        )

        delta_msg = create_compaction_run_message(
            image_id="delta-id",
            receipt_id=1,
            run_id="run-123",
            delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
            event_name="INSERT",
            collection=ChromaDBCollection.LINES,
        )

        # Process all updates
        result = process_collection_updates(
            stream_messages=[metadata_msg, delta_msg],
            collection=ChromaDBCollection.LINES,
            chroma_client=client,
            logger=mock_logger,
        )

        # Verify result
        assert result.total_metadata_updated == 2  # Updated 2 existing lines
        assert result.delta_merge_count == 1  # Merged 1 delta
        assert not result.has_errors

        # Verify all operations completed
        collection = client.get_collection("lines")
        all_data = collection.get(include=["metadatas"])

        # Should have 3 embeddings total (2 original + 1 delta)
        assert len(all_data["ids"]) == 3

        # Metadata should be updated on original lines
        for metadata in all_data["metadatas"]:
            if "merchant_name" in metadata:
                assert metadata["merchant_name"] == "New Merchant"

        client.close()

        # Cleanup
        import shutil

        shutil.rmtree(delta_dir, ignore_errors=True)

    def test_process_collection_updates_with_errors(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test processing with errors in some messages."""
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        client.upsert(
            collection_name="lines",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Line 1"}],
        )

        # Create a valid message and an invalid message
        from receipt_dynamo_stream.models import FieldChange

        valid_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
            changes={"merchant_name": FieldChange(old="A", new="B")},
            collections=(ChromaDBCollection.LINES,),
        )

        # Create invalid message (missing required fields)
        from receipt_dynamo_stream.models import StreamMessage
        from datetime import datetime

        invalid_msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={},  # Missing image_id and receipt_id
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="invalid",
            aws_region="us-east-1",
        )

        # Process updates
        result = process_collection_updates(
            stream_messages=[valid_msg, invalid_msg],
            collection=ChromaDBCollection.LINES,
            chroma_client=client,
            logger=mock_logger,
        )

        # Should have errors
        assert result.has_errors
        assert len(result.metadata_updates) == 2

        # One should be successful, one should have error
        successful = [r for r in result.metadata_updates if r.error is None]
        failed = [r for r in result.metadata_updates if r.error is not None]

        assert len(successful) == 1
        assert len(failed) == 1

        client.close()

    def test_process_collection_updates_empty_messages(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test processing with no messages."""
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        client.upsert(
            collection_name="lines",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Line 1"}],
        )

        # Process empty message list
        result = process_collection_updates(
            stream_messages=[],
            collection=ChromaDBCollection.LINES,
            chroma_client=client,
            logger=mock_logger,
        )

        # Should have no updates
        assert result.total_metadata_updated == 0
        assert result.total_labels_updated == 0
        assert result.delta_merge_count == 0
        assert not result.has_errors

        client.close()

    def test_process_collection_updates_result_to_dict(
        self, temp_chromadb_dir, mock_logger
    ):
        """Test that CollectionUpdateResult can be serialized to dict."""
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

        client.upsert(
            collection_name="lines",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Line 1", "merchant_name": "Old"}],
        )

        # Create metadata update message
        from receipt_dynamo_stream.models import FieldChange

        metadata_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
            changes={"merchant_name": FieldChange(old="Old", new="New")},
            collections=(ChromaDBCollection.LINES,),
        )

        # Process updates
        result = process_collection_updates(
            stream_messages=[metadata_msg],
            collection=ChromaDBCollection.LINES,
            chroma_client=client,
            logger=mock_logger,
        )

        # Convert to dict
        result_dict = result.to_dict()

        # Verify dict structure
        assert "collection" in result_dict
        assert "metadata_updates" in result_dict
        assert "label_updates" in result_dict
        assert "delta_merge_count" in result_dict
        assert "delta_merge_results" in result_dict

        assert result_dict["collection"] == "lines"
        assert result_dict["delta_merge_count"] == 0
        assert len(result_dict["metadata_updates"]) == 1

        client.close()

    def test_process_collection_updates_processing_order(
        self, temp_chromadb_dir, mock_logger, mock_s3_bucket_compaction, monkeypatch
    ):
        """Test that updates are processed in correct order: deltas → metadata → labels."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Set environment variable for S3 bucket
        monkeypatch.setenv("CHROMADB_BUCKET", bucket_name)

        # Create a delta with new word
        delta_dir = tempfile.mkdtemp()
        delta_client = ChromaClient(persist_directory=delta_dir, mode="write")
        delta_client.upsert(
            collection_name="words",
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.9] * 1536],
            metadatas=[
                {
                    "text": "Total",
                    "label_status": "auto_suggested",
                    "valid_labels": "",
                    "merchant_name": "Initial",
                }
            ],
        )
        delta_client.close()

        # Upload delta to S3
        delta_prefix = "deltas/run-order"
        for root, dirs, files in os.walk(delta_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, delta_dir)
                s3_key = f"{delta_prefix}/{relative_path}"
                s3_client.upload_file(local_path, bucket_name, s3_key)

        # Create empty snapshot
        client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")
        client.upsert(
            collection_name="words",
            ids=["IMAGE#placeholder#RECEIPT#00001#LINE#00001#WORD#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Placeholder"}],
        )

        # Create messages in mixed order (intentionally not processing order)
        from receipt_dynamo_stream.models import FieldChange

        # Label update for the word from delta
        label_msg = create_label_message(
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

        # Metadata update for the receipt
        metadata_msg = create_metadata_message(
            image_id="test-id",
            receipt_id=1,
            event_name="MODIFY",
            changes={
                "merchant_name": FieldChange(old="Initial", new="Updated Merchant")
            },
            collections=(ChromaDBCollection.WORDS,),
        )

        # Delta message
        delta_msg = create_compaction_run_message(
            image_id="test-id",
            receipt_id=1,
            run_id="run-order",
            delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
            event_name="INSERT",
            collection=ChromaDBCollection.WORDS,
        )

        # Submit messages in wrong order (label, metadata, delta)
        # Processor should reorder them correctly
        result = process_collection_updates(
            stream_messages=[label_msg, metadata_msg, delta_msg],
            collection=ChromaDBCollection.WORDS,
            chroma_client=client,
            logger=mock_logger,
        )

        # Verify all operations completed
        assert result.delta_merge_count == 1
        assert result.total_metadata_updated >= 1
        assert result.total_labels_updated == 1

        # Verify the delta was merged first, then metadata and labels applied
        collection = client.get_collection("words")
        word_data = collection.get(
            ids=["IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001"]
        )

        # Should have updated merchant name (from metadata)
        assert word_data["metadatas"][0]["merchant_name"] == "Updated Merchant"

        client.close()

        # Cleanup
        import shutil

        shutil.rmtree(delta_dir, ignore_errors=True)
