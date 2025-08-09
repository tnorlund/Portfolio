"""Unit tests for line embedding submission pipeline."""

import pytest
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch, mock_open
from pathlib import Path

from receipt_label.embedding.line.submit import (
    generate_batch_id,
    list_receipt_lines_with_no_embeddings,
    chunk_into_line_embedding_batches,
    format_line_context_embedding,
    write_ndjson,
    upload_to_openai,
    submit_openai_batch,
    create_batch_summary,
    add_batch_summary,
    update_line_embedding_status
)
from tests.markers import unit, fast, embedding
from receipt_dynamo.entities import ReceiptLine, BatchSummary
from receipt_dynamo.constants import EmbeddingStatus


@unit
@fast  
@embedding
class TestLineEmbeddingSubmit:
    """Test line embedding submission pipeline components."""

    @pytest.fixture
    def sample_receipt_lines(self):
        """Sample receipt lines for testing."""
        return [
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=1,
                text="Walmart Supercenter",
                x1=50, y1=30, x2=400, y2=55,
                confidence=0.95,
                embedding_status=EmbeddingStatus.NONE.value
            ),
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=2,
                text="123 Main Street",
                x1=75, y1=60, x2=350, y2=80,
                confidence=0.92,
                embedding_status=EmbeddingStatus.NONE.value
            ),
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=3,
                text="Phone: (555) 123-4567",
                x1=100, y1=90, x2=300, y2=110,
                confidence=0.88,
                embedding_status=EmbeddingStatus.NONE.value
            ),
            # Different receipt
            ReceiptLine(
                image_id="IMG002", receipt_id=2, line_id=1,
                text="Target Store",
                x1=80, y1=40, x2=320, y2=65,
                confidence=0.94,
                embedding_status=EmbeddingStatus.NONE.value
            ),
            ReceiptLine(
                image_id="IMG002", receipt_id=2, line_id=2,
                text="Total: $45.67",
                x1=200, y1=400, x2=350, y2=425,
                confidence=0.98,
                embedding_status=EmbeddingStatus.NONE.value
            )
        ]

    @pytest.fixture
    def mock_client_manager(self):
        """Mock client manager for testing."""
        manager = Mock()
        
        # Mock DynamoDB operations
        manager.dynamo.list_receipt_lines_by_embedding_status.return_value = []
        manager.dynamo.update_receipt_lines.return_value = None
        manager.dynamo.add_batch_summary.return_value = None
        
        # Mock OpenAI operations
        mock_file = Mock()
        mock_file.id = "file_line_123"
        manager.openai.files.create.return_value = mock_file
        
        mock_batch = Mock()
        mock_batch.id = "batch_line_456"
        mock_batch.status = "validating"
        mock_batch.created_at = datetime.now(timezone.utc).timestamp()
        manager.openai.batches.create.return_value = mock_batch
        
        return manager

    def test_generate_batch_id(self):
        """Test batch ID generation."""
        batch_id1 = generate_batch_id()
        batch_id2 = generate_batch_id()
        
        assert batch_id1 != batch_id2
        assert len(batch_id1) > 0
        assert len(batch_id2) > 0

    def test_list_receipt_lines_with_no_embeddings(self, mock_client_manager, sample_receipt_lines):
        """Test listing lines that need embeddings."""
        mock_client_manager.dynamo.list_receipt_lines_by_embedding_status.return_value = sample_receipt_lines
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            lines = list_receipt_lines_with_no_embeddings()
        
        assert len(lines) == 5
        assert all(line.embedding_status == EmbeddingStatus.NONE.value for line in lines)
        
        mock_client_manager.dynamo.list_receipt_lines_by_embedding_status.assert_called_once_with(
            EmbeddingStatus.NONE
        )

    def test_chunk_into_line_embedding_batches(self, sample_receipt_lines):
        """Test chunking lines into embedding batches."""
        batches = chunk_into_line_embedding_batches(sample_receipt_lines)
        
        # Should group by image and receipt
        assert "IMG001" in batches
        assert "IMG002" in batches
        assert 1 in batches["IMG001"]
        assert 2 in batches["IMG002"]
        
        # IMG001 receipt 1 should have 3 lines
        assert len(batches["IMG001"][1]) == 3
        # IMG002 receipt 2 should have 2 lines
        assert len(batches["IMG002"][2]) == 2
        
        # Should deduplicate by line_id
        duplicate_line = ReceiptLine(
            image_id="IMG001", receipt_id=1, line_id=1,
            text="Duplicate Line",
            x1=0, y1=0, x2=100, y2=25,
            confidence=0.80,
            embedding_status=EmbeddingStatus.NONE.value
        )
        lines_with_duplicate = sample_receipt_lines + [duplicate_line]
        
        batches_deduped = chunk_into_line_embedding_batches(lines_with_duplicate)
        # Should still have 3 lines (duplicate removed)
        assert len(batches_deduped["IMG001"][1]) == 3

    def test_format_line_context_embedding(self, sample_receipt_lines):
        """Test formatting line context embeddings for OpenAI."""
        lines_to_embed = sample_receipt_lines[:3]  # First 3 lines
        
        inputs = format_line_context_embedding(lines_to_embed)
        
        assert len(inputs) == 3
        
        # Check first input structure
        first_input = inputs[0]
        assert "custom_id" in first_input
        assert first_input["method"] == "POST"
        assert first_input["url"] == "/v1/embeddings"
        assert first_input["body"]["model"] == "text-embedding-3-small"
        assert first_input["body"]["input"] == "Walmart Supercenter"
        
        # Check custom_id format
        expected_id = "IMAGE#IMG001#RECEIPT#00001#LINE#00001"
        assert first_input["custom_id"] == expected_id
        
        # Check second input
        second_input = inputs[1]
        assert second_input["body"]["input"] == "123 Main Street"
        assert second_input["custom_id"] == "IMAGE#IMG001#RECEIPT#00001#LINE#00002"

    def test_write_ndjson(self):
        """Test writing NDJSON file."""
        batch_id = "test-line-batch-123"
        input_data = [
            {"custom_id": "line_1", "data": "value1"},
            {"custom_id": "line_2", "data": "value2"}
        ]
        
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('pathlib.Path.open', mock_file):
                filepath = write_ndjson(batch_id, input_data)
        
        assert filepath == Path(f"/tmp/{batch_id}.ndjson")
        
        # Should write JSON lines
        mock_file.return_value.write.assert_called()
        call_args = mock_file.return_value.write.call_args_list
        assert len(call_args) == 2  # Two JSON objects written

    def test_upload_to_openai(self, mock_client_manager):
        """Test uploading NDJSON file to OpenAI."""
        filepath = Path("/tmp/test-lines.ndjson")
        
        with patch('pathlib.Path.open', mock_open()) as mock_file:
            with patch('receipt_label.embedding.line.submit.get_client_manager',
                       return_value=mock_client_manager):
                result = upload_to_openai(filepath)
        
        assert result.id == "file_line_123"
        
        # Should have called OpenAI files create
        mock_client_manager.openai.files.create.assert_called_once()
        call_kwargs = mock_client_manager.openai.files.create.call_args.kwargs
        assert call_kwargs["purpose"] == "batch"

    def test_submit_openai_batch(self, mock_client_manager):
        """Test submitting batch to OpenAI."""
        file_id = "file_line_123"
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            result = submit_openai_batch(file_id)
        
        assert result.id == "batch_line_456"
        assert result.status == "validating"
        
        # Should have called OpenAI batches create
        mock_client_manager.openai.batches.create.assert_called_once()
        call_kwargs = mock_client_manager.openai.batches.create.call_args.kwargs
        assert call_kwargs["input_file_id"] == file_id
        assert call_kwargs["endpoint"] == "/v1/embeddings"
        assert call_kwargs["completion_window"] == "24h"
        assert call_kwargs["metadata"]["model"] == "text-embedding-3-small"

    def test_create_batch_summary(self):
        """Test creating batch summary from NDJSON file."""
        batch_id = "test-line-batch-123"
        openai_batch_id = "batch_openai_line_456"
        
        # Create mock NDJSON content
        ndjson_content = '\n'.join([
            '{"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001"}',
            '{"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002"}',
            '{"custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001"}',
            '{"invalid": "json"}'  # Should be skipped
        ])
        
        with patch('builtins.open', mock_open(read_data=ndjson_content)):
            summary = create_batch_summary(batch_id, openai_batch_id, "/tmp/test.ndjson")
        
        assert summary.batch_id == batch_id
        assert summary.batch_type == "EMBEDDING"
        assert summary.openai_batch_id == openai_batch_id
        assert summary.status == "PENDING"
        assert summary.result_file_id == "N/A"
        
        # Should have identified 2 unique receipts
        receipt_refs = summary.receipt_refs
        assert len(receipt_refs) == 2
        assert ("IMG001", 1) in receipt_refs
        assert ("IMG002", 2) in receipt_refs

    def test_add_batch_summary(self, mock_client_manager):
        """Test adding batch summary to DynamoDB."""
        summary = BatchSummary(
            batch_id="test-line-batch",
            batch_type="EMBEDDING",
            openai_batch_id="batch_openai_line_123",
            status="PENDING"
        )
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            add_batch_summary(summary)
        
        mock_client_manager.dynamo.add_batch_summary.assert_called_once_with(summary)

    def test_update_line_embedding_status(self, mock_client_manager, sample_receipt_lines):
        """Test updating line embedding status."""
        lines = sample_receipt_lines[:3]
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            update_line_embedding_status(lines)
        
        # Should update all lines to PENDING status
        for line in lines:
            assert line.embedding_status == EmbeddingStatus.PENDING.value
        
        mock_client_manager.dynamo.update_receipt_lines.assert_called_once_with(lines)

    def test_line_embedding_batch_workflow(self, mock_client_manager, sample_receipt_lines):
        """Test complete line embedding batch workflow."""
        # Step 1: List lines needing embeddings
        mock_client_manager.dynamo.list_receipt_lines_by_embedding_status.return_value = sample_receipt_lines
        
        # Step 2: Chunk into batches
        lines_to_embed = sample_receipt_lines[:3]  # Process first receipt
        batches = chunk_into_line_embedding_batches(lines_to_embed)
        
        # Step 3: Format for OpenAI
        batch_lines = batches["IMG001"][1]  # First batch
        formatted_inputs = format_line_context_embedding(batch_lines)
        
        # Step 4: Generate batch ID and write NDJSON
        batch_id = generate_batch_id()
        
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('pathlib.Path.open', mock_file):
                ndjson_path = write_ndjson(batch_id, formatted_inputs)
        
        # Step 5: Upload to OpenAI
        with patch('pathlib.Path.open', mock_open()):
            with patch('receipt_label.embedding.line.submit.get_client_manager',
                       return_value=mock_client_manager):
                file_obj = upload_to_openai(ndjson_path)
        
        # Step 6: Submit batch
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            batch_obj = submit_openai_batch(file_obj.id)
        
        # Step 7: Create and add batch summary
        with patch('builtins.open', mock_open(read_data='{"custom_id": "test"}\n')):
            summary = create_batch_summary(batch_id, batch_obj.id, str(ndjson_path))
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            add_batch_summary(summary)
        
        # Step 8: Update line statuses
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            update_line_embedding_status(batch_lines)
        
        # Verify workflow completed successfully
        assert len(formatted_inputs) == 3
        assert file_obj.id == "file_line_123"
        assert batch_obj.id == "batch_line_456"
        assert summary.batch_type == "EMBEDDING"
        assert all(line.embedding_status == EmbeddingStatus.PENDING.value for line in batch_lines)

    def test_custom_id_format_consistency(self, sample_receipt_lines):
        """Test that custom ID format is consistent for lines."""
        lines_to_embed = sample_receipt_lines[:1]
        
        formatted_inputs = format_line_context_embedding(lines_to_embed)
        custom_id = formatted_inputs[0]["custom_id"]
        
        # Should match expected format (no WORD component for lines)
        expected_format = r"IMAGE#IMG001#RECEIPT#\d{5}#LINE#\d{5}"
        import re
        assert re.match(expected_format, custom_id)
        
        # Should be parseable by create_batch_summary
        parts = custom_id.split("#")
        assert parts[0] == "IMAGE"
        assert parts[1] == "IMG001"
        assert parts[2] == "RECEIPT"
        assert int(parts[3]) == 1
        assert parts[4] == "LINE"
        assert int(parts[5]) == 1

    def test_empty_line_handling(self, mock_client_manager):
        """Test handling of empty line lists."""
        empty_lines = []
        
        with patch('receipt_label.embedding.line.submit.get_client_manager',
                   return_value=mock_client_manager):
            # Should handle empty input gracefully
            try:
                update_line_embedding_status(empty_lines)
                formatted_inputs = format_line_context_embedding(empty_lines)
                batches = chunk_into_line_embedding_batches(empty_lines)
                
                assert formatted_inputs == []
                assert batches == {}
                
            except Exception as e:
                pytest.fail(f"Should handle empty input gracefully: {e}")

    def test_large_batch_processing(self, sample_receipt_lines):
        """Test processing large batches of lines."""
        # Create large dataset
        large_dataset = []
        for i in range(500):
            line = ReceiptLine(
                image_id=f"IMG{i//50}", receipt_id=i//10, line_id=i%10,
                text=f"Line {i} text content",
                x1=i, y1=i*2, x2=i+200, y2=i*2+25,
                confidence=0.90,
                embedding_status=EmbeddingStatus.NONE.value
            )
            large_dataset.append(line)
        
        # Should process efficiently
        batches = chunk_into_line_embedding_batches(large_dataset)
        
        # Should group correctly
        assert len(batches) == 10  # 10 images (500/50)
        
        # Should deduplicate properly
        total_lines = sum(
            len(receipt_dict)
            for image_dict in batches.values()
            for receipt_dict in image_dict.values()
        )
        assert total_lines == 500

    def test_error_handling_openai_failure(self, mock_client_manager):
        """Test error handling when OpenAI operations fail."""
        # Mock OpenAI file upload failure
        mock_client_manager.openai.files.create.side_effect = Exception("OpenAI API Error")
        
        filepath = Path("/tmp/test-lines.ndjson")
        
        with patch('pathlib.Path.open', mock_open()):
            with patch('receipt_label.embedding.line.submit.get_client_manager',
                       return_value=mock_client_manager):
                with pytest.raises(Exception, match="OpenAI API Error"):
                    upload_to_openai(filepath)

    def test_malformed_ndjson_handling(self):
        """Test handling of malformed NDJSON in create_batch_summary."""
        batch_id = "malformed-test"
        openai_batch_id = "batch_malformed"
        
        # Create NDJSON with various malformed entries
        ndjson_content = '\n'.join([
            '{"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001"}',  # Valid
            '{"invalid": "missing_custom_id"}',  # Invalid - no custom_id
            '{"custom_id": "INVALID#FORMAT"}',  # Invalid - wrong format
            'not json at all',  # Invalid - not JSON
            '{"custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001"}',  # Valid
            ''  # Empty line
        ])
        
        with patch('builtins.open', mock_open(read_data=ndjson_content)):
            summary = create_batch_summary(batch_id, openai_batch_id, "/tmp/malformed.ndjson")
        
        # Should handle malformed entries gracefully and extract valid ones
        receipt_refs = summary.receipt_refs
        assert len(receipt_refs) == 2  # Only valid entries
        assert ("IMG001", 1) in receipt_refs
        assert ("IMG002", 2) in receipt_refs

    def test_line_text_content_preservation(self, sample_receipt_lines):
        """Test that line text content is preserved correctly."""
        lines_with_special_chars = [
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=1,
                text="McDonald's #1234 Store",  # Apostrophe and special chars
                x1=50, y1=30, x2=400, y2=55,
                confidence=0.95,
                embedding_status=EmbeddingStatus.NONE.value
            ),
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=2,
                text="Total: $12.99 (Tax: $1.04)",  # Parentheses and currency
                x1=100, y1=400, x2=300, y2=425,
                confidence=0.98,
                embedding_status=EmbeddingStatus.NONE.value
            )
        ]
        
        formatted_inputs = format_line_context_embedding(lines_with_special_chars)
        
        # Should preserve exact text content
        assert formatted_inputs[0]["body"]["input"] == "McDonald's #1234 Store"
        assert formatted_inputs[1]["body"]["input"] == "Total: $12.99 (Tax: $1.04)"

    def test_performance_optimization(self, sample_receipt_lines, performance_timer):
        """Test performance of line embedding formatting."""
        # Create moderately sized batch
        lines_batch = sample_receipt_lines * 20  # 100 lines
        
        performance_timer.start()
        formatted_inputs = format_line_context_embedding(lines_batch)
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 1.0, f"Line formatting took {elapsed:.2f}s, should be <1s"
        assert len(formatted_inputs) == 100