"""Unit tests for ChromaDBClient core functionality."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, call

from receipt_label.utils.chroma_client import ChromaDBClient
from tests.markers import unit, fast, chroma
import chromadb
from chromadb.config import Settings


@unit
@fast
@chroma
class TestChromaDBClientCore:
    """Test core ChromaDB client functionality."""

    @pytest.fixture
    def temp_chroma_dir(self):
        """Temporary directory for ChromaDB persistence."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def in_memory_client(self):
        """In-memory ChromaDB client for fast testing."""
        return ChromaDBClient(use_persistent_client=False)

    @pytest.fixture
    def persistent_client(self, temp_chroma_dir):
        """Persistent ChromaDB client for testing."""
        return ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )

    @pytest.fixture
    def sample_vectors(self):
        """Sample vector data for testing."""
        return {
            "ids": ["vec_1", "vec_2", "vec_3"],
            "embeddings": [
                [0.1, 0.2, 0.3] * 512,  # 1536 dimensions
                [0.4, 0.5, 0.6] * 512,
                [0.7, 0.8, 0.9] * 512
            ],
            "documents": ["test document 1", "test document 2", "test document 3"],
            "metadatas": [
                {"label": "MERCHANT_NAME", "confidence": 0.95},
                {"label": "CURRENCY", "confidence": 0.90},
                {"label": "DATE", "confidence": 0.85}
            ]
        }

    def test_client_initialization_in_memory(self, in_memory_client):
        """Test client initializes correctly in memory mode."""
        assert in_memory_client is not None
        assert in_memory_client._use_persistent is False
        assert hasattr(in_memory_client, '_client')
        
        # Should be able to access client
        client = in_memory_client._get_client()
        assert client is not None

    def test_client_initialization_persistent(self, persistent_client, temp_chroma_dir):
        """Test client initializes correctly in persistent mode."""
        assert persistent_client is not None
        assert persistent_client._use_persistent is True
        assert persistent_client._persist_directory == temp_chroma_dir
        
        # Should create directory if it doesn't exist
        assert Path(temp_chroma_dir).exists()

    def test_collection_creation_and_retrieval(self, in_memory_client):
        """Test collection creation and retrieval."""
        collection_name = "test_words"
        
        # Create collection
        collection = in_memory_client.get_collection(collection_name)
        assert collection is not None
        assert collection.name == collection_name
        
        # Should be able to retrieve same collection
        same_collection = in_memory_client.get_collection(collection_name)
        assert same_collection.name == collection_name

    def test_upsert_vectors_basic(self, in_memory_client, sample_vectors):
        """Test basic vector upsert functionality.""" 
        collection_name = "words"
        
        # Upsert vectors
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_vectors["ids"],
            embeddings=sample_vectors["embeddings"],
            documents=sample_vectors["documents"],
            metadatas=sample_vectors["metadatas"]
        )
        
        # Verify vectors were added
        collection = in_memory_client.get_collection(collection_name)
        count = collection.count()
        assert count == 3

    def test_get_by_ids_functionality(self, in_memory_client, sample_vectors):
        """Test retrieving vectors by IDs."""
        collection_name = "words"
        
        # First upsert some data
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_vectors["ids"],
            embeddings=sample_vectors["embeddings"],
            documents=sample_vectors["documents"],
            metadatas=sample_vectors["metadatas"]
        )
        
        # Get by specific IDs
        results = in_memory_client.get_by_ids(
            collection_name,
            ["vec_1", "vec_3"],
            include=["embeddings", "metadatas", "documents"]
        )
        
        assert len(results["ids"]) == 2
        assert "vec_1" in results["ids"]
        assert "vec_3" in results["ids"]
        assert len(results["embeddings"]) == 2
        assert len(results["metadatas"]) == 2
        assert len(results["documents"]) == 2

    def test_get_by_ids_missing_vectors(self, in_memory_client, sample_vectors):
        """Test behavior when requesting non-existent IDs."""
        collection_name = "words"
        
        # Upsert some data
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_vectors["ids"][:2],  # Only first 2 vectors
            embeddings=sample_vectors["embeddings"][:2],
            documents=sample_vectors["documents"][:2],
            metadatas=sample_vectors["metadatas"][:2]
        )
        
        # Request including non-existent ID
        results = in_memory_client.get_by_ids(
            collection_name,
            ["vec_1", "vec_nonexistent", "vec_2"],
            include=["metadatas", "documents"]
        )
        
        # Should only return existing vectors
        assert len(results["ids"]) == 2
        assert "vec_1" in results["ids"]
        assert "vec_2" in results["ids"]
        assert "vec_nonexistent" not in results["ids"]

    def test_query_collection_similarity_search(self, in_memory_client, sample_vectors):
        """Test similarity search functionality."""
        collection_name = "words"
        
        # Upsert vectors
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_vectors["ids"],
            embeddings=sample_vectors["embeddings"],
            documents=sample_vectors["documents"],
            metadatas=sample_vectors["metadatas"]
        )
        
        # Query with similar vector
        query_embedding = [0.1, 0.2, 0.3] * 512  # Similar to vec_1
        
        results = in_memory_client.query_collection(
            collection_name=collection_name,
            query_embeddings=[query_embedding],
            n_results=2,
            include=["distances", "metadatas"]
        )
        
        assert len(results["ids"]) == 1  # One query
        assert len(results["ids"][0]) == 2  # Two results per query
        assert len(results["distances"]) == 1
        assert len(results["distances"][0]) == 2
        
        # First result should be most similar (vec_1)
        assert results["ids"][0][0] == "vec_1"
        assert results["distances"][0][0] < results["distances"][0][1]

    def test_query_collection_with_metadata_filter(self, in_memory_client, sample_vectors):
        """Test similarity search with metadata filtering."""
        collection_name = "words"
        
        # Upsert vectors
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=sample_vectors["ids"],
            embeddings=sample_vectors["embeddings"],
            documents=sample_vectors["documents"],
            metadatas=sample_vectors["metadatas"]
        )
        
        # Query with metadata filter
        query_embedding = [0.5, 0.5, 0.5] * 512
        
        results = in_memory_client.query_collection(
            collection_name=collection_name,
            query_embeddings=[query_embedding],
            n_results=3,
            where={"label": "CURRENCY"},
            include=["metadatas"]
        )
        
        # Should only return vectors matching filter
        assert len(results["ids"]) == 1
        assert len(results["ids"][0]) == 1  # Only one CURRENCY vector
        assert results["ids"][0][0] == "vec_2"
        assert results["metadatas"][0][0]["label"] == "CURRENCY"

    def test_upsert_duplicate_ids_update(self, in_memory_client):
        """Test that upserting duplicate IDs updates existing vectors."""
        collection_name = "words"
        
        # Initial upsert
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=["vec_1"],
            embeddings=[[0.1, 0.2, 0.3] * 512],
            documents=["original document"],
            metadatas=[{"original": True}]
        )
        
        # Upsert same ID with different data
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=["vec_1"],
            embeddings=[[0.9, 0.8, 0.7] * 512],
            documents=["updated document"],
            metadatas=[{"updated": True}]
        )
        
        # Should still have only one vector, but updated
        collection = in_memory_client.get_collection(collection_name)
        assert collection.count() == 1
        
        results = in_memory_client.get_by_ids(
            collection_name,
            ["vec_1"],
            include=["documents", "metadatas"]
        )
        
        assert results["documents"][0] == "updated document"
        assert results["metadatas"][0]["updated"] is True
        assert "original" not in results["metadatas"][0]

    def test_empty_collection_handling(self, in_memory_client):
        """Test operations on empty collections."""
        collection_name = "empty_words"
        
        # Get empty collection
        collection = in_memory_client.get_collection(collection_name)
        assert collection.count() == 0
        
        # Query empty collection
        results = in_memory_client.query_collection(
            collection_name=collection_name,
            query_embeddings=[[0.1, 0.2, 0.3] * 512],
            n_results=5
        )
        
        assert len(results["ids"]) == 1
        assert len(results["ids"][0]) == 0  # No results from empty collection
        
        # Get by IDs from empty collection
        results = in_memory_client.get_by_ids(
            collection_name,
            ["nonexistent"],
            include=["documents"]
        )
        
        assert len(results["ids"]) == 0

    def test_persistence_across_client_instances(self, temp_chroma_dir):
        """Test that data persists across client instances."""
        collection_name = "persistent_words"
        
        # First client - write data
        client1 = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        client1.upsert_vectors(
            collection_name=collection_name,
            ids=["persist_1"],
            embeddings=[[0.1, 0.2, 0.3] * 512],
            documents=["persistent document"],
            metadatas=[{"persistent": True}]
        )
        
        # Explicit persist
        client1.persist()
        
        # Second client - read data
        client2 = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        results = client2.get_by_ids(
            collection_name,
            ["persist_1"],
            include=["documents", "metadatas"]
        )
        
        assert len(results["ids"]) == 1
        assert results["documents"][0] == "persistent document"
        assert results["metadatas"][0]["persistent"] is True

    def test_multiple_collections(self, in_memory_client):
        """Test managing multiple collections."""
        collections = ["words", "lines", "receipts"]
        
        for i, collection_name in enumerate(collections):
            in_memory_client.upsert_vectors(
                collection_name=collection_name,
                ids=[f"vec_{i}"],
                embeddings=[[i * 0.1, i * 0.2, i * 0.3] * 512],
                documents=[f"document {i}"],
                metadatas=[{"collection": collection_name}]
            )
        
        # Verify each collection has its data
        for i, collection_name in enumerate(collections):
            results = in_memory_client.get_by_ids(
                collection_name,
                [f"vec_{i}"],
                include=["metadatas"]
            )
            
            assert len(results["ids"]) == 1
            assert results["metadatas"][0]["collection"] == collection_name

    def test_large_batch_operations(self, in_memory_client, performance_timer):
        """Test performance with large batches."""
        collection_name = "large_batch"
        batch_size = 1000
        
        # Generate large batch
        ids = [f"batch_vec_{i}" for i in range(batch_size)]
        embeddings = [[i * 0.001, i * 0.002, i * 0.003] * 512 for i in range(batch_size)]
        documents = [f"batch document {i}" for i in range(batch_size)]
        metadatas = [{"batch_id": i, "even": i % 2 == 0} for i in range(batch_size)]
        
        # Time the upsert
        performance_timer.start()
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        upsert_time = performance_timer.stop()
        
        # Should complete in reasonable time
        assert upsert_time < 30.0, f"Large batch upsert took {upsert_time:.2f}s, should be <30s"
        
        # Verify count
        collection = in_memory_client.get_collection(collection_name)
        assert collection.count() == batch_size
        
        # Time a large query
        performance_timer.start()
        results = in_memory_client.query_collection(
            collection_name=collection_name,
            query_embeddings=[[0.5, 0.5, 0.5] * 512],
            n_results=50,
            where={"even": True}
        )
        query_time = performance_timer.stop()
        
        assert query_time < 5.0, f"Large batch query took {query_time:.2f}s, should be <5s"
        assert len(results["ids"][0]) == 50

    def test_metadata_update_operations(self, in_memory_client):
        """Test updating metadata without changing embeddings."""
        collection_name = "metadata_update"
        
        # Initial upsert
        in_memory_client.upsert_vectors(
            collection_name=collection_name,
            ids=["meta_1", "meta_2"],
            embeddings=[[0.1, 0.2, 0.3] * 512, [0.4, 0.5, 0.6] * 512],
            documents=["doc 1", "doc 2"],
            metadatas=[
                {"valid_labels": ["MERCHANT"], "invalid_labels": []},
                {"valid_labels": [], "invalid_labels": ["NOISE"]}
            ]
        )
        
        # Update metadata via collection
        collection = in_memory_client.get_collection(collection_name)
        collection.update(
            ids=["meta_1"],
            metadatas=[{"valid_labels": ["MERCHANT", "COMPANY"], "invalid_labels": [], "updated": True}]
        )
        
        # Verify update
        results = in_memory_client.get_by_ids(
            collection_name,
            ["meta_1", "meta_2"],
            include=["metadatas"]
        )
        
        meta_1 = next(meta for i, meta in enumerate(results["metadatas"]) 
                     if results["ids"][i] == "meta_1")
        meta_2 = next(meta for i, meta in enumerate(results["metadatas"]) 
                     if results["ids"][i] == "meta_2")
        
        assert "COMPANY" in meta_1["valid_labels"]
        assert meta_1.get("updated") is True
        assert meta_2["invalid_labels"] == ["NOISE"]  # Unchanged

    def test_error_handling_invalid_inputs(self, in_memory_client):
        """Test error handling for invalid inputs."""
        collection_name = "error_test"
        
        # Test mismatched array lengths
        with pytest.raises((ValueError, AssertionError)):
            in_memory_client.upsert_vectors(
                collection_name=collection_name,
                ids=["id1", "id2"],  # 2 IDs
                embeddings=[[0.1] * 1536],  # 1 embedding
                documents=["doc1", "doc2"],  # 2 documents
                metadatas=[{"meta": 1}]  # 1 metadata
            )
        
        # Test empty inputs
        try:
            in_memory_client.upsert_vectors(
                collection_name=collection_name,
                ids=[],
                embeddings=[],
                documents=[],
                metadatas=[]
            )
            # Should handle empty gracefully (no error)
        except Exception as e:
            pytest.fail(f"Should handle empty input gracefully, got: {e}")
        
        # Test invalid embedding dimension
        with pytest.raises((ValueError, chromadb.errors.InvalidDimensionException)):
            in_memory_client.upsert_vectors(
                collection_name=collection_name,
                ids=["invalid_dim"],
                embeddings=[[0.1, 0.2]],  # Wrong dimension
                documents=["doc"],
                metadatas=[{"meta": 1}]
            )

    def test_concurrent_access_safety(self, temp_chroma_dir):
        """Test thread safety of client operations."""
        import threading
        import time
        
        client = ChromaDBClient(
            persist_directory=temp_chroma_dir,
            use_persistent_client=True
        )
        
        results = {}
        errors = {}
        
        def worker_function(worker_id):
            """Worker function for concurrent testing."""
            try:
                # Each worker uses different collection to avoid conflicts
                collection_name = f"concurrent_{worker_id}"
                
                client.upsert_vectors(
                    collection_name=collection_name,
                    ids=[f"worker_{worker_id}_vec_{i}" for i in range(10)],
                    embeddings=[[worker_id * 0.1 + i * 0.01] * 1536 for i in range(10)],
                    documents=[f"worker {worker_id} doc {i}" for i in range(10)],
                    metadatas=[{"worker_id": worker_id, "item": i} for i in range(10)]
                )
                
                # Verify data was written correctly
                get_results = client.get_by_ids(
                    collection_name,
                    [f"worker_{worker_id}_vec_0"],
                    include=["metadatas"]
                )
                
                if len(get_results["ids"]) == 1:
                    results[worker_id] = "success"
                else:
                    results[worker_id] = "data_error"
                    
            except Exception as e:
                errors[worker_id] = str(e)
        
        # Start multiple workers
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker_function, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join(timeout=30)
        
        # Verify all workers succeeded
        assert len(errors) == 0, f"Workers had errors: {errors}"
        assert len(results) == 5, f"Not all workers completed: {results}"
        assert all(result == "success" for result in results.values())

    def test_openai_embedding_function_integration(self, in_memory_client):
        """Test integration with OpenAI embedding function."""
        collection_name = "openai_embeddings"
        
        # Mock OpenAI embedding function
        with patch.object(in_memory_client, '_get_embedding_function') as mock_ef:
            mock_ef.return_value = Mock()
            mock_ef.return_value.__call__ = Mock(return_value=[[0.1, 0.2, 0.3] * 512])
            
            # Create collection (should use embedding function)
            collection = in_memory_client.get_collection(collection_name)
            
            # The collection should be created with OpenAI embedding function
            assert collection is not None

    def test_collection_metadata_and_settings(self, in_memory_client):
        """Test collection metadata and settings."""
        collection_name = "metadata_test"
        
        collection = in_memory_client.get_collection(collection_name)
        
        # Should have expected metadata
        assert collection.name == collection_name
        # Could test additional metadata if supported by implementation

    def test_client_configuration_modes(self, temp_chroma_dir):
        """Test different client configuration modes."""
        configs = [
            # In-memory mode
            {"use_persistent_client": False},
            
            # Persistent mode with custom directory
            {"use_persistent_client": True, "persist_directory": temp_chroma_dir},
            
            # Persistent mode with default settings
            {"use_persistent_client": True},
        ]
        
        for config in configs:
            try:
                client = ChromaDBClient(**config)
                
                # Should be able to perform basic operations
                collection = client.get_collection("config_test")
                assert collection is not None
                
                client.upsert_vectors(
                    collection_name="config_test",
                    ids=["config_test_1"],
                    embeddings=[[0.1, 0.2, 0.3] * 512],
                    documents=["config test document"],
                    metadatas=[{"config": str(config)}]
                )
                
                results = client.get_by_ids(
                    "config_test",
                    ["config_test_1"],
                    include=["documents"]
                )
                
                assert len(results["ids"]) == 1
                
            except Exception as e:
                pytest.fail(f"Configuration {config} failed: {e}")