"""Unit tests for completion polling pipeline."""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from types import SimpleNamespace

from receipt_label.completion.poll import (
    list_pending_completion_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    update_pending_labels,
    update_valid_labels,
    update_invalid_labels,
    write_completion_batch_results,
    update_batch_summary
)
from receipt_label.tests.markers import unit, fast, completion
from receipt_dynamo.entities import ReceiptWordLabel, BatchSummary, CompletionBatchResult
from receipt_dynamo.constants import ValidationStatus, BatchStatus, BatchType


@unit
@fast
@completion
class TestCompletionPoll:
    """Test completion polling pipeline components."""

    @pytest.fixture
    def sample_pending_batches(self):
        """Sample pending completion batches."""
        return [
            BatchSummary(
                batch_id="batch_001",
                openai_batch_id="batch_openai_123",
                status=BatchStatus.PENDING.value,
                batch_type=BatchType.COMPLETION.value,
                label_count=10,
                submitted_at=datetime.now(timezone.utc)
            ),
            BatchSummary(
                batch_id="batch_002", 
                openai_batch_id="batch_openai_456",
                status=BatchStatus.PENDING.value,
                batch_type=BatchType.COMPLETION.value,
                label_count=25,
                submitted_at=datetime.now(timezone.utc)
            )
        ]

    @pytest.fixture
    def sample_openai_batch_results(self):
        """Sample OpenAI batch results in NDJSON format."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                "response": {
                    "body": {
                        "choices": [{
                            "message": {
                                "content": json.dumps({
                                    "results": [
                                        {
                                            "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                                            "is_valid": True,
                                            "confidence": 0.95
                                        }
                                    ]
                                })
                            }
                        }]
                    }
                }
            },
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001#LABEL#CURRENCY", 
                "response": {
                    "body": {
                        "choices": [{
                            "message": {
                                "content": json.dumps({
                                    "results": [
                                        {
                                            "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001#LABEL#CURRENCY",
                                            "is_valid": False,
                                            "confidence": 0.30,
                                            "correct_label": "GRAND_TOTAL"
                                        }
                                    ]
                                })
                            }
                        }]
                    }
                }
            }
        ]
        return '\n'.join(json.dumps(result) for result in results)

    @pytest.fixture  
    def sample_receipt_labels(self):
        """Sample receipt labels for testing."""
        return [
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                label="MERCHANT_NAME", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                label="CURRENCY", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=3, word_id=1,
                label="DATE", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            )
        ]

    @pytest.fixture
    def mock_client_manager(self):
        """Mock client manager for testing."""
        manager = Mock()
        
        # Mock DynamoDB operations
        manager.dynamo.getBatchSummariesByStatus.return_value = ([], None)
        manager.dynamo.updateReceiptWordLabels.return_value = None
        manager.dynamo.addReceiptWordLabels.return_value = None
        manager.dynamo.updateBatchSummary.return_value = None
        manager.dynamo.addCompletionBatchResults.return_value = None
        
        # Mock OpenAI operations
        mock_batch = Mock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file_output_123"
        manager.openai.batches.retrieve.return_value = mock_batch
        
        manager.openai.files.content.return_value = b""
        
        # Mock ChromaDB operations
        manager.chroma.get_by_ids.return_value = {
            'ids': ['test_id'],
            'metadatas': [{'valid_labels': [], 'invalid_labels': []}]
        }
        mock_collection = Mock()
        mock_collection.update.return_value = None
        manager.chroma.get_collection.return_value = mock_collection
        
        return manager

    def test_list_pending_completion_batches(self, mock_client_manager, sample_pending_batches):
        """Test listing pending completion batches."""
        mock_client_manager.dynamo.getBatchSummariesByStatus.return_value = (
            sample_pending_batches, None
        )
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            batches = list_pending_completion_batches()
        
        assert len(batches) == 2
        assert all(batch.status == BatchStatus.PENDING.value for batch in batches)
        assert all(batch.batch_type == BatchType.COMPLETION.value for batch in batches)
        
        # Should have called DynamoDB with correct parameters
        mock_client_manager.dynamo.getBatchSummariesByStatus.assert_called_once_with(
            status=BatchStatus.PENDING,
            batch_type=BatchType.COMPLETION
        )

    def test_get_openai_batch_status(self, mock_client_manager):
        """Test getting OpenAI batch status."""
        openai_batch_id = "batch_test_123"
        
        # Mock different status responses
        test_statuses = ["validating", "in_progress", "finalizing", "completed", "failed"]
        
        for expected_status in test_statuses:
            mock_batch = Mock()
            mock_batch.status = expected_status
            mock_client_manager.openai.batches.retrieve.return_value = mock_batch
            
            with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
                status = get_openai_batch_status(openai_batch_id)
            
            assert status == expected_status
            
            # Should have called OpenAI with correct batch ID
            mock_client_manager.openai.batches.retrieve.assert_called_with(openai_batch_id)

    def test_download_openai_batch_result(self, mock_client_manager, sample_openai_batch_results, sample_receipt_labels):
        """Test downloading and parsing OpenAI batch results."""
        batch_summary = BatchSummary(
            batch_id="test_batch",
            openai_batch_id="batch_openai_test",
            status=BatchStatus.PENDING.value,
            batch_type=BatchType.COMPLETION.value
        )
        
        # Mock OpenAI file content
        mock_client_manager.openai.files.content.return_value = sample_openai_batch_results.encode('utf-8')
        
        # Mock receipt details
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None, sample_receipt_labels
        )
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            pending_labels, valid_labels, invalid_labels = download_openai_batch_result(batch_summary)
        
        # Should parse results correctly
        assert len(valid_labels) == 1   # One valid result
        assert len(invalid_labels) == 1 # One invalid result
        
        # Valid result should have correct structure
        valid_result = valid_labels[0]
        assert valid_result.label_from_dynamo.label == "MERCHANT_NAME"
        assert valid_result.result["is_valid"] is True
        assert valid_result.result["confidence"] == 0.95
        
        # Invalid result should have correct structure
        invalid_result = invalid_labels[0]
        assert invalid_result.label_from_dynamo.label == "CURRENCY"
        assert invalid_result.result["is_valid"] is False
        assert invalid_result.result["correct_label"] == "GRAND_TOTAL"

    def test_update_pending_labels(self, mock_client_manager):
        """Test updating pending labels back to NONE status."""
        pending_labels = [
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=3, word_id=1,
                label="DATE", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_pending_labels(pending_labels)
        
        # Should update status to NONE
        for label in pending_labels:
            assert label.validation_status == ValidationStatus.NONE.value
            assert label.label_proposed_by == "COMPLETION_BATCH"
        
        # Should have called DynamoDB update
        mock_client_manager.dynamo.updateReceiptWordLabels.assert_called_once_with(pending_labels)

    def test_update_valid_labels(self, mock_client_manager, sample_receipt_labels):
        """Test updating valid labels and ChromaDB metadata."""
        # Create valid label results
        from receipt_label.completion.poll import LabelResult
        
        valid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[0],  # MERCHANT_NAME
                result={"is_valid": True, "confidence": 0.95},
                other_labels=[]
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_valid_labels(valid_results)
        
        # Should update label status to VALID
        assert sample_receipt_labels[0].validation_status == ValidationStatus.VALID.value
        assert sample_receipt_labels[0].label_proposed_by == "COMPLETION_BATCH"
        
        # Should have updated DynamoDB
        mock_client_manager.dynamo.updateReceiptWordLabels.assert_called()
        
        # Should have updated ChromaDB metadata
        mock_client_manager.chroma.get_by_ids.assert_called()
        mock_client_manager.chroma.get_collection.assert_called()

    def test_update_invalid_labels(self, mock_client_manager, sample_receipt_labels):
        """Test updating invalid labels and creating corrections."""
        from receipt_label.completion.poll import LabelResult
        
        # Create invalid label result with correction
        invalid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[1],  # CURRENCY
                result={
                    "is_valid": False,
                    "confidence": 0.30,
                    "correct_label": "GRAND_TOTAL"
                },
                other_labels=[]
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_invalid_labels(invalid_results)
        
        # Should update label status to INVALID
        assert sample_receipt_labels[1].validation_status == ValidationStatus.INVALID.value
        assert sample_receipt_labels[1].label_proposed_by == "COMPLETION_BATCH"
        
        # Should have updated existing labels
        mock_client_manager.dynamo.updateReceiptWordLabels.assert_called()
        
        # Should have added new corrected labels
        mock_client_manager.dynamo.addReceiptWordLabels.assert_called()

    def test_invalid_label_needs_review_logic(self, mock_client_manager, sample_receipt_labels):
        """Test NEEDS_REVIEW status when other labels are already invalid."""
        from receipt_label.completion.poll import LabelResult
        
        # Create scenario where other labels are already INVALID
        other_invalid_label = ReceiptWordLabel(
            image_id="IMG001", receipt_id=1, line_id=2, word_id=2,
            label="OTHER_LABEL", validation_status=ValidationStatus.INVALID.value,
            timestamp_added=datetime.now(timezone.utc)
        )
        
        invalid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[1],  # CURRENCY
                result={"is_valid": False, "confidence": 0.30},
                other_labels=[other_invalid_label]  # Another invalid label exists
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_invalid_labels(invalid_results)
        
        # Should set status to NEEDS_REVIEW instead of INVALID
        assert sample_receipt_labels[1].validation_status == ValidationStatus.NEEDS_REVIEW.value

    def test_write_completion_batch_results(self, mock_client_manager, sample_receipt_labels):
        """Test writing completion batch results to database."""
        from receipt_label.completion.poll import LabelResult
        
        batch_summary = BatchSummary(
            batch_id="test_batch",
            openai_batch_id="batch_test",
            status=BatchStatus.PENDING.value,
            batch_type=BatchType.COMPLETION.value
        )
        
        # Create mixed results
        valid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[0],
                result={
                    "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                    "is_valid": True,
                    "confidence": 0.95
                },
                other_labels=[]
            )
        ]
        
        invalid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[1],
                result={
                    "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001#LABEL#CURRENCY",
                    "is_valid": False,
                    "confidence": 0.30,
                    "correct_label": "GRAND_TOTAL"
                },
                other_labels=[]
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            write_completion_batch_results(batch_summary, valid_results, invalid_results)
        
        # Should have written completion results
        mock_client_manager.dynamo.addCompletionBatchResults.assert_called()
        
        # Verify the completion results structure
        call_args = mock_client_manager.dynamo.addCompletionBatchResults.call_args
        completion_results = call_args[0][0]  # First argument
        
        assert len(completion_results) == 2  # One valid + one invalid
        
        # Check result structure
        for result in completion_results:
            assert isinstance(result, CompletionBatchResult)
            assert result.batch_id == "test_batch"
            assert result.status == BatchStatus.COMPLETED.value
            assert result.validated_at is not None

    def test_update_batch_summary(self, mock_client_manager):
        """Test updating batch summary to completed status."""
        batch_summary = BatchSummary(
            batch_id="test_batch",
            openai_batch_id="batch_test",
            status=BatchStatus.PENDING.value,
            batch_type=BatchType.COMPLETION.value
        )
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_batch_summary(batch_summary)
        
        # Should update status to COMPLETED
        assert batch_summary.status == BatchStatus.COMPLETED.value
        
        # Should have called DynamoDB update
        mock_client_manager.dynamo.updateBatchSummary.assert_called_once_with(batch_summary)

    def test_parsing_malformed_openai_results(self, mock_client_manager):
        """Test handling of malformed OpenAI batch results."""
        batch_summary = BatchSummary(
            batch_id="malformed_test",
            openai_batch_id="batch_malformed",
            status=BatchStatus.PENDING.value,
            batch_type=BatchType.COMPLETION.value
        )
        
        # Create malformed results
        malformed_results = '\n'.join([
            '{"invalid": "json"',  # Invalid JSON
            '{"custom_id": "test", "response": {}}',  # Missing body
            '{"custom_id": "test2", "response": {"body": {"choices": []}}}',  # Empty choices
            '',  # Empty line
            '{"custom_id": "valid", "response": {"body": {"choices": [{"message": {"content": "{\\"results\\": []}"}}]}}}'  # Valid
        ])
        
        mock_client_manager.openai.files.content.return_value = malformed_results.encode('utf-8')
        mock_client_manager.dynamo.getReceiptDetails.return_value = (None, None, None, None, None, [])
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            try:
                pending_labels, valid_labels, invalid_labels = download_openai_batch_result(batch_summary)
                
                # Should handle malformed results gracefully
                assert isinstance(pending_labels, list)
                assert isinstance(valid_labels, list) 
                assert isinstance(invalid_labels, list)
                # May have empty results due to malformed data
                
            except Exception as e:
                pytest.fail(f"Should handle malformed results gracefully, got: {e}")

    def test_chromadb_metadata_merge_logic(self, mock_client_manager, sample_receipt_labels):
        """Test ChromaDB metadata merging for valid/invalid labels."""
        from receipt_label.completion.poll import LabelResult
        
        # Mock existing metadata in ChromaDB
        mock_client_manager.chroma.get_by_ids.return_value = {
            'ids': ['test_chroma_id'],
            'metadatas': [{
                'valid_labels': ['EXISTING_VALID'],
                'invalid_labels': ['EXISTING_INVALID']
            }]
        }
        
        valid_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[0],  # MERCHANT_NAME
                result={"is_valid": True, "confidence": 0.95},
                other_labels=[]
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_valid_labels(valid_results)
        
        # Should have called ChromaDB update with merged metadata
        mock_collection = mock_client_manager.chroma.get_collection.return_value
        mock_collection.update.assert_called()
        
        # Verify update was called with correct metadata structure
        update_call = mock_collection.update.call_args
        assert "ids" in update_call.kwargs
        assert "metadatas" in update_call.kwargs

    def test_batch_processing_chunking(self, mock_client_manager):
        """Test that large batches are processed in chunks."""
        # Create large batch of labels
        large_label_batch = []
        for i in range(100):  # More than chunk size of 25
            large_label_batch.append(ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                label="PRODUCT_NAME", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            ))
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            update_pending_labels(large_label_batch)
        
        # Should have called update multiple times (chunked)
        assert mock_client_manager.dynamo.updateReceiptWordLabels.call_count >= 4  # 100/25 = 4 chunks

    def test_error_recovery_partial_failure(self, mock_client_manager):
        """Test error recovery when some operations fail."""
        batch_summary = BatchSummary(
            batch_id="partial_fail_test",
            openai_batch_id="batch_partial_fail", 
            status=BatchStatus.PENDING.value,
            batch_type=BatchType.COMPLETION.value
        )
        
        # Mock partial failure in file download
        mock_client_manager.openai.files.content.side_effect = Exception("Network timeout")
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            try:
                pending_labels, valid_labels, invalid_labels = download_openai_batch_result(batch_summary)
                
                # Should handle failure gracefully and return empty results
                assert len(pending_labels) == 0
                assert len(valid_labels) == 0
                assert len(invalid_labels) == 0
                
            except Exception as e:
                # Or may raise exception that should be handled upstream
                assert "Network timeout" in str(e)

    def test_completion_result_deduplication(self, mock_client_manager, sample_receipt_labels):
        """Test deduplication of completion results by DynamoDB key."""
        from receipt_label.completion.poll import LabelResult
        
        batch_summary = BatchSummary(batch_id="dedup_test", openai_batch_id="batch_dedup")
        
        # Create duplicate results (same label)
        duplicate_results = [
            LabelResult(
                label_from_dynamo=sample_receipt_labels[0],
                result={
                    "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                    "is_valid": True,
                    "confidence": 0.95
                },
                other_labels=[]
            ),
            LabelResult(
                label_from_dynamo=sample_receipt_labels[0],  # Same label
                result={
                    "id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                    "is_valid": True,
                    "confidence": 0.90  # Different confidence, but same key
                },
                other_labels=[]
            )
        ]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            write_completion_batch_results(batch_summary, duplicate_results, [])
        
        # Should deduplicate before writing
        call_args = mock_client_manager.dynamo.addCompletionBatchResults.call_args
        completion_results = call_args[0][0]
        
        # Should only write one result despite two inputs
        assert len(completion_results) == 1

    def test_performance_large_batch_processing(self, mock_client_manager, performance_timer):
        """Test performance with large batches."""
        # Create large batch of results
        large_ndjson_results = []
        for i in range(1000):
            result = {
                "custom_id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}#WORD#00001#LABEL#PRODUCT_NAME",
                "response": {
                    "body": {
                        "choices": [{
                            "message": {
                                "content": json.dumps({
                                    "results": [{
                                        "id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}#WORD#00001#LABEL#PRODUCT_NAME",
                                        "is_valid": True,
                                        "confidence": 0.85
                                    }]
                                })
                            }
                        }]
                    }
                }
            }
            large_ndjson_results.append(json.dumps(result))
        
        large_results_content = '\n'.join(large_ndjson_results)
        mock_client_manager.openai.files.content.return_value = large_results_content.encode('utf-8')
        
        # Mock many labels
        many_labels = [
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                label="PRODUCT_NAME", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc)
            ) for i in range(1000)
        ]
        
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None, many_labels
        )
        
        batch_summary = BatchSummary(
            batch_id="perf_test",
            openai_batch_id="batch_perf",
            status=BatchStatus.PENDING.value
        )
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            performance_timer.start()
            pending_labels, valid_labels, invalid_labels = download_openai_batch_result(batch_summary)
            elapsed = performance_timer.stop()
        
        # Should process large batch efficiently
        assert elapsed < 30.0, f"Large batch processing took {elapsed:.2f}s, should be <30s"
        assert len(valid_labels) > 0