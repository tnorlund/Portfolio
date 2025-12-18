"""Tests for batched chunk processing optimization.

These tests verify that the compaction handler can process multiple chunks
in a single Lambda invocation, reducing the total number of invocations.
"""

import json
import os
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestBatchedChunkProcessing:
    """Test batched chunk processing with multiple chunks per Lambda."""

    @pytest.fixture
    def mock_s3_chunks(self):
        """Create mock S3 chunks data."""
        return [
            {
                "chunk_index": 0,
                "batch_id": "test-batch",
                "delta_results": [
                    {
                        "delta_key": "delta/abc123/",
                        "delta_id": "abc123",
                        "embedding_count": 10,
                        "collection": "lines",
                    }
                ],
            },
            {
                "chunk_index": 1,
                "batch_id": "test-batch",
                "delta_results": [
                    {
                        "delta_key": "delta/def456/",
                        "delta_id": "def456",
                        "embedding_count": 15,
                        "collection": "lines",
                    }
                ],
            },
            {
                "chunk_index": 2,
                "batch_id": "test-batch",
                "delta_results": [
                    {
                        "delta_key": "delta/ghi789/",
                        "delta_id": "ghi789",
                        "embedding_count": 20,
                        "collection": "lines",
                    }
                ],
            },
            {
                "chunk_index": 3,
                "batch_id": "test-batch",
                "delta_results": [],  # Empty chunk
            },
        ]

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    @patch("handlers.compaction.perform_intermediate_merge")
    def test_process_single_chunk_index_backward_compatibility(
        self, mock_merge, mock_process, mock_s3, mock_s3_chunks
    ):
        """Test that single chunk_index still works (backward compatibility)."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download to return chunks
        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(mock_s3_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        # Mock chunk processing to return intermediate key
        mock_process.return_value = {
            "intermediate_key": "intermediate/test-batch/chunk-0/"
        }

        event = {
            "batch_id": "test-batch",
            "chunk_index": 0,  # Single chunk (backward compatible)
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        result = process_chunk_handler(event)

        # Should process successfully and return intermediate_key
        assert "intermediate_key" in result
        assert result["intermediate_key"] == "intermediate/test-batch/chunk-0/"

        # Should NOT call merge (only 1 chunk)
        mock_merge.assert_not_called()

        # Should call process_chunk_deltas once
        assert mock_process.call_count == 1

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    @patch("handlers.compaction.perform_intermediate_merge")
    def test_process_multiple_chunk_indices(
        self, mock_merge, mock_process, mock_s3, mock_s3_chunks
    ):
        """Test processing multiple chunks in one Lambda invocation."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download
        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(mock_s3_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        # Mock chunk processing to return different intermediate keys
        def mock_process_side_effect(batch_id, chunk_index, deltas, by_collection):
            return {
                "intermediate_key": f"intermediate/{batch_id}/chunk-{chunk_index}/"
            }

        mock_process.side_effect = mock_process_side_effect

        # Mock merge to combine intermediates
        mock_merge.return_value = {
            "intermediate_key": "intermediate/test-batch-batch/merged-0/"
        }

        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 2],  # 3 chunks (batched)
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        result = process_chunk_handler(event)

        # Should process all 3 chunks
        assert mock_process.call_count == 3

        # Should merge the intermediates
        assert mock_merge.call_count == 1

        # Should return merged intermediate_key
        assert "intermediate_key" in result
        assert "merged" in result["intermediate_key"]

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    @patch("handlers.compaction.perform_intermediate_merge")
    def test_process_chunk_with_empty_chunks(
        self, mock_merge, mock_process, mock_s3, mock_s3_chunks
    ):
        """Test handling of empty chunks in batch."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download
        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(mock_s3_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        # Mock chunk processing
        def mock_process_side_effect(batch_id, chunk_index, deltas, by_collection):
            return {
                "intermediate_key": f"intermediate/{batch_id}/chunk-{chunk_index}/"
            }

        mock_process.side_effect = mock_process_side_effect

        # Mock merge
        mock_merge.return_value = {
            "intermediate_key": "intermediate/test-batch-batch/merged-0/"
        }

        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 3],  # Chunk 3 is empty
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        result = process_chunk_handler(event)

        # Should process only non-empty chunks (0 and 1)
        assert mock_process.call_count == 2

        # Should still merge the valid intermediates
        assert mock_merge.call_count == 1

        # Should return merged result
        assert "intermediate_key" in result

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    def test_process_chunk_all_empty(self, mock_process, mock_s3):
        """Test handling when all chunks are empty."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download with all empty chunks
        empty_chunks = [
            {"chunk_index": 0, "batch_id": "test-batch", "delta_results": []},
            {"chunk_index": 1, "batch_id": "test-batch", "delta_results": []},
        ]

        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(empty_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1],  # All empty
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        result = process_chunk_handler(event)

        # Should not call process_chunk_deltas (no valid chunks)
        assert mock_process.call_count == 0

        # Should return appropriate empty response
        assert result["statusCode"] == 200
        assert "message" in result
        assert "empty" in result["message"].lower()

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    @patch("handlers.compaction.perform_intermediate_merge")
    def test_process_chunk_batching_preserves_order(
        self, mock_merge, mock_process, mock_s3, mock_s3_chunks
    ):
        """Test that chunks are processed in order."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download
        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(mock_s3_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        # Track the order of chunk processing
        processed_indices = []

        def mock_process_side_effect(batch_id, chunk_index, deltas, by_collection):
            processed_indices.append(chunk_index)
            return {
                "intermediate_key": f"intermediate/{batch_id}/chunk-{chunk_index}/"
            }

        mock_process.side_effect = mock_process_side_effect

        # Mock merge
        mock_merge.return_value = {
            "intermediate_key": "intermediate/test-batch-batch/merged/"
        }

        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 2],
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        process_chunk_handler(event)

        # Verify chunks were processed in order
        assert processed_indices == [0, 1, 2]

    @patch("handlers.compaction.s3_client")
    @patch("handlers.compaction.process_chunk_deltas")
    def test_process_chunk_error_handling(self, mock_process, mock_s3, mock_s3_chunks):
        """Test error handling when chunk processing fails."""
        from handlers.compaction import process_chunk_handler

        # Mock S3 download
        def mock_download(bucket, key, path):
            with open(path, "w") as f:
                json.dump(mock_s3_chunks, f)

        mock_s3.download_file.side_effect = mock_download

        # Mock chunk processing to fail on second chunk
        def mock_process_side_effect(batch_id, chunk_index, deltas, by_collection):
            if chunk_index == 1:
                raise ValueError("Simulated processing error")
            return {
                "intermediate_key": f"intermediate/{batch_id}/chunk-{chunk_index}/"
            }

        mock_process.side_effect = mock_process_side_effect

        event = {
            "batch_id": "test-batch",
            "chunk_indices": [0, 1, 2],
            "chunks_s3_key": "chunks/test-batch/chunks.json",
            "chunks_s3_bucket": "test-bucket",
            "database": "lines",
        }

        # Should raise exception (for Step Functions retry)
        with pytest.raises(ValueError, match="Simulated processing error"):
            process_chunk_handler(event)

        # Should have attempted to process first chunk
        assert mock_process.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
