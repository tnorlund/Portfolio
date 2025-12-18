"""Tests for chunk batching optimization in normalize handler.

These tests verify that chunks are correctly grouped into batches for
processing multiple chunks per Lambda invocation.
"""

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestChunkBatching:
    """Test chunk batching logic in normalize handler."""

    @pytest.fixture
    def mock_poll_results(self):
        """Create mock poll results with S3 references."""
        return [
            {
                "result_s3_bucket": "test-bucket",
                "result_s3_key": f"poll_results/test-batch/result-{i}.json",
                "batch_id": f"batch-{i}",
            }
            for i in range(16)  # 16 poll results
        ]

    @pytest.fixture
    def mock_combined_results(self):
        """Create mock combined poll results with delta keys."""
        return [
            {
                "delta_key": f"delta/{i}/",
                "delta_id": f"delta-{i}",
                "collection": "lines",
                "embedding_count": 10,
            }
            for i in range(50)  # 50 deltas
        ]

    def test_chunks_per_lambda_default(self, monkeypatch, mock_combined_results):
        """Test default CHUNKS_PER_LAMBDA=4."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "10")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        with patch(
            "handler._download_and_combine_poll_results",
            return_value=mock_combined_results,
        ):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],  # Mocked
                }

                result = handler.lambda_handler(event, None)

                # 50 deltas / 10 per chunk = 5 chunks
                # 5 chunks / 4 per Lambda = 2 batches (4 + 1)
                assert result["total_chunks"] == 5
                assert result["total_batches"] == 2
                assert result["chunks_per_lambda"] == 4

    def test_chunk_batching_creates_correct_structure(
        self, monkeypatch, mock_combined_results
    ):
        """Test that chunk batches have correct structure."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "3")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "5")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        # 50 deltas / 5 per chunk = 10 chunks
        # 10 chunks / 3 per Lambda = 4 batches (3 + 3 + 3 + 1)

        with patch(
            "handler._download_and_combine_poll_results",
            return_value=mock_combined_results,
        ):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                assert result["total_chunks"] == 10
                assert result["total_batches"] == 4
                assert len(result["chunks"]) == 4

                # First batch should have chunk_indices [0,1,2]
                assert result["chunks"][0]["chunk_indices"] == [0, 1, 2]
                assert result["chunks"][0]["batch_id"] == "test-batch"
                assert "chunks_s3_key" in result["chunks"][0]
                assert "chunks_s3_bucket" in result["chunks"][0]

                # Second batch should have chunk_indices [3,4,5]
                assert result["chunks"][1]["chunk_indices"] == [3, 4, 5]

                # Third batch should have chunk_indices [6,7,8]
                assert result["chunks"][2]["chunk_indices"] == [6, 7, 8]

                # Fourth batch should have chunk_indices [9] (remainder)
                assert result["chunks"][3]["chunk_indices"] == [9]

    def test_chunk_batching_with_different_batch_sizes(
        self, monkeypatch, mock_combined_results
    ):
        """Test batching with different CHUNKS_PER_LAMBDA values."""
        test_cases = [
            (1, 50),  # No batching: 1 chunk per Lambda
            (2, 25),  # 2 chunks per Lambda
            (4, 13),  # 4 chunks per Lambda (50/10 chunks = 5 chunks, ceil(5/4) = 2 batches)
            (10, 5),  # 10 chunks per Lambda
        ]

        for chunks_per_lambda, expected_batches in test_cases:
            monkeypatch.setenv("CHUNKS_PER_LAMBDA", str(chunks_per_lambda))
            monkeypatch.setenv("CHUNK_SIZE_LINES", "1")  # 1 delta per chunk
            monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

            import handler
            import importlib

            importlib.reload(handler)

            with patch(
                "handler._download_and_combine_poll_results",
                return_value=mock_combined_results,
            ):
                with patch("handler._upload_json_to_s3"):
                    event = {
                        "batch_id": "test-batch",
                        "database": "lines",
                        "poll_results": [],
                    }

                    result = handler.lambda_handler(event, None)

                    # 50 deltas / 1 per chunk = 50 chunks
                    assert result["total_chunks"] == 50
                    assert result["total_batches"] == expected_batches

    def test_no_deltas_returns_empty(self, monkeypatch):
        """Test handling when there are no deltas."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        with patch("handler._download_and_combine_poll_results", return_value=[]):
            event = {
                "batch_id": "test-batch",
                "database": "lines",
                "poll_results": [],
            }

            result = handler.lambda_handler(event, None)

            assert result["has_chunks"] is False
            assert result["total_chunks"] == 0
            assert result["chunks"] == []

    def test_chunk_batch_s3_keys_passed_correctly(
        self, monkeypatch, mock_combined_results
    ):
        """Test that S3 keys are correctly passed in chunk batches."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "2")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "10")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        with patch(
            "handler._download_and_combine_poll_results",
            return_value=mock_combined_results,
        ):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                # Verify each chunk batch has S3 keys
                for batch in result["chunks"]:
                    assert "chunks_s3_key" in batch
                    assert "chunks_s3_bucket" in batch
                    assert batch["chunks_s3_key"] == "chunks/test-batch/chunks.json"
                    assert batch["chunks_s3_bucket"] == "test-bucket"
                    assert batch["batch_id"] == "test-batch"

    def test_poll_results_s3_keys_preserved(self, monkeypatch, mock_combined_results):
        """Test that poll_results S3 keys are preserved in output."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "10")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        with patch(
            "handler._download_and_combine_poll_results",
            return_value=mock_combined_results,
        ):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                # Should include poll_results S3 keys
                assert result["poll_results_s3_key"] == "poll_results/test-batch/poll_results.json"
                assert result["poll_results_s3_bucket"] == "test-bucket"

    def test_exact_multiple_of_batch_size(self, monkeypatch):
        """Test when chunk count is exact multiple of batch size."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "5")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "2")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        # Create exactly 10 deltas → 5 chunks → 1 batch (exact fit)
        deltas = [
            {
                "delta_key": f"delta/{i}/",
                "collection": "lines",
            }
            for i in range(10)
        ]

        with patch("handler._download_and_combine_poll_results", return_value=deltas):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                # 10 deltas / 2 per chunk = 5 chunks
                # 5 chunks / 5 per Lambda = 1 batch (exact)
                assert result["total_chunks"] == 5
                assert result["total_batches"] == 1
                assert len(result["chunks"]) == 1
                assert result["chunks"][0]["chunk_indices"] == [0, 1, 2, 3, 4]


