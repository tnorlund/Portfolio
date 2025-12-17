"""Integration tests for delta merge processing."""

import os
import tarfile
import tempfile

import pytest
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma import ChromaClient
from receipt_chroma.compaction.deltas import merge_compaction_deltas
from tests.helpers.factories import (
    create_compaction_run_message,
    create_mock_logger,
)


@pytest.mark.integration
class TestDeltaMerging:
    """Test delta merge processing with real ChromaDB operations."""

    def test_merge_single_delta_tarball(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test merging a single delta tarball into snapshot.

        Use context managers to properly manage ChromaDB client lifecycle.
        """
        import shutil

        s3_client, bucket_name = mock_s3_bucket_compaction

        delta_dir = tempfile.mkdtemp()
        tarball_path = None

        try:
            # Create delta using context manager for proper cleanup
            with ChromaClient(
                persist_directory=delta_dir, mode="write"
            ) as delta_client:
                # Add delta data
                delta_client.upsert(
                    collection_name="lines",
                    ids=["IMAGE#delta-id#RECEIPT#00001#LINE#00001"],
                    embeddings=[[0.9] * 1536],
                    metadatas=[
                        {"text": "Delta line", "merchant_name": "Delta Merchant"}
                    ],
                )
            # Client is properly closed and resources released here

            # Create tarball from delta directory (after client is closed)
            with tempfile.NamedTemporaryFile(
                suffix=".tar.gz", delete=False
            ) as tf:
                tarball_path = tf.name
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(delta_dir, arcname=".")

            # Upload tarball to S3
            delta_prefix = "deltas/run-123"
            s3_client.upload_file(
                tarball_path, bucket_name, f"{delta_prefix}/delta.tar.gz"
            )

            # Create main snapshot and merge
            with ChromaClient(
                persist_directory=temp_chromadb_dir, mode="write"
            ) as snapshot_client:
                snapshot_client.upsert(
                    collection_name="lines",
                    ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001"],
                    embeddings=[[0.1] * 1536],
                    metadatas=[{"text": "Snapshot line"}],
                )

                # Create compaction run message
                compaction_msg = create_compaction_run_message(
                    image_id="delta-id",
                    receipt_id=1,
                    run_id="run-123",
                    delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
                    event_name="INSERT",
                    collection=ChromaDBCollection.LINES,
                )

                # Merge delta into snapshot
                total_merged, per_run_results = merge_compaction_deltas(
                    chroma_client=snapshot_client,
                    compaction_runs=[compaction_msg],
                    collection=ChromaDBCollection.LINES,
                    logger=mock_logger,
                    bucket=bucket_name,
                )

                # Verify merge results
                assert (
                    total_merged == 1
                ), f"Expected 1 delta merged, got {total_merged}"
                assert len(per_run_results) == 1
                assert per_run_results[0]["run_id"] == "run-123"
                assert per_run_results[0]["merged_count"] == 1

                # Verify delta was merged into snapshot
                collection = snapshot_client.get_collection("lines")
                all_data = collection.get(include=["metadatas"])

                assert len(all_data["ids"]) == 2  # Original + delta
                assert (
                    "IMAGE#delta-id#RECEIPT#00001#LINE#00001"
                    in all_data["ids"]
                )
                assert (
                    "IMAGE#snapshot-id#RECEIPT#00001#LINE#00001"
                    in all_data["ids"]
                )

        finally:
            # Cleanup
            if tarball_path and os.path.exists(tarball_path):
                os.remove(tarball_path)
            shutil.rmtree(delta_dir, ignore_errors=True)

    def test_merge_delta_directory_layout(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test merging delta using directory layout (not tarball).

        Use context managers to properly manage ChromaDB client lifecycle.
        """
        import shutil

        s3_client, bucket_name = mock_s3_bucket_compaction

        delta_dir = tempfile.mkdtemp()

        try:
            # Create delta using context manager for proper cleanup
            with ChromaClient(
                persist_directory=delta_dir, mode="write"
            ) as delta_client:
                # Add delta data
                delta_client.upsert(
                    collection_name="words",
                    ids=[
                        "IMAGE#delta-id#RECEIPT#00001#LINE#00001#WORD#00001",
                        "IMAGE#delta-id#RECEIPT#00001#LINE#00001#WORD#00002",
                    ],
                    embeddings=[[0.8] * 1536, [0.9] * 1536],
                    metadatas=[{"text": "Delta"}, {"text": "Words"}],
                )
            # Client is properly closed and resources released here

            # Upload delta directory to S3 (after client is closed)
            delta_prefix = "deltas/run-456"
            upload_count = 0
            for root, _, files in os.walk(delta_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_path, delta_dir)
                    s3_key = f"{delta_prefix}/{relative_path}"
                    s3_client.upload_file(local_path, bucket_name, s3_key)
                    upload_count += 1

            # Verify files were actually uploaded
            assert upload_count > 0, "No files uploaded for delta"

            # Create main snapshot and merge
            with ChromaClient(
                persist_directory=temp_chromadb_dir, mode="write"
            ) as snapshot_client:
                snapshot_client.upsert(
                    collection_name="words",
                    ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001#WORD#00001"],
                    embeddings=[[0.1] * 1536],
                    metadatas=[{"text": "Snapshot"}],
                )

                # Create compaction run message
                compaction_msg = create_compaction_run_message(
                    image_id="delta-id",
                    receipt_id=1,
                    run_id="run-456",
                    delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
                    event_name="INSERT",
                    collection=ChromaDBCollection.WORDS,
                )

                # Merge delta into snapshot
                total_merged, per_run_results = merge_compaction_deltas(
                    chroma_client=snapshot_client,
                    compaction_runs=[compaction_msg],
                    collection=ChromaDBCollection.WORDS,
                    logger=mock_logger,
                    bucket=bucket_name,
                )

                # Verify merge results
                assert (
                    total_merged == 2
                ), f"Expected 2 deltas merged, got {total_merged}"
                assert len(per_run_results) == 1
                assert per_run_results[0]["merged_count"] == 2

                # Verify delta was merged
                collection = snapshot_client.get_collection("words")
                all_data = collection.get(include=["metadatas"])

                assert len(all_data["ids"]) == 3  # Original + 2 delta vectors

        finally:
            # Cleanup
            shutil.rmtree(delta_dir, ignore_errors=True)

    def test_merge_multiple_deltas(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test merging multiple deltas in a single operation.

        Use context managers to properly manage ChromaDB client lifecycle
        and avoid nested close/reopen patterns that interact poorly with
        ChromaDB issue #5868.
        """
        import shutil

        s3_client, bucket_name = mock_s3_bucket_compaction

        # Track delta directories for cleanup
        delta_dirs = []
        compaction_messages = []

        try:
            # Create main snapshot using context manager
            with ChromaClient(
                persist_directory=temp_chromadb_dir, mode="write"
            ) as snapshot_client:
                snapshot_client.upsert(
                    collection_name="lines",
                    ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001"],
                    embeddings=[[0.1] * 1536],
                    metadatas=[{"text": "Snapshot"}],
                )

                # Create two delta snapshots and upload to S3
                for i, run_id in enumerate(["run-1", "run-2"]):
                    delta_dir = tempfile.mkdtemp()
                    delta_dirs.append(delta_dir)

                    # Use context manager for delta client to ensure proper cleanup
                    with ChromaClient(
                        persist_directory=delta_dir, mode="write"
                    ) as delta_client:
                        # Add unique delta data
                        delta_client.upsert(
                            collection_name="lines",
                            ids=[f"IMAGE#delta-{i}#RECEIPT#00001#LINE#00001"],
                            embeddings=[[0.5 + i * 0.1] * 1536],
                            metadatas=[{"text": f"Delta {i}"}],
                        )
                    # Client is properly closed and resources released here

                    # Upload to S3 (directory layout)
                    delta_prefix = f"deltas/{run_id}"
                    upload_count = 0
                    for root, _, files in os.walk(delta_dir):
                        for file in files:
                            local_path = os.path.join(root, file)
                            relative_path = os.path.relpath(
                                local_path, delta_dir
                            )
                            s3_key = f"{delta_prefix}/{relative_path}"
                            s3_client.upload_file(
                                local_path, bucket_name, s3_key
                            )
                            upload_count += 1

                    # Verify files were actually uploaded
                    assert (
                        upload_count > 0
                    ), f"No files uploaded for delta {i}"

                    # Create compaction message
                    compaction_msg = create_compaction_run_message(
                        image_id=f"delta-{i}",
                        receipt_id=1,
                        run_id=run_id,
                        delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
                        event_name="INSERT",
                        collection=ChromaDBCollection.LINES,
                    )
                    compaction_messages.append(compaction_msg)

                # Merge all deltas
                total_merged, per_run_results = merge_compaction_deltas(
                    chroma_client=snapshot_client,
                    compaction_runs=compaction_messages,
                    collection=ChromaDBCollection.LINES,
                    logger=mock_logger,
                    bucket=bucket_name,
                )

                # Verify merge results
                assert (
                    total_merged == 2
                ), f"Expected 2 deltas merged, got {total_merged}"
                assert (
                    len(per_run_results) == 2
                ), f"Expected 2 run results, got {len(per_run_results)}"

                # Verify all deltas were merged
                collection = snapshot_client.get_collection("lines")
                all_data = collection.get(include=["metadatas"])

                assert len(all_data["ids"]) == 3  # Original + 2 deltas

        finally:
            # Cleanup delta dirs
            for delta_dir in delta_dirs:
                shutil.rmtree(delta_dir, ignore_errors=True)

    def test_merge_deltas_no_messages(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test merging with no compaction run messages."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        snapshot_client = ChromaClient(
            persist_directory=temp_chromadb_dir, mode="write"
        )

        # Merge with empty message list
        total_merged, per_run_results = merge_compaction_deltas(
            chroma_client=snapshot_client,
            compaction_runs=[],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            bucket=bucket_name,
        )

        # Should return zero results
        assert total_merged == 0
        assert len(per_run_results) == 0

        snapshot_client.close()

    def test_merge_deltas_missing_s3_files(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test handling when delta files are missing from S3."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        snapshot_client = ChromaClient(
            persist_directory=temp_chromadb_dir, mode="write"
        )
        snapshot_client.upsert(
            collection_name="lines",
            ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Snapshot"}],
        )

        # Create compaction message pointing to non-existent delta
        compaction_msg = create_compaction_run_message(
            image_id="missing",
            receipt_id=1,
            run_id="run-missing",
            delta_s3_prefix=f"s3://{bucket_name}/nonexistent/path/",
            event_name="INSERT",
            collection=ChromaDBCollection.LINES,
        )

        # Merge should handle missing files gracefully
        total_merged, per_run_results = merge_compaction_deltas(
            chroma_client=snapshot_client,
            compaction_runs=[compaction_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            bucket=bucket_name,
        )

        # Should not merge anything
        assert total_merged == 0
        assert len(per_run_results) == 0

        snapshot_client.close()

    def test_merge_deltas_without_delta_prefix(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test handling when compaction message lacks delta_s3_prefix."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        snapshot_client = ChromaClient(
            persist_directory=temp_chromadb_dir, mode="write"
        )

        # Create compaction message without delta_s3_prefix
        from datetime import datetime

        from receipt_dynamo_stream.models import StreamMessage

        compaction_msg = StreamMessage(
            entity_type="COMPACTION_RUN",
            entity_data={
                "image_id": "test",
                "receipt_id": 1,
                "run_id": "run-123",
                # Missing delta_s3_prefix
            },
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="record-123",
            aws_region="us-east-1",
        )

        # Should skip this message
        total_merged, per_run_results = merge_compaction_deltas(
            chroma_client=snapshot_client,
            compaction_runs=[compaction_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            bucket=bucket_name,
        )

        assert total_merged == 0
        assert len(per_run_results) == 0

        snapshot_client.close()

    def test_merge_deltas_delta_has_no_collection(
        self, mock_s3_bucket_compaction, temp_chromadb_dir, mock_logger
    ):
        """Test handling when delta has no matching collection."""
        s3_client, bucket_name = mock_s3_bucket_compaction

        # Create a delta without the expected collection
        delta_dir = tempfile.mkdtemp()
        delta_client = ChromaClient(persist_directory=delta_dir, mode="write")

        # Add data to wrong collection
        delta_client.upsert(
            collection_name="other_collection",
            ids=["test-id"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Wrong collection"}],
        )
        delta_client.close()

        # Upload to S3
        delta_prefix = "deltas/run-wrong-collection"
        for root, _, files in os.walk(delta_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, delta_dir)
                s3_key = f"{delta_prefix}/{relative_path}"
                s3_client.upload_file(local_path, bucket_name, s3_key)

        # Create main snapshot
        snapshot_client = ChromaClient(
            persist_directory=temp_chromadb_dir, mode="write"
        )
        snapshot_client.upsert(
            collection_name="lines",
            ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001"],
            embeddings=[[0.1] * 1536],
            metadatas=[{"text": "Snapshot"}],
        )

        # Create compaction message
        compaction_msg = create_compaction_run_message(
            image_id="test",
            receipt_id=1,
            run_id="run-wrong-collection",
            delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
            event_name="INSERT",
            collection=ChromaDBCollection.LINES,
        )

        # Should handle gracefully (no merge)
        total_merged, per_run_results = merge_compaction_deltas(
            chroma_client=snapshot_client,
            compaction_runs=[compaction_msg],
            collection=ChromaDBCollection.LINES,
            logger=mock_logger,
            bucket=bucket_name,
        )

        # No vectors should be merged
        assert total_merged == 0

        snapshot_client.close()

        # Cleanup
        import shutil

        shutil.rmtree(delta_dir, ignore_errors=True)
