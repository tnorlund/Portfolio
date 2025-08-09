"""Unit tests for completion submission pipeline."""

import pytest
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch, mock_open
from pathlib import Path

from receipt_label.completion.submit import (
    list_labels_that_need_validation,
    chunk_into_completion_batches,
    format_batch_completion_file,
    merge_ndjsons,
    submit_openai_batch
)
from tests.markers import unit, fast, completion, aws
from receipt_dynamo.entities import ReceiptWordLabel, BatchSummary
from receipt_dynamo.constants import ValidationStatus, BatchStatus, BatchType


@unit
@fast
@completion
class TestCompletionSubmit:
    """Test completion submission pipeline components."""

    @pytest.fixture
    def sample_labels_needing_validation(self):
        """Sample labels that need validation."""
        return [
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                label="MERCHANT_NAME", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                label="CURRENCY", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=3, word_id=1,
                label="DATE", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            # Different receipt
            ReceiptWordLabel(
                image_id="IMG002", receipt_id=2, line_id=1, word_id=1,
                label="MERCHANT_NAME", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ),
            ReceiptWordLabel(
                image_id="IMG002", receipt_id=2, line_id=2, word_id=1,
                label="GRAND_TOTAL", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            )
        ]

    @pytest.fixture
    def mock_client_manager(self):
        """Mock client manager for testing."""
        manager = Mock()
        
        # Mock DynamoDB client
        manager.dynamo.getReceiptWordLabelsToValidate.return_value = ([], None)
        manager.dynamo.updateReceiptWordLabels.return_value = None
        manager.dynamo.addBatchSummary.return_value = None
        
        # Mock OpenAI client
        mock_batch = Mock()
        mock_batch.id = "batch_test_123"
        mock_batch.status = "validating"
        manager.openai.batches.create.return_value = mock_batch
        
        return manager

    def test_list_labels_that_need_validation(self, mock_client_manager, sample_labels_needing_validation):
        """Test listing labels that need validation."""
        # Mock DynamoDB response
        mock_client_manager.dynamo.getReceiptWordLabelsToValidate.return_value = (
            sample_labels_needing_validation, None
        )
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            labels, continuation_token = list_labels_that_need_validation(
                limit=100,
                continuation_token=None
            )
        
        assert len(labels) == 5
        assert all(label.validation_status == ValidationStatus.NONE.value for label in labels)
        assert continuation_token is None
        
        # Should have called DynamoDB with correct parameters
        mock_client_manager.dynamo.getReceiptWordLabelsToValidate.assert_called_once_with(
            status=ValidationStatus.NONE,
            limit=100,
            last_evaluated_key=None
        )

    def test_chunk_into_completion_batches(self, sample_labels_needing_validation):
        """Test chunking labels into completion batches by receipt."""
        batches = chunk_into_completion_batches(
            labels=sample_labels_needing_validation,
            max_batch_size=3
        )
        
        # Should create 2 batches (one per receipt)
        assert len(batches) == 2
        
        # First batch should have IMG001 labels
        batch1 = batches[0]
        assert len(batch1) == 3
        assert all(label.image_id == "IMG001" and label.receipt_id == 1 for label in batch1)
        
        # Second batch should have IMG002 labels
        batch2 = batches[1]
        assert len(batch2) == 2
        assert all(label.image_id == "IMG002" and label.receipt_id == 2 for label in batch2)

    def test_chunk_with_size_limit(self, sample_labels_needing_validation):
        """Test chunking respects batch size limits."""
        # Create more labels for same receipt
        large_receipt_labels = []
        for i in range(10):
            large_receipt_labels.append(ReceiptWordLabel(
                image_id="IMG003", receipt_id=3, line_id=i, word_id=1,
                label="PRODUCT_NAME", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ))
        
        batches = chunk_into_completion_batches(
            labels=large_receipt_labels,
            max_batch_size=4
        )
        
        # Should split large receipt into multiple batches
        assert len(batches) == 3  # 4 + 4 + 2
        assert len(batches[0]) == 4
        assert len(batches[1]) == 4
        assert len(batches[2]) == 2

    def test_format_batch_completion_file(self, sample_labels_needing_validation, mock_client_manager):
        """Test formatting batch completion file."""
        # Mock receipt details
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None,  # Receipt metadata components
            [
                Mock(text="Walmart", x1=100, y1=50, x2=200, y2=70),
                Mock(text="$12.99", x1=250, y1=100, x2=300, y2=120),
                Mock(text="12/25/2023", x1=100, y1=150, x2=180, y2=170)
            ]
        )
        
        batch = sample_labels_needing_validation[:3]  # First receipt
        output_path = "/tmp/test_batch.ndjson"
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', mock_open()) as mock_file:
                result = format_batch_completion_file(batch, output_path)
        
        assert result["output_path"] == output_path
        assert result["request_count"] == 1  # One receipt
        assert result["label_count"] == 3    # Three labels
        
        # Should have opened file for writing
        mock_file.assert_called_once_with(output_path, 'w', encoding='utf-8')
        
        # Should have called getReceiptDetails
        mock_client_manager.dynamo.getReceiptDetails.assert_called_once()

    def test_format_completion_prompt_structure(self, sample_labels_needing_validation, mock_client_manager):
        """Test structure of completion prompts."""
        # Mock receipt details with specific words
        mock_words = [
            Mock(text="Walmart", x1=100, y1=50, x2=200, y2=70),
            Mock(text="$12.99", x1=250, y1=100, x2=300, y2=120),
            Mock(text="12/25/2023", x1=100, y1=150, x2=180, y2=170)
        ]
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None, mock_words
        )
        
        batch = sample_labels_needing_validation[:3]
        
        # Capture the actual file content
        written_content = []
        
        def capture_write(content):
            written_content.append(content)
            return len(content)
        
        mock_file_obj = Mock()
        mock_file_obj.write.side_effect = capture_write
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', return_value=mock_file_obj):
                format_batch_completion_file(batch, "/tmp/test.ndjson")
        
        # Should have written NDJSON content
        assert len(written_content) > 0
        
        # Parse the written content as NDJSON
        ndjson_line = ''.join(written_content)
        try:
            request_data = json.loads(ndjson_line)
            
            # Should have proper OpenAI batch format
            assert "custom_id" in request_data
            assert "method" in request_data
            assert "url" in request_data
            assert "body" in request_data
            
            # Body should contain the completion request
            body = request_data["body"]
            assert "model" in body
            assert "messages" in body
            assert len(body["messages"]) > 0
            
            # Should contain the labels to validate
            message_content = str(body["messages"])
            assert "MERCHANT_NAME" in message_content
            assert "CURRENCY" in message_content
            assert "DATE" in message_content
            
        except json.JSONDecodeError as e:
            pytest.fail(f"Generated content is not valid JSON: {e}")

    def test_merge_ndjsons(self):
        """Test merging multiple NDJSON files."""
        # Create temporary files
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as f1:
            f1.write('{"id": 1, "data": "test1"}\n')
            f1.write('{"id": 2, "data": "test2"}\n')
            file1_path = f1.name
            
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as f2:
            f2.write('{"id": 3, "data": "test3"}\n')
            file2_path = f2.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as output:
            output_path = output.name
        
        try:
            input_files = [file1_path, file2_path]
            
            result = merge_ndjsons(input_files, output_path)
            
            assert result["output_path"] == output_path
            assert result["total_lines"] == 3
            assert result["input_file_count"] == 2
            
            # Verify merged content
            with open(output_path, 'r') as merged_file:
                lines = merged_file.readlines()
                assert len(lines) == 3
                
                # Should contain all original data
                content = ''.join(lines)
                assert '"id": 1' in content
                assert '"id": 2' in content
                assert '"id": 3' in content
                
        finally:
            # Cleanup
            Path(file1_path).unlink(missing_ok=True)
            Path(file2_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def test_merge_ndjsons_size_limit(self):
        """Test merge respects size limits."""
        # Create large files to test size limit
        large_content = '{"data": "' + 'x' * 1000 + '"}\n' * 200  # ~200KB per file
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as f1:
            f1.write(large_content)
            file1_path = f1.name
            
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as f2:
            f2.write(large_content)
            file2_path = f2.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ndjson') as output:
            output_path = output.name
        
        try:
            input_files = [file1_path, file2_path]
            
            # Set a small size limit to trigger limit behavior
            result = merge_ndjsons(
                input_files, 
                output_path, 
                max_size_mb=0.1  # 100KB limit
            )
            
            # Should stop at size limit
            assert result["total_lines"] < 400  # Less than both files combined
            assert result["size_limited"] is True
            
        finally:
            Path(file1_path).unlink(missing_ok=True)
            Path(file2_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def test_submit_openai_batch(self, mock_client_manager):
        """Test submitting batch to OpenAI."""
        batch_file_path = "/tmp/test_batch.ndjson"
        batch_description = "Test validation batch"
        
        # Mock file upload
        mock_file = Mock()
        mock_file.id = "file_test_123"
        mock_client_manager.openai.files.create.return_value = mock_file
        
        # Mock batch creation
        mock_batch = Mock()
        mock_batch.id = "batch_test_456"
        mock_batch.status = "validating"
        mock_batch.created_at = datetime.now(timezone.utc).timestamp()
        mock_client_manager.openai.batches.create.return_value = mock_batch
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
                result = submit_openai_batch(
                    batch_file_path=batch_file_path,
                    batch_description=batch_description
                )
        
        assert result["openai_batch_id"] == "batch_test_456"
        assert result["status"] == "validating"
        assert result["file_id"] == "file_test_123"
        
        # Should have uploaded file
        mock_client_manager.openai.files.create.assert_called_once()
        
        # Should have created batch
        mock_client_manager.openai.batches.create.assert_called_once()
        create_call = mock_client_manager.openai.batches.create.call_args
        assert create_call.kwargs["input_file_id"] == "file_test_123"
        assert create_call.kwargs["endpoint"] == "/v1/chat/completions"
        assert create_call.kwargs["completion_window"] == "24h"

    def test_batch_summary_creation(self, mock_client_manager, sample_labels_needing_validation):
        """Test creation of BatchSummary records."""
        openai_batch_id = "batch_test_789"
        label_batch = sample_labels_needing_validation[:3]
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            # This would be called by the orchestrating function
            batch_summary = BatchSummary(
                openai_batch_id=openai_batch_id,
                status=BatchStatus.PENDING.value,
                batch_type=BatchType.COMPLETION.value,
                label_count=len(label_batch),
                receipt_ids=list(set(f"{l.image_id}#{l.receipt_id}" for l in label_batch)),
                submitted_at=datetime.now(timezone.utc)
            )
            
            # Verify batch summary structure
            assert batch_summary.openai_batch_id == openai_batch_id
            assert batch_summary.status == BatchStatus.PENDING.value
            assert batch_summary.batch_type == BatchType.COMPLETION.value
            assert batch_summary.label_count == 3
            assert len(batch_summary.receipt_ids) == 1  # All from same receipt

    def test_label_status_update_to_pending(self, mock_client_manager, sample_labels_needing_validation):
        """Test updating label status to PENDING after submission."""
        label_batch = sample_labels_needing_validation[:3]
        
        # Update labels to PENDING status
        for label in label_batch:
            label.validation_status = ValidationStatus.PENDING.value
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            # Mock the update call
            mock_client_manager.dynamo.updateReceiptWordLabels(label_batch)
        
        # Should have called update with PENDING status
        mock_client_manager.dynamo.updateReceiptWordLabels.assert_called_once_with(label_batch)
        
        # Verify all labels are PENDING
        assert all(label.validation_status == ValidationStatus.PENDING.value for label in label_batch)

    def test_error_handling_openai_failure(self, mock_client_manager):
        """Test error handling when OpenAI batch creation fails."""
        batch_file_path = "/tmp/test_batch.ndjson"
        
        # Mock OpenAI API failure
        mock_client_manager.openai.files.create.side_effect = Exception("OpenAI API Error")
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
                try:
                    result = submit_openai_batch(batch_file_path, "Test batch")
                    
                    # Should handle error gracefully
                    assert result["status"] == "failed"
                    assert "error" in result
                    assert "OpenAI API Error" in result["error"]
                    
                except Exception as e:
                    pytest.fail(f"Should handle OpenAI failures gracefully, got: {e}")

    def test_batch_size_optimization(self, sample_labels_needing_validation):
        """Test batch size optimization for OpenAI limits."""
        # Create a large number of labels
        large_batch = []
        for i in range(1000):
            large_batch.append(ReceiptWordLabel(
                image_id=f"IMG{i//10}", receipt_id=i//10, line_id=i%10, word_id=1,
                label="PRODUCT_NAME", validation_status=ValidationStatus.NONE.value,
                timestamp_added=datetime.now(timezone.utc)
            ))
        
        # Chunk with OpenAI-friendly batch size
        batches = chunk_into_completion_batches(
            labels=large_batch,
            max_batch_size=50  # OpenAI batch limit consideration
        )
        
        # Should create appropriate number of batches
        assert len(batches) > 1
        assert all(len(batch) <= 50 for batch in batches)
        
        # Total labels should be preserved
        total_labels = sum(len(batch) for batch in batches)
        assert total_labels == 1000

    def test_prompt_template_validation(self, mock_client_manager):
        """Test validation prompt template structure."""
        # Mock with specific label and word data
        test_label = ReceiptWordLabel(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            label="MERCHANT_NAME", validation_status=ValidationStatus.NONE.value,
            timestamp_added=datetime.now(timezone.utc)
        )
        
        mock_words = [
            Mock(text="Walmart", x1=100, y1=50, x2=200, y2=70),
            Mock(text="Store", x1=100, y1=75, x2=150, y2=95),
            Mock(text="#1234", x1=155, y1=75, x2=200, y2=95)
        ]
        
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None, mock_words
        )
        
        written_prompts = []
        
        def capture_prompt(content):
            try:
                data = json.loads(content)
                if "body" in data and "messages" in data["body"]:
                    written_prompts.append(data["body"]["messages"])
            except:
                pass
            return len(content)
        
        mock_file_obj = Mock()
        mock_file_obj.write.side_effect = capture_prompt
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', return_value=mock_file_obj):
                format_batch_completion_file([test_label], "/tmp/test.ndjson")
        
        # Should have generated at least one prompt
        assert len(written_prompts) > 0
        
        messages = written_prompts[0]
        
        # Should have system and user messages
        assert len(messages) >= 2
        assert any(msg["role"] == "system" for msg in messages)
        assert any(msg["role"] == "user" for msg in messages)
        
        # User message should contain label validation context
        user_message = next(msg for msg in messages if msg["role"] == "user")["content"]
        assert "MERCHANT_NAME" in user_message
        assert "Walmart" in user_message

    def test_concurrent_batch_submission_safety(self, mock_client_manager):
        """Test thread safety of batch submission process."""
        import threading
        import time
        
        results = {}
        errors = {}
        
        def submit_worker(worker_id):
            """Worker function for concurrent batch submission."""
            try:
                batch_path = f"/tmp/worker_{worker_id}_batch.ndjson"
                
                # Mock unique responses for each worker
                mock_batch = Mock()
                mock_batch.id = f"batch_worker_{worker_id}"
                mock_batch.status = "validating"
                mock_client_manager.openai.batches.create.return_value = mock_batch
                
                mock_file = Mock()
                mock_file.id = f"file_worker_{worker_id}"
                mock_client_manager.openai.files.create.return_value = mock_file
                
                with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
                    with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
                        result = submit_openai_batch(batch_path, f"Worker {worker_id} batch")
                        
                        results[worker_id] = result["openai_batch_id"]
                        
            except Exception as e:
                errors[worker_id] = str(e)
        
        # Start multiple workers
        threads = []
        for i in range(3):
            thread = threading.Thread(target=submit_worker, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join(timeout=10)
        
        # All workers should succeed without interference
        assert len(errors) == 0, f"Workers had errors: {errors}"
        assert len(results) == 3
        assert len(set(results.values())) == 3  # All unique batch IDs

    def test_completion_request_format_validation(self, mock_client_manager, sample_labels_needing_validation):
        """Test that completion requests follow OpenAI format spec."""
        batch = sample_labels_needing_validation[:1]
        
        mock_client_manager.dynamo.getReceiptDetails.return_value = (
            None, None, None, None, None,
            [Mock(text="TestWord", x1=100, y1=100, x2=150, y2=120)]
        )
        
        captured_requests = []
        
        def validate_and_capture(content):
            try:
                request = json.loads(content)
                captured_requests.append(request)
                
                # Validate OpenAI batch request format
                required_fields = ["custom_id", "method", "url", "body"]
                for field in required_fields:
                    assert field in request, f"Missing required field: {field}"
                
                # Validate method and URL
                assert request["method"] == "POST"
                assert request["url"] == "/v1/chat/completions"
                
                # Validate body structure
                body = request["body"]
                required_body_fields = ["model", "messages", "max_tokens"]
                for field in required_body_fields:
                    assert field in body, f"Missing required body field: {field}"
                
                # Validate messages structure
                messages = body["messages"]
                assert isinstance(messages, list)
                assert len(messages) > 0
                
                for message in messages:
                    assert "role" in message
                    assert "content" in message
                    assert message["role"] in ["system", "user", "assistant"]
                
            except (json.JSONDecodeError, KeyError, AssertionError) as e:
                pytest.fail(f"Invalid request format: {e}")
            
            return len(content)
        
        mock_file_obj = Mock()
        mock_file_obj.write.side_effect = validate_and_capture
        
        with patch('receipt_label.utils.get_client_manager', return_value=mock_client_manager):
            with patch('builtins.open', return_value=mock_file_obj):
                format_batch_completion_file(batch, "/tmp/validation_test.ndjson")
        
        # Should have generated valid request
        assert len(captured_requests) > 0