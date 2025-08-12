"""
Unit tests for the batch status handler module.

Tests all OpenAI batch status handling including completed, failed,
expired, in-progress, and cancelled states.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest

from receipt_dynamo.constants import BatchStatus, EmbeddingStatus
from receipt_dynamo.entities import BatchSummary
from receipt_label.embedding.common.batch_status_handler import (
    handle_batch_status,
    handle_cancelled_status,
    handle_completed_status,
    handle_expired_status,
    handle_failed_status,
    handle_in_progress_status,
    map_openai_to_dynamo_status,
    mark_items_for_retry,
    process_error_file,
    process_partial_results,
    should_retry_batch,
)


class TestMapOpenAIToDynamoStatus:
    """Test status mapping between OpenAI and DynamoDB."""
    
    @pytest.mark.parametrize(
        "openai_status,expected_dynamo_status",
        [
            ("validating", BatchStatus.VALIDATING),
            ("in_progress", BatchStatus.IN_PROGRESS),
            ("finalizing", BatchStatus.FINALIZING),
            ("completed", BatchStatus.COMPLETED),
            ("failed", BatchStatus.FAILED),
            ("expired", BatchStatus.EXPIRED),
            ("canceling", BatchStatus.CANCELING),
            ("cancelled", BatchStatus.CANCELLED),
        ],
    )
    def test_valid_status_mapping(
        self,
        openai_status: str,
        expected_dynamo_status: BatchStatus,
    ) -> None:
        """Test that valid OpenAI statuses map correctly."""
        result = map_openai_to_dynamo_status(openai_status)
        assert result == expected_dynamo_status
    
    def test_invalid_status_raises_error(self) -> None:
        """Test that unknown status raises ValueError."""
        with pytest.raises(ValueError, match="Unknown OpenAI batch status"):
            map_openai_to_dynamo_status("unknown_status")


class TestProcessErrorFile:
    """Test error file processing for failed batches."""
    
    def test_process_error_file_with_errors(self) -> None:
        """Test processing error file with multiple errors."""
        # Mock client manager
        client_manager = MagicMock()
        mock_batch = Mock()
        mock_batch.error_file_id = "error_file_123"
        client_manager.openai.batches.retrieve.return_value = mock_batch
        
        # Mock error file content
        error_content = [
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00001",
                "error": {
                    "type": "rate_limit_exceeded",
                    "message": "Rate limit exceeded for model"
                }
            }),
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00002",
                "error": {
                    "type": "invalid_request",
                    "message": "Invalid embedding input"
                }
            }),
        ]
        mock_response = Mock()
        mock_response.read.return_value = "\n".join(error_content).encode("utf-8")
        client_manager.openai.files.content.return_value = mock_response
        
        # Process error file
        result = process_error_file("batch_123", client_manager)
        
        # Verify results
        assert result["error_count"] == 2
        assert result["error_types"]["rate_limit_exceeded"] == 1
        assert result["error_types"]["invalid_request"] == 1
        assert len(result["error_details"]) == 2
        assert len(result["sample_errors"]) == 2
    
    def test_process_error_file_no_error_file(self) -> None:
        """Test processing when no error file exists."""
        client_manager = MagicMock()
        mock_batch = Mock()
        mock_batch.error_file_id = None
        client_manager.openai.batches.retrieve.return_value = mock_batch
        
        result = process_error_file("batch_123", client_manager)
        
        assert result["error_count"] == 0
        assert result["error_types"] == {}
        assert result["error_details"] == []
        assert result["sample_errors"] == []
    
    def test_process_error_file_with_malformed_json(self) -> None:
        """Test handling of malformed JSON in error file."""
        client_manager = MagicMock()
        mock_batch = Mock()
        mock_batch.error_file_id = "error_file_123"
        client_manager.openai.batches.retrieve.return_value = mock_batch
        
        # Mix valid and invalid JSON
        error_content = [
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00001",
                "error": {"type": "rate_limit_exceeded", "message": "Rate limit"}
            }),
            "INVALID JSON LINE",
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00002",
                "error": {"type": "invalid_request", "message": "Invalid input"}
            }),
        ]
        mock_response = Mock()
        mock_response.read.return_value = "\n".join(error_content).encode("utf-8")
        client_manager.openai.files.content.return_value = mock_response
        
        result = process_error_file("batch_123", client_manager)
        
        # Should process valid lines and skip invalid
        assert result["error_count"] == 2
        assert len(result["error_details"]) == 2


class TestProcessPartialResults:
    """Test partial result processing for expired batches."""
    
    def test_process_partial_results_with_output_and_errors(self) -> None:
        """Test processing partial results with both successes and failures."""
        client_manager = MagicMock()
        mock_batch = Mock()
        mock_batch.output_file_id = "output_file_123"
        mock_batch.error_file_id = "error_file_456"
        client_manager.openai.batches.retrieve.return_value = mock_batch
        
        # Mock output file with successful embeddings
        output_content = [
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00001",
                "response": {
                    "body": {
                        "data": [{"embedding": [0.1, 0.2, 0.3]}]
                    }
                }
            }),
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00002",
                "response": {
                    "body": {
                        "data": [{"embedding": None}]  # Failed embedding
                    }
                }
            }),
        ]
        mock_output = Mock()
        mock_output.read.return_value = "\n".join(output_content).encode("utf-8")
        
        # Mock error file
        error_content = [
            json.dumps({
                "custom_id": "IMAGE#123#RECEIPT#00001#LINE#00003",
                "error": {"type": "timeout", "message": "Request timed out"}
            }),
        ]
        mock_error = Mock()
        mock_error.read.return_value = "\n".join(error_content).encode("utf-8")
        
        # Set up mock returns
        def file_content_side_effect(file_id):
            if file_id == "output_file_123":
                return mock_output
            elif file_id == "error_file_456":
                return mock_error
            return None
        
        client_manager.openai.files.content.side_effect = file_content_side_effect
        
        # Process partial results
        successful, failed = process_partial_results("batch_123", client_manager)
        
        # Verify results
        assert len(successful) == 1  # Only one with valid embedding
        assert successful[0]["custom_id"] == "IMAGE#123#RECEIPT#00001#LINE#00001"
        assert successful[0]["embedding"] == [0.1, 0.2, 0.3]
        
        assert len(failed) == 2  # One from output with None, one from error file
        assert "IMAGE#123#RECEIPT#00001#LINE#00002" in failed
        assert "IMAGE#123#RECEIPT#00001#LINE#00003" in failed
    
    def test_process_partial_results_no_files(self) -> None:
        """Test processing when no output or error files exist."""
        client_manager = MagicMock()
        mock_batch = Mock()
        mock_batch.output_file_id = None
        mock_batch.error_file_id = None
        client_manager.openai.batches.retrieve.return_value = mock_batch
        
        successful, failed = process_partial_results("batch_123", client_manager)
        
        assert successful == []
        assert failed == []


class TestStatusHandlers:
    """Test individual status handler functions."""
    
    def test_handle_completed_status(self) -> None:
        """Test handling of completed batch status."""
        client_manager = MagicMock()
        
        result = handle_completed_status(
            "batch_123",
            "openai_batch_456",
            client_manager
        )
        
        assert result["action"] == "process_results"
        assert result["status"] == "completed"
        assert result["next_step"] == "download_and_store"
        assert result["should_continue_processing"] is True
    
    def test_handle_failed_status(self) -> None:
        """Test handling of failed batch status."""
        client_manager = MagicMock()
        
        # Mock batch summary
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        # Mock error processing
        with patch(
            "receipt_label.embedding.common.batch_status_handler.process_error_file"
        ) as mock_process_error:
            mock_process_error.return_value = {
                "error_count": 5,
                "error_types": {"rate_limit": 3, "invalid_request": 2},
                "error_details": [],
                "sample_errors": [{"custom_id": "test", "error_type": "rate_limit"}]
            }
            
            result = handle_failed_status(
                "batch_123",
                "openai_batch_456",
                client_manager
            )
        
        assert result["action"] == "handle_failure"
        assert result["status"] == "failed"
        assert result["error_count"] == 5
        assert result["should_continue_processing"] is False
        
        # Verify batch status was updated
        assert mock_batch_summary.status == BatchStatus.FAILED
        client_manager.dynamo.update_batch_summary.assert_called_once()
    
    def test_handle_expired_status(self) -> None:
        """Test handling of expired batch status."""
        client_manager = MagicMock()
        
        # Mock batch summary
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        # Mock partial results processing
        with patch(
            "receipt_label.embedding.common.batch_status_handler.process_partial_results"
        ) as mock_process_partial:
            mock_process_partial.return_value = (
                [{"custom_id": "test1", "embedding": [0.1, 0.2]}],  # successful
                ["test2", "test3"]  # failed
            )
            
            result = handle_expired_status(
                "batch_123",
                "openai_batch_456",
                client_manager
            )
        
        assert result["action"] == "process_partial"
        assert result["status"] == "expired"
        assert result["successful_count"] == 1
        assert result["failed_count"] == 2
        assert result["should_continue_processing"] is True
        
        # Verify batch status was updated
        assert mock_batch_summary.status == BatchStatus.EXPIRED
    
    def test_handle_in_progress_status(self) -> None:
        """Test handling of in-progress statuses."""
        client_manager = MagicMock()
        
        # Mock batch summary
        submitted_time = datetime.now(timezone.utc) - timedelta(hours=10)
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=submitted_time,
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        result = handle_in_progress_status(
            "batch_123",
            "openai_batch_456",
            "in_progress",
            client_manager
        )
        
        assert result["action"] == "wait"
        assert result["status"] == "in_progress"
        assert 9.9 < result["hours_elapsed"] < 10.1  # Allow for small time diff
        assert result["next_step"] == "poll_again_later"
        assert result["should_continue_processing"] is False
        
        # Verify batch status was updated
        assert mock_batch_summary.status == BatchStatus.IN_PROGRESS
    
    def test_handle_in_progress_status_near_timeout(self) -> None:
        """Test warning when batch approaches 24h limit."""
        client_manager = MagicMock()
        
        # Mock batch submitted 22 hours ago
        submitted_time = datetime.now(timezone.utc) - timedelta(hours=22)
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=submitted_time,
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        with patch("receipt_label.embedding.common.batch_status_handler.logger") as mock_logger:
            result = handle_in_progress_status(
                "batch_123",
                "openai_batch_456",
                "in_progress",
                client_manager
            )
            
            # Should log warning about approaching limit
            mock_logger.warning.assert_called()
            warning_call = mock_logger.warning.call_args[0][0]
            assert "approaching 24h limit" in warning_call
    
    def test_handle_cancelled_status(self) -> None:
        """Test handling of cancelled/canceling statuses."""
        client_manager = MagicMock()
        
        # Mock batch summary
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        result = handle_cancelled_status(
            "batch_123",
            "openai_batch_456",
            "cancelled",
            client_manager
        )
        
        assert result["action"] == "handle_cancellation"
        assert result["status"] == "cancelled"
        assert result["next_step"] == "cleanup_or_retry"
        assert result["should_continue_processing"] is False
        
        # Verify batch status was updated
        assert mock_batch_summary.status == BatchStatus.CANCELLED


class TestHandleBatchStatus:
    """Test the central batch status handler."""
    
    @pytest.mark.parametrize(
        "status,expected_action",
        [
            ("completed", "process_results"),
            ("failed", "handle_failure"),
            ("expired", "process_partial"),
            ("validating", "wait"),
            ("in_progress", "wait"),
            ("finalizing", "wait"),
            ("canceling", "handle_cancellation"),
            ("cancelled", "handle_cancellation"),
        ],
    )
    def test_handle_batch_status_routing(
        self,
        status: str,
        expected_action: str,
    ) -> None:
        """Test that statuses route to correct handlers."""
        client_manager = MagicMock()
        
        # Mock batch summary for statuses that need it
        mock_batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.PENDING,
            result_file_id="",
            receipt_refs=[]
        )
        client_manager.dynamo.get_batch_summary.return_value = mock_batch_summary
        
        # Mock supporting functions
        with patch(
            "receipt_label.embedding.common.batch_status_handler.process_error_file"
        ) as mock_error:
            mock_error.return_value = {
                "error_count": 0,
                "error_types": {},
                "error_details": [],
                "sample_errors": []
            }
            
            with patch(
                "receipt_label.embedding.common.batch_status_handler.process_partial_results"
            ) as mock_partial:
                mock_partial.return_value = ([], [])
                
                result = handle_batch_status(
                    "batch_123",
                    "openai_batch_456",
                    status,
                    client_manager
                )
        
        assert result["action"] == expected_action
    
    def test_handle_batch_status_unknown(self) -> None:
        """Test handling of unknown status."""
        client_manager = MagicMock()
        
        with pytest.raises(ValueError, match="Unknown batch status"):
            handle_batch_status(
                "batch_123",
                "openai_batch_456",
                "unknown_status",
                client_manager
            )


class TestMarkItemsForRetry:
    """Test marking failed items for retry."""
    
    def test_mark_lines_for_retry(self) -> None:
        """Test marking failed lines for retry."""
        client_manager = MagicMock()
        
        # Mock line data
        mock_line = Mock()
        mock_line.line_id = 1
        mock_line.embedding_status = EmbeddingStatus.PENDING
        client_manager.dynamo.get_lines_from_receipt.return_value = [mock_line]
        
        failed_ids = ["IMAGE#123#RECEIPT#00001#LINE#00001"]
        
        marked = mark_items_for_retry(failed_ids, "line", client_manager)
        
        assert marked == 1
        assert mock_line.embedding_status == EmbeddingStatus.FAILED
        client_manager.dynamo.update_lines.assert_called_once()
    
    def test_mark_words_for_retry(self) -> None:
        """Test marking failed words for retry."""
        client_manager = MagicMock()
        
        # Mock word data
        mock_word = Mock()
        mock_word.word_id = 1
        mock_word.embedding_status = EmbeddingStatus.PENDING
        client_manager.dynamo.get_words_from_receipt.return_value = [mock_word]
        
        failed_ids = ["IMAGE#123#RECEIPT#00001#LINE#00001#WORD#00001"]
        
        marked = mark_items_for_retry(failed_ids, "word", client_manager)
        
        assert marked == 1
        assert mock_word.embedding_status == EmbeddingStatus.FAILED
        client_manager.dynamo.update_words.assert_called_once()
    
    def test_mark_items_with_invalid_id(self) -> None:
        """Test handling of invalid custom IDs."""
        client_manager = MagicMock()
        
        failed_ids = ["INVALID_ID_FORMAT", "ALSO_INVALID"]
        
        marked = mark_items_for_retry(failed_ids, "line", client_manager)
        
        assert marked == 0  # No items marked due to invalid IDs


class TestShouldRetryBatch:
    """Test batch retry decision logic."""
    
    def test_should_retry_failed_batch(self) -> None:
        """Test that failed batches should be retried."""
        batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.FAILED,
            result_file_id="",
            receipt_refs=[]
        )
        
        assert should_retry_batch(batch_summary) is True
    
    def test_should_retry_expired_batch(self) -> None:
        """Test that expired batches should be retried."""
        batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.EXPIRED,
            result_file_id="",
            receipt_refs=[]
        )
        
        assert should_retry_batch(batch_summary) is True
    
    def test_should_not_retry_completed_batch(self) -> None:
        """Test that completed batches should not be retried."""
        batch_summary = BatchSummary(
            batch_id="batch_123",
            batch_type="WORD_EMBEDDING",
            openai_batch_id="openai_batch_456",
            submitted_at=datetime.now(timezone.utc),
            status=BatchStatus.COMPLETED,
            result_file_id="",
            receipt_refs=[]
        )
        
        assert should_retry_batch(batch_summary) is False