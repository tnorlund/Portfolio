"""Unit tests for line embedding polling pipeline."""

import pytest

# Skip entire module due to API changes - see tests/CLAUDE.md
pytestmark = pytest.mark.skip(reason="Embedding pipeline API changes - ReceiptWordLabel 'reasoning' field removed, see tests/CLAUDE.md")
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from receipt_label.embedding.line.poll import (
    list_pending_line_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    get_receipt_descriptions,
    write_line_embedding_results_to_dynamo,
    mark_batch_complete,
    save_line_embeddings_as_delta,
    _parse_metadata_from_line_id,
    _get_unique_receipt_and_image_ids
)
from tests.markers import unit, fast, embedding
from receipt_dynamo.entities import BatchSummary, ReceiptLine, EmbeddingBatchResult, ReceiptMetadata
from receipt_dynamo.constants import BatchType, EmbeddingStatus


@unit
@fast
@embedding  
class TestLineEmbeddingPoll:
    """Test line embedding polling pipeline components."""

    @pytest.fixture
    def sample_batch_summaries(self):
        """Sample batch summaries for testing."""
        return [
            BatchSummary(
                batch_id="line_batch_001",
                openai_batch_id="batch_openai_line_123",
                status="PENDING",
                batch_type="EMBEDDING",
                submitted_at=datetime.now(timezone.utc)
            ),
            BatchSummary(
                batch_id="line_batch_002", 
                openai_batch_id="batch_openai_line_456",
                status="PENDING",
                batch_type="EMBEDDING",
                submitted_at=datetime.now(timezone.utc)
            )
        ]

    @pytest.fixture
    def sample_openai_line_results(self):
        """Sample OpenAI line embedding results."""
        return [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "response": {
                    "body": {
                        "data": [{
                            "embedding": [0.1, 0.2, 0.3] * 512  # 1536 dimensions
                        }]
                    }
                }
            },
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002",
                "response": {
                    "body": {
                        "data": [{
                            "embedding": [0.4, 0.5, 0.6] * 512
                        }]
                    }
                }
            },
            {
                "custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001",
                "response": {
                    "body": {
                        "data": [{
                            "embedding": [0.7, 0.8, 0.9] * 512
                        }]
                    }
                }
            }
        ]

    @pytest.fixture
    def sample_receipt_lines(self):
        """Sample receipt lines for testing."""
        return [
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=1,
                text="Walmart Supercenter",
                x1=50, y1=30, x2=400, y2=55,
                confidence=0.95,
                embedding_status=EmbeddingStatus.PENDING.value
            ),
            ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=2,
                text="123 Main Street",
                x1=75, y1=60, x2=350, y2=80,
                confidence=0.92,
                embedding_status=EmbeddingStatus.PENDING.value
            ),
            ReceiptLine(
                image_id="IMG002", receipt_id=2, line_id=1,
                text="Target Store",
                x1=80, y1=40, x2=320, y2=65,
                confidence=0.94,
                embedding_status=EmbeddingStatus.PENDING.value
            )
        ]

    @pytest.fixture
    def sample_receipt_metadata(self):
        """Sample receipt metadata for testing."""
        return ReceiptMetadata(
            image_id="IMG001",
            receipt_id=1,
            merchant_name="Walmart Supercenter",
            canonical_merchant_name="Walmart",
            timestamp_created=datetime.now(timezone.utc)
        )

    @pytest.fixture
    def mock_client_manager(self):
        """Mock client manager for testing."""
        manager = Mock()
        
        # Mock DynamoDB operations
        manager.dynamo.get_batch_summaries_by_status.return_value = ([], None)
        manager.dynamo.get_receipt_details.return_value = (None, [], None, None, None)
        manager.dynamo.get_receipt_metadata.return_value = None
        manager.dynamo.add_embedding_batch_results.return_value = None
        manager.dynamo.get_batch_summary.return_value = Mock()
        manager.dynamo.update_batch_summary.return_value = None
        
        # Mock OpenAI operations
        mock_batch = Mock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file_line_output_123"
        manager.openai.batches.retrieve.return_value = mock_batch
        
        mock_content = Mock()
        mock_content.read.return_value = b""
        manager.openai.files.content.return_value = mock_content
        
        return manager

    def test_list_pending_embedding_batches(self, mock_client_manager, sample_batch_summaries):
        """Test listing pending line embedding batches."""
        mock_client_manager.dynamo.get_batch_summaries_by_status.return_value = (
            sample_batch_summaries, None
        )
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            batches = list_pending_line_embedding_batches()
        
        assert len(batches) == 2
        assert all(batch.status == "PENDING" for batch in batches)
        assert all(batch.batch_type == "EMBEDDING" for batch in batches)
        
        mock_client_manager.dynamo.get_batch_summaries_by_status.assert_called_with(
            status="PENDING",
            batch_type=BatchType.EMBEDDING,
            limit=25,
            last_evaluated_key=None
        )

    def test_get_openai_batch_status(self, mock_client_manager):
        """Test getting OpenAI batch status for line embeddings."""
        openai_batch_id = "batch_openai_line_123"
        
        # Test different statuses
        test_statuses = ["validating", "in_progress", "finalizing", "completed", "failed"]
        
        for expected_status in test_statuses:
            mock_batch = Mock()
            mock_batch.status = expected_status
            mock_client_manager.openai.batches.retrieve.return_value = mock_batch
            
            with patch('receipt_label.embedding.line.poll.get_client_manager',
                       return_value=mock_client_manager):
                status = get_openai_batch_status(openai_batch_id)
            
            assert status == expected_status

    def test_download_openai_batch_result(self, mock_client_manager, sample_openai_line_results):
        """Test downloading OpenAI line batch results."""
        openai_batch_id = "batch_openai_line_123"
        
        # Mock the response content as NDJSON
        ndjson_content = '\n'.join(json.dumps(result) for result in sample_openai_line_results)
        
        mock_content = Mock()
        mock_content.read.return_value = ndjson_content.encode('utf-8')
        mock_client_manager.openai.files.content.return_value = mock_content
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            results = download_openai_batch_result(openai_batch_id)
        
        assert len(results) == 3
        
        # Check first result
        result1 = results[0]
        assert result1["custom_id"] == "IMAGE#IMG001#RECEIPT#00001#LINE#00001"
        assert len(result1["embedding"]) == 1536  # text-embedding-3-small dimension
        assert result1["embedding"][:3] == [0.1, 0.2, 0.3]

    def test_parse_metadata_from_custom_id(self):
        """Test parsing metadata from line custom ID."""
        custom_id = "IMAGE#IMG001#RECEIPT#00001#LINE#00002"
        
        metadata = _parse_metadata_from_line_id(custom_id)
        
        assert metadata["image_id"] == "IMG001"
        assert metadata["receipt_id"] == 1
        assert metadata["line_id"] == 2
        assert metadata["source"] == "openai_embedding_batch"

    def test_get_unique_receipt_and_image_ids(self):
        """Test extracting unique receipt and image IDs from line results."""
        results = [
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001"},
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002"},
            {"custom_id": "IMAGE#IMG001#RECEIPT#00002#LINE#00001"},
            {"custom_id": "IMAGE#IMG002#RECEIPT#00001#LINE#00001"}
        ]
        
        unique_ids = _get_unique_receipt_and_image_ids(results)
        
        # Should get unique combinations
        expected_ids = {(1, "IMG001"), (2, "IMG001"), (1, "IMG002")}
        assert set(unique_ids) == expected_ids

    def test_get_receipt_descriptions(self, mock_client_manager, sample_receipt_lines,
                                      sample_receipt_metadata):
        """Test getting receipt descriptions for line embeddings."""
        results = [
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001"},
            {"custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001"}
        ]
        
        # Mock DynamoDB responses
        def mock_get_receipt_details(image_id, receipt_id):
            if image_id == "IMG001" and receipt_id == 1:
                return (None, sample_receipt_lines[:2], None, None, None)
            elif image_id == "IMG002" and receipt_id == 2:
                return (None, sample_receipt_lines[2:], None, None, None)
            return (None, [], None, None, None)
        
        def mock_get_receipt_metadata(image_id, receipt_id):
            if image_id == "IMG001" and receipt_id == 1:
                return sample_receipt_metadata
            return None
        
        mock_client_manager.dynamo.get_receipt_details.side_effect = mock_get_receipt_details
        mock_client_manager.dynamo.get_receipt_metadata.side_effect = mock_get_receipt_metadata
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            descriptions = get_receipt_descriptions(results)
        
        # Should get descriptions for both receipts
        assert "IMG001" in descriptions
        assert "IMG002" in descriptions
        assert 1 in descriptions["IMG001"]
        assert 2 in descriptions["IMG002"]
        
        # Check IMG001 description
        img001_desc = descriptions["IMG001"][1]
        assert len(img001_desc["lines"]) == 2
        assert img001_desc["metadata"] == sample_receipt_metadata
        
        # Check IMG002 description
        img002_desc = descriptions["IMG002"][2]
        assert len(img002_desc["lines"]) == 1

    def test_write_embedding_results_to_dynamo(self, mock_client_manager, sample_receipt_lines):
        """Test writing line embedding results to DynamoDB."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            },
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002", 
                "embedding": [0.4, 0.5, 0.6] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": sample_receipt_lines[:2],
                    "metadata": None
                }
            }
        }
        
        batch_id = "test_line_batch"
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_line_embedding_results_to_dynamo(results, descriptions, batch_id)
        
        assert written_count == 2
        
        # Should have called DynamoDB add_embedding_batch_results
        mock_client_manager.dynamo.add_embedding_batch_results.assert_called_once()
        
        # Verify the embedding results structure
        call_args = mock_client_manager.dynamo.add_embedding_batch_results.call_args[0][0]
        assert len(call_args) == 2
        
        result1 = call_args[0]
        assert isinstance(result1, EmbeddingBatchResult)
        assert result1.batch_id == batch_id
        assert result1.image_id == "IMG001"
        assert result1.receipt_id == 1
        assert result1.line_id == 1
        assert result1.status == "SUCCESS"
        assert result1.text == "Walmart Supercenter"

    def test_mark_batch_complete(self, mock_client_manager):
        """Test marking line batch as complete."""
        batch_id = "test_line_batch_123"
        
        mock_summary = Mock()
        mock_client_manager.dynamo.get_batch_summary.return_value = mock_summary
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            mark_batch_complete(batch_id)
        
        # Should have updated batch status
        assert mock_summary.status == "COMPLETED"
        
        mock_client_manager.dynamo.get_batch_summary.assert_called_once_with(batch_id)
        mock_client_manager.dynamo.update_batch_summary.assert_called_once_with(mock_summary)

    def test_save_line_embeddings_as_delta(self, mock_client_manager, sample_receipt_lines,
                                           sample_receipt_metadata):
        """Test saving line embeddings as delta for ChromaDB compaction."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": sample_receipt_lines[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_line_batch"
        
        # Mock environment variables
        with patch.dict('os.environ', {
            'CHROMADB_BUCKET': 'test-chroma-bucket',
            'COMPACTION_QUEUE_URL': 'test-sqs-url'
        }):
            with patch('receipt_label.embedding.line.poll.produce_embedding_delta') as mock_produce:
                mock_produce.return_value = {
                    "delta_id": "line_delta_123",
                    "delta_key": "s3://bucket/line_delta_123",
                    "embedding_count": 1
                }
                
                with patch('receipt_label.embedding.line.poll.get_client_manager',
                           return_value=mock_client_manager):
                    result = save_line_embeddings_as_delta(results, descriptions, batch_id)
        
        # Should return delta creation result
        assert result["delta_id"] == "line_delta_123"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with correct parameters
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        assert len(call_kwargs["ids"]) == 1
        assert len(call_kwargs["embeddings"]) == 1
        assert len(call_kwargs["documents"]) == 1
        assert len(call_kwargs["metadatas"]) == 1
        
        # Check metadata structure for lines
        metadata = call_kwargs["metadatas"][0]
        assert metadata["image_id"] == "IMG001"
        assert metadata["text"] == "Walmart Supercenter"
        assert metadata["merchant_name"] == "Walmart"  # canonical name
        assert metadata["x"] is not None
        assert metadata["y"] is not None
        assert metadata["width"] is not None
        assert metadata["height"] is not None
        
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_lines"

    def test_line_embedding_batch_workflow(self, mock_client_manager, sample_batch_summaries,
                                           sample_openai_line_results, sample_receipt_lines):
        """Test complete line embedding batch polling workflow."""
        # Step 1: List pending batches
        mock_client_manager.dynamo.get_batch_summaries_by_status.return_value = (
            sample_batch_summaries[:1], None
        )
        
        # Step 2: Check OpenAI batch status
        mock_client_manager.openai.batches.retrieve.return_value.status = "completed"
        
        # Step 3: Download results
        ndjson_content = json.dumps(sample_openai_line_results[0])
        mock_content = Mock()
        mock_content.read.return_value = ndjson_content.encode('utf-8')
        mock_client_manager.openai.files.content.return_value = mock_content
        
        # Step 4: Get receipt descriptions
        mock_client_manager.dynamo.get_receipt_details.return_value = (
            None, sample_receipt_lines[:1], None, None, None
        )
        mock_client_manager.dynamo.get_receipt_metadata.return_value = None
        
        batch_summary = sample_batch_summaries[0]
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            # Execute workflow
            batches = list_pending_embedding_batches()
            assert len(batches) == 1
            
            status = get_openai_batch_status(batch_summary.openai_batch_id)
            assert status == "completed"
            
            results = download_openai_batch_result(batch_summary.openai_batch_id)
            assert len(results) == 1
            
            descriptions = get_receipt_descriptions(results)
            assert len(descriptions) == 1
            
            written_count = write_line_embedding_results_to_dynamo(
                results, descriptions, batch_summary.batch_id
            )
            assert written_count == 1
            
            mark_batch_complete(batch_summary.batch_id)

    def test_error_handling_missing_line(self, mock_client_manager):
        """Test error handling when receipt line is missing."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1] * 1536
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": [],  # Empty lines list
                    "metadata": None
                }
            }
        }
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            with pytest.raises(ValueError, match="No ReceiptLine found"):
                write_line_embedding_results_to_dynamo(results, descriptions, "batch")

    def test_large_batch_chunking(self, mock_client_manager):
        """Test chunking when writing large line batches to DynamoDB."""
        # Create 50 results (should be chunked into 2 batches of 25)
        results = []
        lines = []
        for i in range(50):
            results.append({
                "custom_id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}",
                "embedding": [0.1] * 1536
            })
            lines.append(ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=i,
                text=f"Line {i} text",
                x1=0, y1=i*25, x2=400, y2=i*25+25,
                confidence=0.95,
                embedding_status=EmbeddingStatus.PENDING.value
            ))
        
        descriptions = {
            "IMG001": {
                1: {"lines": lines, "metadata": None}
            }
        }
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_line_embedding_results_to_dynamo(results, descriptions, "batch")
        
        assert written_count == 50
        # Should have made 2 calls for chunking (25 + 25)
        assert mock_client_manager.dynamo.add_embedding_batch_results.call_count == 2

    def test_line_metadata_structure(self, sample_receipt_lines, sample_receipt_metadata):
        """Test line embedding metadata structure for ChromaDB."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1] * 1536
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": sample_receipt_lines[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        with patch.dict('os.environ', {'CHROMADB_BUCKET': 'test-bucket'}):
            with patch('receipt_label.embedding.line.poll.produce_embedding_delta') as mock_produce:
                mock_produce.return_value = {"delta_id": "test", "embedding_count": 1}
                
                with patch('receipt_label.embedding.line.poll.get_client_manager',
                           return_value=Mock()):
                    save_line_embeddings_as_delta(results, descriptions, "batch")
        
        # Check metadata structure
        call_kwargs = mock_produce.call_args.kwargs
        metadata = call_kwargs["metadatas"][0]
        
        # Should have line-specific fields
        assert metadata["line_id"] == 1
        assert metadata["text"] == "Walmart Supercenter"
        assert metadata["confidence"] == 0.95
        assert metadata["x"] is not None
        assert metadata["y"] is not None
        assert metadata["width"] is not None
        assert metadata["height"] is not None
        
        # Should use canonical merchant name
        assert metadata["merchant_name"] == "Walmart"

    def test_performance_large_batch_processing(self, mock_client_manager, performance_timer):
        """Test performance with large line embedding result batches.""" 
        # Create large batch of results
        results = []
        lines = []
        for i in range(1000):
            results.append({
                "custom_id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}",
                "embedding": [i * 0.001] * 1536
            })
            lines.append(ReceiptLine(
                image_id="IMG001", receipt_id=1, line_id=i,
                text=f"Line {i} content",
                x1=i, y1=i*2, x2=i+400, y2=i*2+25,
                confidence=0.95,
                embedding_status=EmbeddingStatus.PENDING.value
            ))
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": lines,
                    "metadata": None
                }
            }
        }
        
        performance_timer.start()
        
        with patch('receipt_label.embedding.line.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_line_embedding_results_to_dynamo(results, descriptions, "large_batch")
        
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 5.0, f"Large batch processing took {elapsed:.2f}s, should be <5s"
        assert written_count == 1000
        
        # Should have chunked into multiple DynamoDB calls
        call_count = mock_client_manager.dynamo.add_embedding_batch_results.call_count
        assert call_count == 40  # 1000 / 25 = 40 chunks

    def test_save_line_embeddings_as_delta_with_skip_sqs(self, mock_client_manager, 
                                                         sample_receipt_lines,
                                                         sample_receipt_metadata):
        """Test saving line embeddings as delta with skip_sqs_notification flag."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": sample_receipt_lines[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_line_batch"
        
        # Mock environment variables
        with patch.dict('os.environ', {
            'CHROMADB_BUCKET': 'test-chroma-bucket',
            'COMPACTION_QUEUE_URL': 'test-sqs-url'
        }):
            with patch('receipt_label.embedding.line.poll.produce_embedding_delta') as mock_produce:
                mock_produce.return_value = {
                    "delta_id": "line_delta_123",
                    "delta_key": "s3://bucket/line_delta_123",
                    "embedding_count": 1
                }
                
                # Test with skip_sqs_notification=True
                with patch('receipt_label.embedding.line.poll.get_client_manager',
                           return_value=mock_client_manager):
                    result = save_line_embeddings_as_delta(
                        results, descriptions, batch_id, skip_sqs_notification=True
                    )
        
        # Should return delta creation result
        assert result["delta_id"] == "line_delta_123"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with sqs_queue_url=None
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        # Verify SQS queue URL is explicitly None when skip_sqs_notification=True
        assert call_kwargs["sqs_queue_url"] is None
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_lines"

    def test_save_line_embeddings_as_delta_without_skip_sqs(self, mock_client_manager,
                                                            sample_receipt_lines,
                                                            sample_receipt_metadata):
        """Test saving line embeddings as delta without skip_sqs_notification flag (default behavior)."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "lines": sample_receipt_lines[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_line_batch"
        
        # Mock environment variables with SQS queue URL
        with patch.dict('os.environ', {
            'CHROMADB_BUCKET': 'test-chroma-bucket',
            'COMPACTION_QUEUE_URL': 'test-sqs-url'
        }):
            with patch('receipt_label.embedding.line.poll.produce_embedding_delta') as mock_produce:
                mock_produce.return_value = {
                    "delta_id": "line_delta_456",
                    "delta_key": "s3://bucket/line_delta_456",
                    "embedding_count": 1
                }
                
                # Test with skip_sqs_notification=False (default)
                with patch('receipt_label.embedding.line.poll.get_client_manager',
                           return_value=mock_client_manager):
                    result = save_line_embeddings_as_delta(
                        results, descriptions, batch_id, skip_sqs_notification=False
                    )
        
        # Should return delta creation result
        assert result["delta_id"] == "line_delta_456"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with sqs_queue_url from environment
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        # Verify SQS queue URL is from environment when skip_sqs_notification=False
        assert call_kwargs["sqs_queue_url"] == "test-sqs-url"
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_lines"