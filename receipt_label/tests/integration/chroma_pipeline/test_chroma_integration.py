"""Integration tests for ChromaDB pipeline."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_compactor import ChromaCompactor 
from receipt_label.utils.chroma_s3_helpers import produce_embedding_delta, consume_latest_snapshot
from receipt_label.tests.markers import integration, chroma, aws


@integration
@chroma
class TestChromaIntegration:
    """Test ChromaDB integration workflows."""

    @pytest.fixture
    def temp_chroma_dir(self):
        """Temporary directory for ChromaDB persistence."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def chroma_client(self, temp_chroma_dir):
        """ChromaDB client with temporary persistence."""
        return ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )

    @pytest.fixture
    def sample_embeddings(self):
        """Sample embedding data for testing."""
        return {
            "ids": ["test_id_1", "test_id_2", "test_id_3"],
            "embeddings": [
                [0.1] * 1536,  # OpenAI embedding dimension
                [0.2] * 1536,
                [0.3] * 1536
            ],
            "documents": ["Walmart", "$12.99", "receipt text"],
            "metadatas": [
                {"valid_labels": ["MERCHANT_NAME"], "invalid_labels": []},
                {"valid_labels": ["CURRENCY"], "invalid_labels": []},
                {"valid_labels": [], "invalid_labels": ["NOISE"]}
            ]
        }

    def test_basic_chroma_operations(self, chroma_client, sample_embeddings):
        """Test basic ChromaDB CRUD operations."""
        collection_name = "words"
        
        # Test upsert
        chroma_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_embeddings["ids"],
            embeddings=sample_embeddings["embeddings"],
            documents=sample_embeddings["documents"],
            metadatas=sample_embeddings["metadatas"]
        )
        
        # Test get by IDs
        results = chroma_client.get_by_ids(
            collection_name, 
            ["test_id_1", "test_id_2"],
            include=["embeddings", "metadatas", "documents"]
        )
        
        assert len(results["ids"]) == 2
        assert "test_id_1" in results["ids"]
        assert "test_id_2" in results["ids"]
        
        # Test query
        query_results = chroma_client.query_collection(
            collection_name=collection_name,
            query_embeddings=[sample_embeddings["embeddings"][0]],
            n_results=2,
            include=["distances", "metadatas"]
        )
        
        assert len(query_results["ids"][0]) == 2
        assert query_results["distances"][0][0] < 0.1  # Should be very similar to itself

    def test_metadata_updates(self, chroma_client, sample_embeddings):
        """Test metadata update operations."""
        collection_name = "words"
        
        # Initial upsert
        chroma_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_embeddings["ids"][:1],
            embeddings=sample_embeddings["embeddings"][:1], 
            documents=sample_embeddings["documents"][:1],
            metadatas=sample_embeddings["metadatas"][:1]
        )
        
        # Update metadata
        collection = chroma_client.get_collection(collection_name)
        updated_metadata = {
            "valid_labels": ["MERCHANT_NAME", "COMPANY_NAME"],
            "invalid_labels": ["NOISE"],
            "confidence": 0.95
        }
        
        collection.update(
            ids=["test_id_1"],
            metadatas=[updated_metadata]
        )
        
        # Verify update
        results = chroma_client.get_by_ids(
            collection_name,
            ["test_id_1"],
            include=["metadatas"]
        )
        
        metadata = results["metadatas"][0]
        assert "COMPANY_NAME" in metadata["valid_labels"]
        assert "NOISE" in metadata["invalid_labels"] 
        assert metadata["confidence"] == 0.95

    @pytest.mark.parametrize("collection_name,data_type", [
        ("words", "word_embeddings"),
        ("lines", "line_embeddings"),
        ("receipts", "receipt_embeddings")
    ])
    def test_multi_collection_support(self, chroma_client, sample_embeddings, collection_name, data_type):
        """Test support for multiple collections."""
        # Modify metadata to reflect data type
        typed_metadata = [{**meta, "data_type": data_type} for meta in sample_embeddings["metadatas"]]
        
        chroma_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_embeddings["ids"],
            embeddings=sample_embeddings["embeddings"],
            documents=sample_embeddings["documents"],
            metadatas=typed_metadata
        )
        
        results = chroma_client.get_by_ids(
            collection_name,
            sample_embeddings["ids"],
            include=["metadatas"]
        )
        
        assert len(results["ids"]) == 3
        for metadata in results["metadatas"]:
            assert metadata["data_type"] == data_type

    def test_persistence_across_restarts(self, temp_chroma_dir, sample_embeddings):
        """Test data persistence across client restarts."""
        collection_name = "words"
        
        # First client instance - write data
        client1 = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        client1.upsert_vectors(
            collection_name=collection_name,
            ids=sample_embeddings["ids"],
            embeddings=sample_embeddings["embeddings"],
            documents=sample_embeddings["documents"],
            metadatas=sample_embeddings["metadatas"]
        )
        
        # Force persistence
        client1.persist()
        
        # Second client instance - read data
        client2 = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        results = client2.get_by_ids(
            collection_name,
            sample_embeddings["ids"],
            include=["documents", "metadatas"]
        )
        
        assert len(results["ids"]) == 3
        assert set(results["documents"]) == set(sample_embeddings["documents"])


