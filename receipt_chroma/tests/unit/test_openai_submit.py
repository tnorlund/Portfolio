"""Tests for OpenAI batch submission."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from receipt_chroma.embedding.openai.submit import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)


class TestUploadToOpenAI:
    """Tests for upload_to_openai function."""

    @patch("pathlib.Path.open", new_callable=mock_open, read_data=b"test data")
    def test_upload_success(self, mock_file: MagicMock) -> None:
        """Test successful file upload to OpenAI."""
        mock_client = MagicMock()
        mock_file_obj = MagicMock()
        mock_client.files.create.return_value = mock_file_obj

        filepath = Path("/tmp/test.jsonl")
        result = upload_to_openai(filepath, mock_client)

        assert result == mock_file_obj
        mock_client.files.create.assert_called_once()


class TestSubmitOpenAIBatch:
    """Tests for submit_openai_batch function."""

    def test_submit_batch(self) -> None:
        """Test batch submission to OpenAI."""
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_client.batches.create.return_value = mock_batch

        result = submit_openai_batch("file-123", mock_client)

        assert result == mock_batch
        mock_client.batches.create.assert_called_once_with(
            input_file_id="file-123",
            endpoint="/v1/embeddings",
            completion_window="24h",
            metadata={"model": "text-embedding-3-small"},
        )


class TestCreateBatchSummary:
    """Tests for create_batch_summary function."""

    def test_creates_summary_from_ndjson(self) -> None:
        """Test creating batch summary from NDJSON file."""
        # Create a temporary NDJSON file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            json.dump({"custom_id": "IMAGE#img1#RECEIPT#00001#LINE#00001"}, f)
            f.write("\n")
            json.dump({"custom_id": "IMAGE#img1#RECEIPT#00001#LINE#00002"}, f)
            f.write("\n")
            json.dump({"custom_id": "IMAGE#img2#RECEIPT#00002#LINE#00001"}, f)
            temp_path = f.name

        try:
            summary = create_batch_summary(
                batch_id="batch123",
                openai_batch_id="openai123",
                file_path=temp_path,
                batch_type="LINE_EMBEDDING",
            )

            assert summary.batch_id == "batch123"
            assert summary.openai_batch_id == "openai123"
            assert summary.batch_type == "LINE_EMBEDDING"
            assert summary.status == "PENDING"
            assert summary.receipt_refs is not None
            assert len(summary.receipt_refs) == 2
            assert ("img1", 1) in summary.receipt_refs
            assert ("img2", 2) in summary.receipt_refs
        finally:
            import os

            os.unlink(temp_path)

    def test_handles_invalid_json_lines(self) -> None:
        """Test that invalid JSON lines are skipped."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            json.dump({"custom_id": "IMAGE#img1#RECEIPT#00001#LINE#00001"}, f)
            f.write("\n")
            f.write("invalid json line\n")
            json.dump({"custom_id": "IMAGE#img2#RECEIPT#00002#LINE#00001"}, f)
            temp_path = f.name

        try:
            summary = create_batch_summary(
                batch_id="batch123",
                openai_batch_id="openai123",
                file_path=temp_path,
                batch_type="WORD_EMBEDDING",
            )

            # Should still create summary despite invalid line
            assert summary.batch_id == "batch123"
            assert summary.receipt_refs is not None
            assert len(summary.receipt_refs) == 2
        finally:
            import os

            os.unlink(temp_path)


class TestAddBatchSummary:
    """Tests for add_batch_summary function."""

    def test_adds_summary_to_dynamo(self) -> None:
        """Test adding batch summary to DynamoDB."""
        mock_dynamo_client = MagicMock()
        mock_summary = MagicMock()

        add_batch_summary(mock_summary, mock_dynamo_client)

        mock_dynamo_client.add_batch_summary.assert_called_once_with(mock_summary)
