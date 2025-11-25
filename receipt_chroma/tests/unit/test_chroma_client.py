"""
Unit tests for ChromaClient.

These tests focus on client behavior, context managers, and resource management
without requiring actual ChromaDB operations or file I/O.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from receipt_chroma import ChromaClient


@pytest.mark.unit
def test_context_manager_cleanup():
    """Test that context manager properly cleans up resources."""
    # Use in-memory client for unit test with metadata_only to avoid OpenAI API
    with ChromaClient(mode="write", metadata_only=True) as client:
        collection = client.get_collection("test", create_if_missing=True)
        collection.upsert(
            ids=["1"],
            documents=["test document"],
            metadatas=[{"key": "value"}],
        )
        assert collection.count() == 1

    # Client should be closed after context exit
    assert client._closed  # pylint: disable=protected-access


@pytest.mark.unit
def test_explicit_close():
    """Test that explicit close() method works."""
    client = ChromaClient(mode="write", metadata_only=True)  # In-memory
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test"])

    # Close the client
    client.close()

    # Client should be marked as closed
    assert client._closed  # pylint: disable=protected-access

    # Should not be able to use client after close
    with pytest.raises(RuntimeError, match="Cannot use closed ChromaClient"):
        client.get_collection("test")


@pytest.mark.unit
def test_close_can_be_called_multiple_times():
    """Test that close() can be called multiple times safely."""
    client = ChromaClient()  # In-memory
    client.close()

    # Should not raise an error
    client.close()
    client.close()

    assert client._closed  # pylint: disable=protected-access


@pytest.mark.unit
def test_close_without_persist_directory():
    """Test that close() works with in-memory clients."""
    client = ChromaClient(mode="write", metadata_only=True)  # In-memory
    collection = client.get_collection("test", create_if_missing=True)
    collection.upsert(ids=["1"], documents=["test"])

    # Should close without error
    client.close()
    assert client._closed  # pylint: disable=protected-access


@pytest.mark.unit
def test_collection_context_manager():
    """Test the collection context manager."""
    with ChromaClient(mode="write", metadata_only=True) as client:  # In-memory
        with client.collection("test", create_if_missing=True) as coll:
            coll.upsert(ids=["1"], documents=["test"])
            assert coll.count() == 1


@pytest.mark.unit
def test_read_only_mode_raises_error():
    """Test that read-only mode raises error on write attempts."""
    with ChromaClient(mode="read") as client:
        with pytest.raises(RuntimeError, match="read-only"):
            client.upsert(
                collection_name="test", ids=["1"], documents=["test"]
            )


@pytest.mark.unit
def test_collection_exists():
    """Test collection_exists() method."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        # Collection doesn't exist yet
        assert not client.collection_exists("nonexistent")

        # Create collection
        client.get_collection("test", create_if_missing=True)

        # Now it exists
        assert client.collection_exists("test")


@pytest.mark.unit
def test_list_collections():
    """Test list_collections() method."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        # Initially empty
        collections = client.list_collections()
        initial_count = len(collections)

        # Create a collection
        client.get_collection("test1", create_if_missing=True)
        client.get_collection("test2", create_if_missing=True)

        # Should have 2 more collections
        collections = client.list_collections()
        assert len(collections) == initial_count + 2
        assert "test1" in collections
        assert "test2" in collections


@pytest.mark.unit
def test_count():
    """Test count() method."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        collection_name = "count_test"
        client.upsert(
            collection_name=collection_name,
            ids=["1", "2", "3"],
            documents=["doc1", "doc2", "doc3"],
        )

        assert client.count(collection_name) == 3


@pytest.mark.unit
def test_reset():
    """Test reset() method."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        # Add some data
        client.upsert(
            collection_name="reset_test", ids=["1"], documents=["doc1"]
        )

        assert client.count("reset_test") == 1

        # Reset
        client.reset()

        # Collection should be gone
        assert not client.collection_exists("reset_test")


@pytest.mark.unit
def test_query_requires_embeddings_or_texts():
    """Test that query() requires either embeddings or texts."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        client.get_collection("test_query_validation", create_if_missing=True)

        with pytest.raises(
            ValueError, match="Either query_embeddings or query_texts"
        ):
            client.query(collection_name="test_query_validation", n_results=10)