@integration
@chroma 
@aws
class TestChromaS3Pipeline:
    """Test ChromaDB S3 compaction pipeline."""

    @pytest.fixture
    def mock_s3_client(self):
        """Mock S3 client for testing."""
        client = Mock()
        client.upload_fileobj.return_value = None
        client.download_fileobj.return_value = None
        client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "delta/uuid1/", "LastModified": "2023-01-01T00:00:00Z"},
                {"Key": "delta/uuid2/", "LastModified": "2023-01-02T00:00:00Z"}
            ]
        }
        return client

    @pytest.fixture 
    def mock_dynamo_client(self):
        """Mock DynamoDB client for compaction locks."""
        client = Mock()
        # Mock successful lock acquisition
        client.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        client.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        return client

    @pytest.fixture
    def temp_delta_dir(self):
        """Temporary directory for delta storage."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_delta_production(self, temp_delta_dir, mock_s3_client, sample_embeddings):
        """Test producing embedding deltas."""
        with patch('boto3.client', return_value=mock_s3_client):
            result = produce_embedding_delta(
                ids=sample_embeddings["ids"],
                embeddings=sample_embeddings["embeddings"],
                documents=sample_embeddings["documents"],
                metadatas=sample_embeddings["metadatas"],
                bucket="test-bucket",
                delta_prefix="delta/",
                local_temp_dir=temp_delta_dir
            )
        
        assert "delta_key" in result
        assert result["delta_key"].startswith("delta/")
        assert "item_count" in result
        assert result["item_count"] == 3
        
        # Verify S3 upload was called
        mock_s3_client.upload_fileobj.assert_called_once()

    def test_compaction_workflow(self, mock_s3_client, mock_dynamo_client, temp_delta_dir):
        """Test full compaction workflow.""" 
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        delta_keys = ["delta/uuid1/", "delta/uuid2/"]
        
        with patch('boto3.client', return_value=mock_s3_client):
            with patch.object(compactor, '_download_current_snapshot') as mock_download:
                with patch.object(compactor, '_merge_deltas') as mock_merge:
                    with patch.object(compactor, '_upload_new_snapshot') as mock_upload:
                        
                        # Mock the compaction steps
                        mock_download.return_value = temp_delta_dir
                        mock_merge.return_value = {"merged_count": 150}
                        mock_upload.return_value = {"snapshot_key": "snapshot/2023-01-01/"}
                        
                        result = compactor.compact_deltas(delta_keys)
        
        assert result["status"] == "completed"
        assert result["deltas_processed"] == 2
        assert "snapshot_key" in result
        
        # Verify lock operations
        mock_dynamo_client.put_item.assert_called()  # Lock acquire
        mock_dynamo_client.delete_item.assert_called()  # Lock release

    def test_concurrent_compaction_prevention(self, mock_dynamo_client, temp_delta_dir):
        """Test prevention of concurrent compactions."""
        # Mock lock acquisition failure (already exists)
        from botocore.exceptions import ClientError
        mock_dynamo_client.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem"
        )
        
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        result = compactor.compact_deltas(["delta/uuid1/"])
        
        assert result["status"] == "skipped"
        assert "lock_busy" in result["reason"]

    def test_snapshot_consumption(self, mock_s3_client, temp_delta_dir):
        """Test consuming latest snapshot for queries."""
        with patch('boto3.client', return_value=mock_s3_client):
            result = consume_latest_snapshot(
                bucket="test-bucket",
                snapshot_prefix="snapshot/",
                local_mount_dir=temp_delta_dir
            )
        
        assert "snapshot_path" in result
        assert result["snapshot_path"] == temp_delta_dir
        
        # Verify S3 download was called
        mock_s3_client.download_fileobj.assert_called()

    @pytest.mark.parametrize("failure_scenario", [
        "s3_upload_failure",
        "dynamo_lock_timeout", 
        "merge_corruption",
        "network_interruption"
    ])
    def test_failure_recovery(self, failure_scenario, mock_s3_client, mock_dynamo_client):
        """Test recovery from various failure scenarios."""
        compactor = ChromaCompactor(
            dynamo_client=mock_dynamo_client,
            bucket="test-bucket"
        )
        
        # Simulate different failures
        if failure_scenario == "s3_upload_failure":
            mock_s3_client.upload_fileobj.side_effect = Exception("S3 upload failed")
        elif failure_scenario == "dynamo_lock_timeout":
            mock_dynamo_client.put_item.side_effect = Exception("DynamoDB timeout")
        elif failure_scenario == "merge_corruption":
            with patch.object(compactor, '_merge_deltas', side_effect=Exception("Merge failed")):
                result = compactor.compact_deltas(["delta/uuid1/"])
        elif failure_scenario == "network_interruption":
            mock_s3_client.download_fileobj.side_effect = Exception("Network error")
        
        with patch('boto3.client', return_value=mock_s3_client):
            result = compactor.compact_deltas(["delta/uuid1/"])
        
        # Should handle failures gracefully
        assert result["status"] in ["failed", "error"]
        assert "error" in result
        
        # Lock should still be released on failure
        if failure_scenario != "dynamo_lock_timeout":
            mock_dynamo_client.delete_item.assert_called()


@integration
@chroma
class TestChromaPerformance:
    """Test ChromaDB performance characteristics."""

    def test_large_batch_operations(self, chroma_client):
        """Test performance with large batches."""
        import time
        
        # Generate large batch of embeddings
        batch_size = 1000
        ids = [f"test_id_{i}" for i in range(batch_size)]
        embeddings = [[0.1] * 1536 for _ in range(batch_size)]
        documents = [f"document_{i}" for i in range(batch_size)]
        metadatas = [{"index": i} for i in range(batch_size)]
        
        # Time the upsert
        start_time = time.time()
        chroma_client.upsert_vectors(
            collection_name="words",
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        upsert_time = time.time() - start_time
        
        # Should complete within reasonable time
        assert upsert_time < 30.0  # 30 seconds for 1000 items
        
        # Time the query
        start_time = time.time()
        results = chroma_client.query_collection(
            collection_name="words",
            query_embeddings=[embeddings[0]],
            n_results=10
        )
        query_time = time.time() - start_time
        
        # Query should be fast
        assert query_time < 1.0  # 1 second for similarity search
        assert len(results["ids"][0]) == 10

    def test_concurrent_access(self, temp_chroma_dir):
        """Test concurrent access to ChromaDB."""
        import threading
        import time
        
        def worker_function(worker_id, results_dict):
            """Worker function for concurrent testing."""
            try:
                client = ChromaDBClient(
                    persist_directory=temp_chroma_dir,
                    use_persistent_client=True
                )
                
                # Each worker inserts some data
                worker_ids = [f"worker_{worker_id}_item_{i}" for i in range(10)]
                worker_embeddings = [[worker_id * 0.1] * 1536 for _ in range(10)]
                worker_documents = [f"worker {worker_id} doc {i}" for i in range(10)]
                worker_metadatas = [{"worker_id": worker_id, "item": i} for i in range(10)]
                
                client.upsert_vectors(
                    collection_name="words",
                    ids=worker_ids,
                    embeddings=worker_embeddings,
                    documents=worker_documents,
                    metadatas=worker_metadatas
                )
                
                results_dict[worker_id] = "success"
                
            except Exception as e:
                results_dict[worker_id] = f"error: {e}"
        
        # Start multiple workers
        threads = []
        results = {}
        
        for i in range(5):
            thread = threading.Thread(target=worker_function, args=(i, results))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join(timeout=30)  # 30 second timeout
        
        # All workers should succeed
        for worker_id, result in results.items():
            assert result == "success", f"Worker {worker_id} failed: {result}"
        
        # Verify all data was inserted
        final_client = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        all_results = final_client.get_by_ids(
            "words",
            [f"worker_{i}_item_{j}" for i in range(5) for j in range(10)],
            include=["metadatas"]
        )
        
        assert len(all_results["ids"]) == 50  # 5 workers × 10 items each