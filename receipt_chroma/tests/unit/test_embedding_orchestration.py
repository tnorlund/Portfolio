"""
Unit tests for embedding orchestration module.

These tests focus on EmbeddingResult lifecycle, context managers, and
validation without requiring actual S3/DynamoDB/OpenAI operations.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from receipt_chroma.embedding.orchestration import (
    EmbeddingResult,
    create_embeddings_and_compaction_run,
)
from receipt_dynamo.constants import CompactionState


class TestEmbeddingResult:
    """Test EmbeddingResult lifecycle and methods."""

    @pytest.mark.unit
    def test_close_closes_both_clients(self):
        """Verify close() closes both ChromaClients."""
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.run_id = "test-run"

        # Create temp directories for the test
        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        result = EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        )

        result.close()

        mock_lines.close.assert_called_once()
        mock_words.close.assert_called_once()
        assert result._closed is True

    @pytest.mark.unit
    def test_close_is_idempotent(self):
        """Verify close() can be called multiple times safely."""
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.run_id = "test-run"

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        result = EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        )

        result.close()
        result.close()  # Should not raise

        # Should only be called once
        assert mock_lines.close.call_count == 1
        assert mock_words.close.call_count == 1

    @pytest.mark.unit
    def test_context_manager_closes_on_exit(self):
        """Verify context manager calls close()."""
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.run_id = "test-run"

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        with EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        ) as result:
            pass

        mock_lines.close.assert_called_once()
        mock_words.close.assert_called_once()
        assert result._closed is True

    @pytest.mark.unit
    def test_context_manager_closes_on_exception(self):
        """Verify context manager closes even if exception occurs."""
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.run_id = "test-run"

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        with pytest.raises(ValueError):
            with EmbeddingResult(
                lines_client=mock_lines,
                words_client=mock_words,
                compaction_run=mock_run,
                _lines_dir=lines_dir,
                _words_dir=words_dir,
            ):
                raise ValueError("Test exception")

        mock_lines.close.assert_called_once()
        mock_words.close.assert_called_once()

    @pytest.mark.unit
    def test_wait_for_compaction_success(self):
        """Verify wait_for_compaction_to_finish returns True on success."""
        mock_client = MagicMock()
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.image_id = "test-image"
        mock_run.receipt_id = 1
        mock_run.run_id = "run-123"

        # Simulate completion after first poll
        updated_run = MagicMock()
        updated_run.lines_state = CompactionState.COMPLETED.value
        updated_run.words_state = CompactionState.COMPLETED.value
        mock_client.get_compaction_run.return_value = updated_run

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        result = EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        )

        success = result.wait_for_compaction_to_finish(
            mock_client,
            max_wait_seconds=10,
            poll_interval_seconds=0.1,
        )

        assert success is True
        mock_client.get_compaction_run.assert_called_with(
            image_id="test-image",
            receipt_id=1,
            run_id="run-123",
        )

        result.close()

    @pytest.mark.unit
    def test_wait_for_compaction_failure(self):
        """Verify wait_for_compaction_to_finish returns False on failure."""
        mock_client = MagicMock()
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.image_id = "test-image"
        mock_run.receipt_id = 1
        mock_run.run_id = "run-123"

        # Simulate failure
        updated_run = MagicMock()
        updated_run.lines_state = CompactionState.COMPLETED.value
        updated_run.words_state = CompactionState.FAILED.value
        mock_client.get_compaction_run.return_value = updated_run

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        result = EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        )

        success = result.wait_for_compaction_to_finish(
            mock_client,
            max_wait_seconds=10,
            poll_interval_seconds=0.1,
        )

        assert success is False
        result.close()

    @pytest.mark.unit
    def test_wait_for_compaction_not_found(self):
        """Verify wait_for_compaction_to_finish returns False if run not found."""
        mock_client = MagicMock()
        mock_client.get_compaction_run.return_value = None
        mock_lines = MagicMock()
        mock_words = MagicMock()
        mock_run = MagicMock()
        mock_run.image_id = "test-image"
        mock_run.receipt_id = 1
        mock_run.run_id = "run-123"

        lines_dir = tempfile.mkdtemp()
        words_dir = tempfile.mkdtemp()

        result = EmbeddingResult(
            lines_client=mock_lines,
            words_client=mock_words,
            compaction_run=mock_run,
            _lines_dir=lines_dir,
            _words_dir=words_dir,
        )

        success = result.wait_for_compaction_to_finish(
            mock_client,
            max_wait_seconds=1,
            poll_interval_seconds=0.1,
        )

        assert success is False
        result.close()


class TestCreateEmbeddingsValidation:
    """Test input validation for create_embeddings_and_compaction_run."""

    @pytest.mark.unit
    def test_empty_lines_raises_value_error(self):
        """Verify ValueError raised for empty receipt_lines."""
        mock_dynamo = MagicMock()
        mock_word = MagicMock()

        with pytest.raises(ValueError, match="receipt_lines cannot be empty"):
            create_embeddings_and_compaction_run(
                receipt_lines=[],
                receipt_words=[mock_word],
                image_id="test",
                receipt_id=1,
                chromadb_bucket="bucket",
                dynamo_client=mock_dynamo,
            )

    @pytest.mark.unit
    def test_empty_words_raises_value_error(self):
        """Verify ValueError raised for empty receipt_words."""
        mock_dynamo = MagicMock()
        mock_line = MagicMock()

        with pytest.raises(ValueError, match="receipt_words cannot be empty"):
            create_embeddings_and_compaction_run(
                receipt_lines=[mock_line],
                receipt_words=[],
                image_id="test",
                receipt_id=1,
                chromadb_bucket="bucket",
                dynamo_client=mock_dynamo,
            )

    @pytest.mark.unit
    def test_missing_openai_key_raises_runtime_error(self):
        """Verify RuntimeError raised when OPENAI_API_KEY not set."""
        mock_dynamo = MagicMock()
        mock_line = MagicMock()
        mock_word = MagicMock()

        # Ensure OPENAI_API_KEY is not set
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)

            with pytest.raises(
                RuntimeError,
                match="OPENAI_API_KEY environment variable not set",
            ):
                create_embeddings_and_compaction_run(
                    receipt_lines=[mock_line],
                    receipt_words=[mock_word],
                    image_id="test",
                    receipt_id=1,
                    chromadb_bucket="bucket",
                    dynamo_client=mock_dynamo,
                )