@pytest.mark.unit
def test_delete_requires_ids_or_where():
    """Test that delete() requires either ids or where."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        client.get_collection("test_delete_validation", create_if_missing=True)

        with pytest.raises(
            ValueError, match="Either ids or where must be provided"
        ):
            client.delete(collection_name="test_delete_validation")


@pytest.mark.unit
def test_custom_embedding_function():
    """Test that custom embedding function is used when provided."""
    # Create a proper embedding function that matches ChromaDB's interface
    from chromadb.utils import embedding_functions

    # Use DefaultEmbeddingFunction as a real embedding function
    custom_embedding_fn = embedding_functions.DefaultEmbeddingFunction()

    with ChromaClient(
        mode="write", embedding_function=custom_embedding_fn
    ) as client:
        collection = client.get_collection(
            "test_custom_embed", create_if_missing=True
        )
        # Verify custom embedding function is set
        assert client._embedding_function is custom_embedding_fn


@pytest.mark.unit
@patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-123"})
def test_openai_embedding_function_setup():
    """Test that OpenAI embedding function is set up when not metadata_only."""
    with patch(
        "receipt_chroma.data.chroma_client.embedding_functions"
    ) as mock_ef:
        mock_openai_fn = MagicMock()
        mock_ef.OpenAIEmbeddingFunction.return_value = mock_openai_fn

        with ChromaClient(mode="write", metadata_only=False) as client:
            # Verify OpenAI embedding function was created
            mock_ef.OpenAIEmbeddingFunction.assert_called_once()
            call_kwargs = mock_ef.OpenAIEmbeddingFunction.call_args[1]
            assert call_kwargs["api_key"] == "test-key-123"
            assert call_kwargs["model_name"] == "text-embedding-3-small"
            assert client._embedding_function is mock_openai_fn


@pytest.mark.unit
@patch.dict("os.environ", {}, clear=True)
def test_openai_embedding_function_with_placeholder_key():
    """Test that OpenAI embedding function uses placeholder when no API key."""
    with patch(
        "receipt_chroma.data.chroma_client.embedding_functions"
    ) as mock_ef:
        mock_openai_fn = MagicMock()
        mock_ef.OpenAIEmbeddingFunction.return_value = mock_openai_fn

        with ChromaClient(mode="write", metadata_only=False) as client:
            # Verify OpenAI embedding function was created with placeholder
            mock_ef.OpenAIEmbeddingFunction.assert_called_once()
            call_kwargs = mock_ef.OpenAIEmbeddingFunction.call_args[1]
            assert call_kwargs["api_key"] == "placeholder"
            assert client._embedding_function is mock_openai_fn


@pytest.mark.unit
def test_upsert_with_embeddings():
    """Test upsert with explicit embeddings (not documents)."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        collection = client.get_collection(
            "test_embeddings", create_if_missing=True
        )

        embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        client.upsert(
            collection_name="test_embeddings",
            ids=["id1", "id2"],
            embeddings=embeddings,
            metadatas=[{"key": "value1"}, {"key": "value2"}],
        )

        assert collection.count() == 2


@pytest.mark.unit
def test_upsert_duplicate_id_handling():
    """Test that duplicate IDs are handled by deleting and retrying."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        collection = client.get_collection(
            "test_duplicates", create_if_missing=True
        )

        # First upsert
        client.upsert(
            collection_name="test_duplicates",
            ids=["duplicate_id"],
            documents=["first document"],
        )

        # Mock the collection to raise ValueError on second upsert
        original_upsert = collection.upsert
        original_delete = collection.delete
        call_count = 0
        delete_called = False

        def mock_upsert(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call raises duplicate error
                raise ValueError("ids already exist: ['duplicate_id']")
            # Second call succeeds
            return original_upsert(**kwargs)

        def mock_delete(**kwargs):
            nonlocal delete_called
            delete_called = True
            return original_delete(**kwargs)

        collection.upsert = MagicMock(side_effect=mock_upsert)
        collection.delete = MagicMock(side_effect=mock_delete)

        # Second upsert with same ID should handle duplicate
        client.upsert(
            collection_name="test_duplicates",
            ids=["duplicate_id"],
            documents=["second document"],
        )

        # Verify delete was called to handle duplicate
        assert delete_called


@pytest.mark.unit
def test_query_with_embeddings():
    """Test query using query_embeddings instead of query_texts."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        # Add some data with proper embedding dimensions (384 for DefaultEmbeddingFunction)
        # First get the collection to know the embedding dimension
        collection = client.get_collection(
            "test_query_emb", create_if_missing=True
        )
        embedding_dim = collection.metadata.get("hnsw:space") or 384

        # Create embeddings with correct dimension
        embeddings = [[0.1] * embedding_dim, [0.2] * embedding_dim]
        client.upsert(
            collection_name="test_query_emb",
            ids=["id1", "id2"],
            embeddings=embeddings,
            documents=["doc1", "doc2"],
        )

        # Query with embeddings
        query_embeddings = [[0.1] * embedding_dim]
        results = client.query(
            collection_name="test_query_emb",
            query_embeddings=query_embeddings,
            n_results=2,
        )

        assert "ids" in results
        assert len(results["ids"]) > 0


