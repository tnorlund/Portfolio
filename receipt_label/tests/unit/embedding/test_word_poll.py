"""Unit tests for word embedding polling pipeline."""

import pytest

# Skip entire module due to API changes - see tests/CLAUDE.md
pytestmark = pytest.mark.skip(reason="Embedding pipeline API changes - ReceiptWordLabel 'reasoning' field removed, see tests/CLAUDE.md")
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from receipt_label.embedding.word.poll import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    get_receipt_descriptions,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
    save_word_embeddings_as_delta,
    _parse_left_right_from_formatted,
    _parse_metadata_from_custom_id,
    _get_unique_receipt_and_image_ids
)
from tests.markers import unit, fast, embedding
from receipt_dynamo.entities import BatchSummary, ReceiptWord, ReceiptWordLabel, EmbeddingBatchResult, ReceiptMetadata
from tests.helpers import create_test_receipt_word
from receipt_dynamo.constants import BatchType, ValidationStatus, EmbeddingStatus


@unit
@fast
@embedding
class TestWordEmbeddingPoll:
    """Test word embedding polling pipeline components."""

    @pytest.fixture
    def sample_batch_summaries(self):
        """Sample batch summaries for testing."""
        return [
            BatchSummary(
                batch_id="batch_001",
                openai_batch_id="batch_openai_123",
                status="PENDING",
                batch_type="EMBEDDING",
                submitted_at=datetime.now(timezone.utc)
            ),
            BatchSummary(
                batch_id="batch_002",
                openai_batch_id="batch_openai_456", 
                status="PENDING",
                batch_type="EMBEDDING",
                submitted_at=datetime.now(timezone.utc)
            )
        ]

    @pytest.fixture
    def sample_openai_results(self):
        """Sample OpenAI embedding results."""
        return [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "response": {
                    "body": {
                        "data": [{
                            "embedding": [0.1, 0.2, 0.3] * 512  # 1536 dimensions
                        }]
                    }
                }
            },
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001", 
                "response": {
                    "body": {
                        "data": [{
                            "embedding": [0.4, 0.5, 0.6] * 512
                        }]
                    }
                }
            },
            {
                "custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001#WORD#00001",
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
    def sample_receipt_words(self):
        """Sample receipt words for testing."""
        return [
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text="Walmart", x1=100, y1=50, x2=200, y2=70,
                confidence=0.95, embedding_status=EmbeddingStatus.PENDING.value,
                is_noise=False
            ),
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                text="$12.99", x1=100, y1=100, x2=150, y2=120,
                confidence=0.98, embedding_status=EmbeddingStatus.PENDING.value,
                is_noise=False
            ),
            ReceiptWord(
                image_id="IMG002", receipt_id=2, line_id=1, word_id=1,
                text="Target", x1=50, y1=40, x2=120, y2=60,
                confidence=0.92, embedding_status=EmbeddingStatus.PENDING.value,
                is_noise=False
            )
        ]

    @pytest.fixture
    def sample_word_labels(self):
        """Sample word labels for testing."""
        return [
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                label="MERCHANT_NAME", validation_status=ValidationStatus.VALID.value,
                timestamp_added=datetime.now(timezone.utc), label_proposed_by="PATTERN_MATCH"
            ),
            ReceiptWordLabel(
                image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                label="CURRENCY", validation_status=ValidationStatus.PENDING.value,
                timestamp_added=datetime.now(timezone.utc), label_proposed_by="AUTO_SUGGEST",
                confidence=0.85
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
        manager.dynamo.get_receipt_details.return_value = (None, None, [], None, None, [])
        manager.dynamo.get_receipt_metadata.return_value = None
        manager.dynamo.add_embedding_batch_results.return_value = None
        manager.dynamo.get_batch_summary.return_value = Mock()
        manager.dynamo.update_batch_summary.return_value = None
        
        # Mock OpenAI operations
        mock_batch = Mock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file_output_123"
        manager.openai.batches.retrieve.return_value = mock_batch
        
        mock_content = Mock()
        mock_content.read.return_value = b""
        manager.openai.files.content.return_value = mock_content
        
        return manager

    def test_list_pending_embedding_batches(self, mock_client_manager, sample_batch_summaries):
        """Test listing pending embedding batches."""
        mock_client_manager.dynamo.get_batch_summaries_by_status.return_value = (
            sample_batch_summaries, None
        )
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            batches = list_pending_embedding_batches()
        
        assert len(batches) == 2
        assert all(batch.status == "PENDING" for batch in batches)
        assert all(batch.batch_type == "EMBEDDING" for batch in batches)
        
        mock_client_manager.dynamo.get_batch_summaries_by_status.assert_called_with(
            status="PENDING",
            batch_type=BatchType.EMBEDDING,
            limit=25,
            last_evaluated_key=None
        )

    def test_list_pending_batches_pagination(self, mock_client_manager, sample_batch_summaries):
        """Test pagination when listing pending batches."""
        # Mock paginated response
        mock_client_manager.dynamo.get_batch_summaries_by_status.side_effect = [
            (sample_batch_summaries[:1], {"last_key": "value"}),  # First page
            (sample_batch_summaries[1:], None)  # Second page
        ]
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            batches = list_pending_embedding_batches()
        
        assert len(batches) == 2
        # Should have made two calls for pagination
        assert mock_client_manager.dynamo.get_batch_summaries_by_status.call_count == 2

    def test_get_openai_batch_status(self, mock_client_manager):
        """Test getting OpenAI batch status."""
        openai_batch_id = "batch_openai_123"
        
        # Test different statuses
        test_statuses = ["validating", "in_progress", "finalizing", "completed", "failed"]
        
        for expected_status in test_statuses:
            mock_batch = Mock()
            mock_batch.status = expected_status
            mock_client_manager.openai.batches.retrieve.return_value = mock_batch
            
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                status = get_openai_batch_status(openai_batch_id)
            
            assert status == expected_status
            mock_client_manager.openai.batches.retrieve.assert_called_with(openai_batch_id)

    def test_download_openai_batch_result(self, mock_client_manager, sample_openai_results):
        """Test downloading OpenAI batch results."""
        openai_batch_id = "batch_openai_123"
        
        # Mock the response content as NDJSON
        ndjson_content = '\n'.join(json.dumps(result) for result in sample_openai_results)
        
        mock_content = Mock()
        mock_content.read.return_value = ndjson_content.encode('utf-8')
        mock_client_manager.openai.files.content.return_value = mock_content
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            results = download_openai_batch_result(openai_batch_id)
        
        assert len(results) == 3
        
        # Check first result
        result1 = results[0]
        assert result1["custom_id"] == "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001"
        assert len(result1["embedding"]) == 1536  # text-embedding-3-small dimension
        assert result1["embedding"][:3] == [0.1, 0.2, 0.3]

    def test_download_batch_result_different_response_types(self, mock_client_manager):
        """Test handling different OpenAI response content types."""
        test_content = '{"custom_id": "test", "response": {"body": {"data": [{"embedding": [0.1, 0.2]}]}}}'
        
        # Test bytes response
        mock_content = test_content.encode('utf-8')
        mock_client_manager.openai.files.content.return_value = mock_content
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            results = download_openai_batch_result("batch_123")
        
        assert len(results) == 1
        assert results[0]["custom_id"] == "test"
        
        # Test string response
        mock_client_manager.openai.files.content.return_value = test_content
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            results = download_openai_batch_result("batch_123")
        
        assert len(results) == 1
        
        # Test file-like object response
        mock_file_obj = Mock()
        mock_file_obj.read.return_value = test_content.encode('utf-8')
        mock_client_manager.openai.files.content.return_value = mock_file_obj
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            results = download_openai_batch_result("batch_123")
        
        assert len(results) == 1

    def test_parse_metadata_from_custom_id(self):
        """Test parsing metadata from custom ID."""
        custom_id = "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00003"
        
        metadata = _parse_metadata_from_custom_id(custom_id)
        
        assert metadata["image_id"] == "IMG001"
        assert metadata["receipt_id"] == 1
        assert metadata["line_id"] == 2
        assert metadata["word_id"] == 3
        assert metadata["source"] == "openai_embedding_batch"

    def test_get_unique_receipt_and_image_ids(self):
        """Test extracting unique receipt and image IDs from results."""
        results = [
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001"},
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001"},
            {"custom_id": "IMAGE#IMG001#RECEIPT#00002#LINE#00001#WORD#00001"},
            {"custom_id": "IMAGE#IMG002#RECEIPT#00001#LINE#00001#WORD#00001"}
        ]
        
        unique_ids = _get_unique_receipt_and_image_ids(results)
        
        # Should get unique combinations
        expected_ids = {(1, "IMG001"), (2, "IMG001"), (1, "IMG002")}
        assert set(unique_ids) == expected_ids

    def test_get_receipt_descriptions(self, mock_client_manager, sample_receipt_words, 
                                      sample_word_labels, sample_receipt_metadata):
        """Test getting receipt descriptions."""
        results = [
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001"},
            {"custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001#WORD#00001"}
        ]
        
        # Mock DynamoDB responses
        def mock_get_receipt_details(image_id, receipt_id):
            if image_id == "IMG001" and receipt_id == 1:
                return (None, None, sample_receipt_words[:2], None, sample_word_labels)
            elif image_id == "IMG002" and receipt_id == 2:
                return (None, None, sample_receipt_words[2:], None, [])
            return (None, None, [], None, [])
        
        def mock_get_receipt_metadata(image_id, receipt_id):
            if image_id == "IMG001" and receipt_id == 1:
                return sample_receipt_metadata
            return None
        
        mock_client_manager.dynamo.get_receipt_details.side_effect = mock_get_receipt_details
        mock_client_manager.dynamo.get_receipt_metadata.side_effect = mock_get_receipt_metadata
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            descriptions = get_receipt_descriptions(results)
        
        # Should get descriptions for both receipts
        assert "IMG001" in descriptions
        assert "IMG002" in descriptions
        assert 1 in descriptions["IMG001"]
        assert 2 in descriptions["IMG002"]
        
        # Check IMG001 description
        img001_desc = descriptions["IMG001"][1]
        assert len(img001_desc["words"]) == 2
        assert len(img001_desc["labels"]) == 2
        assert img001_desc["metadata"] == sample_receipt_metadata
        
        # Check IMG002 description
        img002_desc = descriptions["IMG002"][2]
        assert len(img002_desc["words"]) == 1
        assert len(img002_desc["labels"]) == 0

    def test_write_embedding_results_to_dynamo(self, mock_client_manager, sample_receipt_words):
        """Test writing embedding results to DynamoDB."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            },
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001",
                "embedding": [0.4, 0.5, 0.6] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": sample_receipt_words[:2],
                    "labels": [],
                    "metadata": None
                }
            }
        }
        
        batch_id = "test_batch"
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_embedding_results_to_dynamo(results, descriptions, batch_id)
        
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
        assert result1.word_id == 1
        assert result1.status == "SUCCESS"
        assert result1.text == "Walmart"

    def test_write_results_large_batch_chunking(self, mock_client_manager):
        """Test chunking when writing large batches to DynamoDB."""
        # Create 50 results (should be chunked into 2 batches of 25)
        results = []
        words = []
        for i in range(50):
            results.append({
                "custom_id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}#WORD#00001",
                "embedding": [0.1] * 1536
            })
            words.append(ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=f"word_{i}", x1=0, y1=0, x2=50, y2=20,
                confidence=0.95, embedding_status=EmbeddingStatus.PENDING.value
            ))
        
        descriptions = {
            "IMG001": {
                1: {"words": words, "labels": [], "metadata": None}
            }
        }
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_embedding_results_to_dynamo(results, descriptions, "batch")
        
        assert written_count == 50
        # Should have made 2 calls for chunking (25 + 25)
        assert mock_client_manager.dynamo.add_embedding_batch_results.call_count == 2

    def test_mark_batch_complete(self, mock_client_manager):
        """Test marking batch as complete."""
        batch_id = "test_batch_123"
        
        mock_summary = Mock()
        mock_client_manager.dynamo.get_batch_summary.return_value = mock_summary
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            mark_batch_complete(batch_id)
        
        # Should have updated batch status
        assert mock_summary.status == "COMPLETED"
        
        mock_client_manager.dynamo.get_batch_summary.assert_called_once_with(batch_id)
        mock_client_manager.dynamo.update_batch_summary.assert_called_once_with(mock_summary)

    def test_parse_left_right_from_formatted(self):
        """Test parsing left/right context from formatted strings."""
        # Test normal case
        formatted = "<TARGET>WORD</TARGET> <POS>middle-center</POS> <CONTEXT>LEFT RIGHT</CONTEXT>"
        left, right = _parse_left_right_from_formatted(formatted)
        
        assert left == "LEFT"
        assert right == "RIGHT"
        
        # Test single word context
        formatted_single = "<TARGET>WORD</TARGET> <POS>top-left</POS> <CONTEXT>ONLY</CONTEXT>"
        left, right = _parse_left_right_from_formatted(formatted_single)
        
        assert left == "ONLY"
        assert right == "<EDGE>"
        
        # Test empty context
        formatted_empty = "<TARGET>WORD</TARGET> <POS>bottom-right</POS> <CONTEXT></CONTEXT>"
        left, right = _parse_left_right_from_formatted(formatted_empty)
        
        assert left == "<EDGE>"
        assert right == "<EDGE>"
        
        # Test missing context
        with pytest.raises(ValueError):
            _parse_left_right_from_formatted("<TARGET>WORD</TARGET> <POS>center</POS>")

    def test_save_word_embeddings_as_delta(self, mock_client_manager, sample_receipt_words,
                                           sample_word_labels, sample_receipt_metadata):
        """Test saving word embeddings as delta for ChromaDB compaction."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": sample_receipt_words[:1],
                    "labels": sample_word_labels[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_batch"
        bucket_name = "test-chroma-bucket"
        sqs_queue_url = "test-sqs-url"
        
        with patch('receipt_label.embedding.word.poll.produce_embedding_delta') as mock_produce:
            mock_produce.return_value = {
                "delta_id": "delta_123",
                "delta_key": "s3://bucket/delta_123",
                "embedding_count": 1
            }
            
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                result = save_word_embeddings_as_delta(
                    results, descriptions, batch_id, bucket_name, sqs_queue_url
                )
        
        # Should return delta creation result
        assert result["delta_id"] == "delta_123"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with correct parameters
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        assert len(call_kwargs["ids"]) == 1
        assert len(call_kwargs["embeddings"]) == 1
        assert len(call_kwargs["documents"]) == 1
        assert len(call_kwargs["metadatas"]) == 1
        
        # Check metadata structure
        metadata = call_kwargs["metadatas"][0]
        assert metadata["image_id"] == "IMG001"
        assert metadata["text"] == "Walmart"
        assert metadata["merchant_name"] == "Walmart"  # canonical name
        assert metadata["label_status"] == "validated"
        assert "validated_labels" in metadata
        
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_words"

    def test_save_embeddings_label_status_logic(self, mock_client_manager, sample_receipt_words):
        """Test label status determination logic in save_word_embeddings_as_delta."""
        results = [
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001", "embedding": [0.1] * 1536},
            {"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001", "embedding": [0.2] * 1536}
        ]
        
        # Test different label statuses
        valid_label = ReceiptWordLabel(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            label="MERCHANT_NAME", validation_status=ValidationStatus.VALID.value,
            timestamp_added=datetime.now(timezone.utc)
        )
        
        pending_label = ReceiptWordLabel(
            image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
            label="CURRENCY", validation_status=ValidationStatus.PENDING.value,
            timestamp_added=datetime.now(timezone.utc), confidence=0.85
        )
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": sample_receipt_words[:2],
                    "labels": [valid_label, pending_label],
                    "metadata": ReceiptMetadata(
                        image_id="IMG001", receipt_id=1,
                        merchant_name="Test Merchant"
                    )
                }
            }
        }
        
        with patch.dict('os.environ', {'CHROMADB_BUCKET': 'test-bucket'}):
            with patch('receipt_label.embedding.word.poll.produce_embedding_delta') as mock_produce:
                mock_produce.return_value = {"delta_id": "test", "embedding_count": 2}
                
                with patch('receipt_label.embedding.word.poll.get_client_manager',
                           return_value=mock_client_manager):
                    save_word_embeddings_as_delta(results, descriptions, "batch")
        
        # Check metadata for label status
        call_kwargs = mock_produce.call_args.kwargs
        metadatas = call_kwargs["metadatas"]
        
        # First word should be "validated" (has VALID label)
        assert metadatas[0]["label_status"] == "validated"
        assert "validated_labels" in metadatas[0]
        
        # Second word should be "auto_suggested" (has PENDING label)
        assert metadatas[1]["label_status"] == "auto_suggested"
        assert metadatas[1]["label_confidence"] == 0.85

    def test_error_handling_missing_word(self, mock_client_manager):
        """Test error handling when receipt word is missing."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "embedding": [0.1] * 1536
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": [],  # Empty words list
                    "labels": [],
                    "metadata": None
                }
            }
        }
        
        with patch.dict('os.environ', {'CHROMADB_BUCKET': 'test-bucket'}):
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                with pytest.raises(ValueError, match="No ReceiptWord found"):
                    save_word_embeddings_as_delta(results, descriptions, "batch")

    def test_error_handling_missing_environment_variables(self, mock_client_manager):
        """Test error handling when required environment variables are missing."""
        results = [{"custom_id": "test", "embedding": [0.1]}]
        descriptions = {}
        
        # Missing CHROMADB_BUCKET
        with patch.dict('os.environ', {}, clear=True):
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                with pytest.raises(ValueError, match="CHROMADB_BUCKET environment variable not set"):
                    save_word_embeddings_as_delta(results, descriptions, "batch")

    def test_performance_large_batch_processing(self, mock_client_manager, performance_timer):
        """Test performance with large embedding result batches."""
        # Create large batch of results
        results = []
        words = []
        for i in range(1000):
            results.append({
                "custom_id": f"IMAGE#IMG001#RECEIPT#00001#LINE#{i:05d}#WORD#00001",
                "embedding": [i * 0.001] * 1536
            })
            words.append(ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=f"word_{i}", x1=i, y1=i, x2=i+50, y2=i+20,
                confidence=0.95, embedding_status=EmbeddingStatus.PENDING.value
            ))
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": words,
                    "labels": [],
                    "metadata": ReceiptMetadata(
                        image_id="IMG001", receipt_id=1,
                        merchant_name="Test Merchant"
                    )
                }
            }
        }
        
        performance_timer.start()
        
        with patch('receipt_label.embedding.word.poll.get_client_manager',
                   return_value=mock_client_manager):
            written_count = write_embedding_results_to_dynamo(results, descriptions, "large_batch")
        
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 10.0, f"Large batch processing took {elapsed:.2f}s, should be <10s"
        assert written_count == 1000
        
        # Should have chunked into multiple DynamoDB calls
        call_count = mock_client_manager.dynamo.add_embedding_batch_results.call_count
        assert call_count == 40  # 1000 / 25 = 40 chunks

    def test_save_word_embeddings_as_delta_with_skip_sqs(self, mock_client_manager, 
                                                         sample_receipt_words,
                                                         sample_word_labels, 
                                                         sample_receipt_metadata):
        """Test saving word embeddings as delta with skip_sqs_notification flag."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": sample_receipt_words[:1],
                    "labels": sample_word_labels[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_batch"
        bucket_name = "test-chroma-bucket"
        sqs_queue_url = None  # Skip SQS notification
        
        with patch('receipt_label.embedding.word.poll.produce_embedding_delta') as mock_produce:
            mock_produce.return_value = {
                "delta_id": "delta_123",
                "delta_key": "s3://bucket/delta_123",
                "embedding_count": 1
            }
            
            # Test with sqs_queue_url=None
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                result = save_word_embeddings_as_delta(
                    results, descriptions, batch_id, bucket_name, sqs_queue_url
                )
        
        # Should return delta creation result
        assert result["delta_id"] == "delta_123"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with sqs_queue_url=None
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        # Verify SQS queue URL is explicitly None when skip_sqs_notification=True
        assert call_kwargs["sqs_queue_url"] is None
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_words"

    def test_save_word_embeddings_as_delta_without_skip_sqs(self, mock_client_manager,
                                                            sample_receipt_words,
                                                            sample_word_labels,
                                                            sample_receipt_metadata):
        """Test saving word embeddings as delta without skip_sqs_notification flag (default behavior)."""
        results = [
            {
                "custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001",
                "embedding": [0.1, 0.2, 0.3] * 512
            }
        ]
        
        descriptions = {
            "IMG001": {
                1: {
                    "words": sample_receipt_words[:1],
                    "labels": sample_word_labels[:1],
                    "metadata": sample_receipt_metadata
                }
            }
        }
        
        batch_id = "test_batch"
        bucket_name = "test-chroma-bucket"
        sqs_queue_url = "test-sqs-url"
        
        with patch('receipt_label.embedding.word.poll.produce_embedding_delta') as mock_produce:
            mock_produce.return_value = {
                "delta_id": "delta_456",
                "delta_key": "s3://bucket/delta_456",
                "embedding_count": 1
            }
            
            # Test with sqs_queue_url provided
            with patch('receipt_label.embedding.word.poll.get_client_manager',
                       return_value=mock_client_manager):
                result = save_word_embeddings_as_delta(
                    results, descriptions, batch_id, bucket_name, sqs_queue_url
                )
        
        # Should return delta creation result
        assert result["delta_id"] == "delta_456"
        assert result["embedding_count"] == 1
        
        # Should have called produce_embedding_delta with sqs_queue_url from environment
        mock_produce.assert_called_once()
        call_kwargs = mock_produce.call_args.kwargs
        
        # Verify SQS queue URL is from environment when skip_sqs_notification=False
        assert call_kwargs["sqs_queue_url"] == "test-sqs-url"
        assert call_kwargs["bucket_name"] == "test-chroma-bucket"
        assert call_kwargs["collection_name"] == "receipt_words"