class TestChunkBatchingEdgeCases:
    """Test edge cases for chunk batching."""

    def test_single_delta_single_batch(self, monkeypatch):
        """Test with single delta."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "4")
        monkeypatch.setenv("CHUNK_SIZE_LINES", "1")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        deltas = [{"delta_key": "delta/0/", "collection": "lines"}]

        with patch("handler._download_and_combine_poll_results", return_value=deltas):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "lines",
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                # 1 delta → 1 chunk → 1 batch
                assert result["total_chunks"] == 1
                assert result["total_batches"] == 1
                assert result["chunks"][0]["chunk_indices"] == [0]

    def test_words_database_uses_different_chunk_size(self, monkeypatch):
        """Test that 'words' database uses CHUNK_SIZE_WORDS."""
        monkeypatch.setenv("CHUNKS_PER_LAMBDA", "3")
        monkeypatch.setenv("CHUNK_SIZE_WORDS", "5")  # Different from LINES
        monkeypatch.setenv("CHUNK_SIZE_LINES", "10")
        monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")

        import handler
        import importlib

        importlib.reload(handler)

        deltas = [
            {"delta_key": f"delta/{i}/", "collection": "words"} for i in range(30)
        ]

        with patch("handler._download_and_combine_poll_results", return_value=deltas):
            with patch("handler._upload_json_to_s3"):
                event = {
                    "batch_id": "test-batch",
                    "database": "words",  # Words database
                    "poll_results": [],
                }

                result = handler.lambda_handler(event, None)

                # 30 deltas / 5 per chunk = 6 chunks
                # 6 chunks / 3 per Lambda = 2 batches
                assert result["total_chunks"] == 6
                assert result["total_batches"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