@pytest.mark.unit
def test_query_texts_in_read_mode_raises_error():
    """Test that text queries in read mode raise ValueError."""
    # First create collection in write mode
    with ChromaClient(mode="write", metadata_only=True) as temp_client:
        temp_client.upsert(
            collection_name="read_mode_test",
            ids=["id1"],
            documents=["test doc"],
        )

    # Now try to query with texts in read mode
    with ChromaClient(mode="read") as client:
        with pytest.raises(
            ValueError, match="Text queries require write mode"
        ):
            client.query(
                collection_name="read_mode_test",
                query_texts=["test query"],
                n_results=1,
            )


@pytest.mark.unit
def test_closed_client_raises_error():
    """Test that using a closed client raises RuntimeError."""
    client = ChromaClient(mode="write", metadata_only=True)
    client.close()

    # Try to get collection from closed client
    with pytest.raises(RuntimeError, match="Cannot use closed ChromaClient"):
        client.get_collection("test")


@pytest.mark.unit
def test_http_client_creation():
    """Test HTTP client creation when http_url is provided."""
    with patch("receipt_chroma.data.chroma_client.chromadb") as mock_chromadb:
        mock_http_client = MagicMock()
        mock_chromadb.HttpClient.return_value = mock_http_client

        client = ChromaClient(http_url="http://localhost:8000", mode="write")
        # Trigger client creation
        _ = client.client

        # Verify HttpClient was created
        mock_chromadb.HttpClient.assert_called_once()
        call_kwargs = mock_chromadb.HttpClient.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 8000


@pytest.mark.unit
def test_http_client_creation_with_url():
    """Test HTTP client creation with full URL."""
    with patch("receipt_chroma.data.chroma_client.chromadb") as mock_chromadb:
        mock_http_client = MagicMock()
        mock_chromadb.HttpClient.return_value = mock_http_client

        client = ChromaClient(
            http_url="https://chromadb.example.com:9000", mode="write"
        )
        _ = client.client

        # Verify HttpClient was created with parsed URL
        mock_chromadb.HttpClient.assert_called_once()
        call_kwargs = mock_chromadb.HttpClient.call_args[1]
        assert call_kwargs["host"] == "chromadb.example.com"
        assert call_kwargs["port"] == 9000


@pytest.mark.unit
def test_collection_not_found_without_create():
    """Test that get_collection raises error when collection doesn't exist and create_if_missing=False."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        with pytest.raises(
            ValueError, match="Collection 'nonexistent' not found"
        ):
            client.get_collection("nonexistent", create_if_missing=False)


@pytest.mark.unit
def test_closed_client_context_manager_raises_error():
    """Test that entering a closed client via context manager raises RuntimeError."""
    client = ChromaClient(mode="write", metadata_only=True)
    client.close()

    # Try to use closed client as context manager
    with pytest.raises(RuntimeError, match="Cannot use closed ChromaClient"):
        with client:
            pass


@pytest.mark.unit
def test_http_client_invalid_port():
    """Test HTTP client creation with invalid port (should handle gracefully)."""
    with patch("receipt_chroma.data.chroma_client.chromadb") as mock_chromadb:
        mock_http_client = MagicMock()
        mock_chromadb.HttpClient.return_value = mock_http_client

        # URL with invalid port (non-numeric)
        client = ChromaClient(
            http_url="http://localhost:invalid", mode="write"
        )
        _ = client.client

        # Should still create client, but port should be None
        mock_chromadb.HttpClient.assert_called_once()
        call_kwargs = mock_chromadb.HttpClient.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert "port" not in call_kwargs or call_kwargs.get("port") is None


@pytest.mark.unit
def test_upsert_value_error_not_duplicate():
    """Test that non-duplicate ValueError in upsert is re-raised."""
    with ChromaClient(mode="write", metadata_only=True) as client:
        collection = client.get_collection(
            "test_error", create_if_missing=True
        )

        # Mock collection to raise ValueError that's not about duplicates
        original_upsert = collection.upsert

        def mock_upsert(**kwargs):
            raise ValueError("Some other error")

        collection.upsert = MagicMock(side_effect=mock_upsert)

        # Should re-raise the error
        with pytest.raises(ValueError, match="Some other error"):
            client.upsert(
                collection_name="test_error", ids=["id1"], documents=["doc1"]
            )
