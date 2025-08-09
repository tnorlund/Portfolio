"""Unit tests for word embedding submission pipeline."""

import pytest
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch, mock_open
from pathlib import Path

from receipt_label.embedding.word.submit import (
    serialize_receipt_words,
    upload_serialized_words,
    download_serialized_words,
    deserialize_receipt_words,
    query_receipt_words,
    chunk_into_embedding_batches,
    generate_batch_id,
    list_receipt_words_with_no_embeddings,
    format_word_context_embedding,
    write_ndjson,
    upload_to_openai,
    submit_openai_batch,
    create_batch_summary,
    add_batch_summary,
    update_word_embedding_status,
    _format_word_context_embedding_input,
    _get_word_position
)
from tests.markers import unit, fast, embedding
from receipt_dynamo.entities import ReceiptWord, BatchSummary
from tests.helpers import create_test_receipt_word
from receipt_dynamo.constants import EmbeddingStatus


@unit
@fast
@embedding
class TestWordEmbeddingSubmit:
    """Test word embedding submission pipeline components."""

    @pytest.fixture
    def sample_receipt_words(self):
        """Sample receipt words for testing."""
        return [
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text="Walmart",
                bounding_box={"x": 100, "y": 50, "width": 100, "height": 20},
                top_right={"x": 200, "y": 70}, top_left={"x": 100, "y": 70},
                bottom_right={"x": 200, "y": 50}, bottom_left={"x": 100, "y": 50},
                angle_degrees=0.0, angle_radians=0.0,
                confidence=0.95, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=False
            ),
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=2,
                text="Supercenter",
                bounding_box={"x": 210, "y": 50, "width": 110, "height": 20},
                top_right={"x": 320, "y": 70}, top_left={"x": 210, "y": 70},
                bottom_right={"x": 320, "y": 50}, bottom_left={"x": 210, "y": 50},
                angle_degrees=0.0, angle_radians=0.0,
                confidence=0.90, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=False
            ),
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=2, word_id=1,
                text="$12.99",
                bounding_box={"x": 100, "y": 100, "width": 50, "height": 20},
                top_right={"x": 150, "y": 120}, top_left={"x": 100, "y": 120},
                bottom_right={"x": 150, "y": 100}, bottom_left={"x": 100, "y": 100},
                angle_degrees=0.0, angle_radians=0.0,
                confidence=0.98, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=False
            ),
            # Different receipt
            ReceiptWord(
                image_id="IMG002", receipt_id=2, line_id=1, word_id=1,
                text="Target",
                bounding_box={"x": 50, "y": 40, "width": 70, "height": 20},
                top_right={"x": 120, "y": 60}, top_left={"x": 50, "y": 60},
                bottom_right={"x": 120, "y": 40}, bottom_left={"x": 50, "y": 40},
                angle_degrees=0.0, angle_radians=0.0,
                confidence=0.92, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=False
            ),
            # Noise word (should be filtered out)
            ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=3, word_id=1,
                text="|||",
                bounding_box={"x": 300, "y": 150, "width": 20, "height": 20},
                top_right={"x": 320, "y": 170}, top_left={"x": 300, "y": 170},
                bottom_right={"x": 320, "y": 150}, bottom_left={"x": 300, "y": 150},
                angle_degrees=0.0, angle_radians=0.0,
                confidence=0.30, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=True
            )
        ]

    @pytest.fixture
    def mock_client_manager(self):
        """Mock client manager for testing."""
        manager = Mock()
        
        # Mock DynamoDB operations
        manager.dynamo.list_receipt_words_by_embedding_status.return_value = []
        manager.dynamo.get_receipt_details.return_value = (None, None, [], None, None, None)
        manager.dynamo.update_receipt_words.return_value = None
        manager.dynamo.add_batch_summary.return_value = None
        
        # Mock OpenAI operations
        mock_file = Mock()
        mock_file.id = "file_test_123"
        manager.openai.files.create.return_value = mock_file
        
        mock_batch = Mock()
        mock_batch.id = "batch_test_456"
        mock_batch.status = "validating"
        mock_batch.created_at = datetime.now(timezone.utc).timestamp()
        manager.openai.batches.create.return_value = mock_batch
        
        return manager

    def test_serialize_receipt_words(self, sample_receipt_words):
        """Test serializing receipt words to NDJSON files."""
        # Prepare word dictionary
        word_dict = {}
        for word in sample_receipt_words[:3]:  # Skip noise word
            if word.image_id not in word_dict:
                word_dict[word.image_id] = {}
            if word.receipt_id not in word_dict[word.image_id]:
                word_dict[word.image_id][word.receipt_id] = []
            word_dict[word.image_id][word.receipt_id].append(word)
        
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('pathlib.Path.open', mock_file):
                results = serialize_receipt_words(word_dict)
        
        # Should create one file per receipt
        assert len(results) == 1
        result = results[0]
        
        assert result["image_id"] == "IMG001"
        assert result["receipt_id"] == 1
        assert "ndjson_path" in result
        
        # Should have written NDJSON content
        mock_file.assert_called()

    def test_upload_serialized_words(self):
        """Test uploading serialized words to S3."""
        serialized_words = [
            {
                "image_id": "IMG001",
                "receipt_id": 1,
                "ndjson_path": Path("/tmp/test.ndjson")
            }
        ]
        
        with patch('boto3.client') as mock_boto:
            mock_s3 = Mock()
            mock_boto.return_value = mock_s3
            
            result = upload_serialized_words(serialized_words, "test-bucket", "prefix")
        
        # Should have uploaded to S3
        mock_s3.upload_file.assert_called_once()
        
        # Should add S3 metadata
        assert "s3_key" in result[0]
        assert "s3_bucket" in result[0]
        assert result[0]["s3_bucket"] == "test-bucket"

    def test_download_serialized_words(self):
        """Test downloading serialized words from S3."""
        serialized_word = {
            "s3_bucket": "test-bucket",
            "s3_key": "prefix/test.ndjson",
            "ndjson_path": "/tmp/test.ndjson"
        }
        
        with patch('boto3.client') as mock_boto:
            mock_s3 = Mock()
            mock_boto.return_value = mock_s3
            
            result = download_serialized_words(serialized_word)
        
        # Should have downloaded from S3
        mock_s3.download_file.assert_called_once_with(
            "test-bucket", "prefix/test.ndjson", "/tmp/test.ndjson"
        )
        
        assert isinstance(result, Path)

    def test_deserialize_receipt_words(self, sample_receipt_words):
        """Test deserializing receipt words from NDJSON."""
        # Create test NDJSON content
        test_words = sample_receipt_words[:2]
        ndjson_lines = [json.dumps(word.__dict__) for word in test_words]
        ndjson_content = '\n'.join(ndjson_lines)
        
        with patch('builtins.open', mock_open(read_data=ndjson_content)):
            words = deserialize_receipt_words(Path("/tmp/test.ndjson"))
        
        assert len(words) == 2
        assert all(isinstance(word, ReceiptWord) for word in words)
        assert words[0].text == "Walmart"
        assert words[1].text == "Supercenter"

    def test_query_receipt_words(self, mock_client_manager, sample_receipt_words):
        """Test querying receipt words from DynamoDB."""
        mock_client_manager.dynamo.get_receipt_details.return_value = (
            None, None, sample_receipt_words[:3], None, None, None
        )
        
        with patch('receipt_label.embedding.word.submit.get_client_manager', 
                   return_value=mock_client_manager):
            words = query_receipt_words("IMG001", 1)
        
        assert len(words) == 3
        mock_client_manager.dynamo.get_receipt_details.assert_called_once_with(
            "IMG001", 1
        )

    def test_chunk_into_embedding_batches(self, sample_receipt_words):
        """Test chunking words into embedding batches."""
        # Filter out noise words as the function expects
        non_noise_words = [w for w in sample_receipt_words if not w.is_noise]
        
        batches = chunk_into_embedding_batches(non_noise_words)
        
        # Should group by image and receipt
        assert "IMG001" in batches
        assert "IMG002" in batches
        assert 1 in batches["IMG001"]
        assert 2 in batches["IMG002"]
        
        # IMG001 receipt 1 should have 3 words
        assert len(batches["IMG001"][1]) == 3
        # IMG002 receipt 2 should have 1 word
        assert len(batches["IMG002"][2]) == 1
        
        # Should deduplicate by (line_id, word_id)
        duplicate_word = ReceiptWord(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            text="Duplicate", x1=0, y1=0, x2=50, y2=20,
            confidence=0.80, embedding_status=EmbeddingStatus.NONE.value
        )
        words_with_duplicate = non_noise_words + [duplicate_word]
        
        batches_deduped = chunk_into_embedding_batches(words_with_duplicate)
        # Should still have 3 words (duplicate removed)
        assert len(batches_deduped["IMG001"][1]) == 3

    def test_generate_batch_id(self):
        """Test batch ID generation."""
        batch_id1 = generate_batch_id()
        batch_id2 = generate_batch_id()
        
        assert batch_id1 != batch_id2
        assert len(batch_id1) > 0
        assert len(batch_id2) > 0

    def test_list_receipt_words_with_no_embeddings(self, mock_client_manager, sample_receipt_words):
        """Test listing words that need embeddings."""
        # Mock returns all words, function should filter noise
        mock_client_manager.dynamo.list_receipt_words_by_embedding_status.return_value = sample_receipt_words
        
        with patch('receipt_label.embedding.word.submit.get_client_manager',
                   return_value=mock_client_manager):
            words = list_receipt_words_with_no_embeddings()
        
        # Should filter out noise words
        assert len(words) == 4  # 5 total - 1 noise word
        assert all(not word.is_noise for word in words)
        
        mock_client_manager.dynamo.list_receipt_words_by_embedding_status.assert_called_once_with(
            EmbeddingStatus.NONE
        )

    def test_get_word_position(self, sample_receipt_words):
        """Test word position calculation."""
        word = sample_receipt_words[0]  # Walmart at (100, 50)
        
        # Mock centroid calculation
        with patch.object(word, 'calculate_centroid', return_value=(0.2, 0.8)):
            position = _get_word_position(word)
        
        # Should be top-left based on coordinates
        assert position == "top-left"
        
        # Test other positions
        with patch.object(word, 'calculate_centroid', return_value=(0.5, 0.5)):
            position = _get_word_position(word)
        assert position == "middle-center"
        
        with patch.object(word, 'calculate_centroid', return_value=(0.8, 0.2)):
            position = _get_word_position(word)
        assert position == "bottom-right"

    def test_format_word_context_embedding_input(self, sample_receipt_words):
        """Test formatting word context for embedding input."""
        target_word = sample_receipt_words[0]  # Walmart
        all_words = sample_receipt_words[:3]  # Include context words
        
        # Mock bounding box and centroid calculations
        for word in all_words:
            word.bounding_box = {
                "y": word.y1,
                "height": word.y2 - word.y1,
                "width": word.x2 - word.x1
            }
            word.top_left = {"y": word.y2}
            word.bottom_left = {"y": word.y1}
        
        with patch.object(target_word, 'calculate_centroid', return_value=(0.2, 0.8)):
            formatted_input = _format_word_context_embedding_input(target_word, all_words)
        
        # Should contain target word, position, and context
        assert "<TARGET>Walmart</TARGET>" in formatted_input
        assert "<POS>" in formatted_input
        assert "<CONTEXT>" in formatted_input

    def test_format_word_context_embedding(self, sample_receipt_words):
        """Test formatting word context embeddings for OpenAI."""
        words_to_embed = sample_receipt_words[:2]
        all_words = sample_receipt_words[:3]
        
        # Mock required methods
        for word in all_words:
            word.bounding_box = {
                "y": word.y1,
                "height": word.y2 - word.y1,
                "width": word.x2 - word.x1
            }
            word.top_left = {"y": word.y2}
            word.bottom_left = {"y": word.y1}
        
        with patch('receipt_label.embedding.word.submit._format_word_context_embedding_input') as mock_format:
            mock_format.return_value = "formatted_context"
            
            inputs = format_word_context_embedding(words_to_embed, all_words)
        
        assert len(inputs) == 2
        
        # Check first input structure
        first_input = inputs[0]
        assert "custom_id" in first_input
        assert first_input["method"] == "POST"
        assert first_input["url"] == "/v1/embeddings"
        assert first_input["body"]["model"] == "text-embedding-3-small"
        assert first_input["body"]["input"] == "formatted_context"
        
        # Check custom_id format
        expected_id = "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001"
        assert first_input["custom_id"] == expected_id

    def test_write_ndjson(self):
        """Test writing NDJSON file."""
        batch_id = "test-batch-123"
        input_data = [
            {"custom_id": "test_1", "data": "value1"},
            {"custom_id": "test_2", "data": "value2"}
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
        filepath = Path("/tmp/test.ndjson")
        
        with patch('pathlib.Path.open', mock_open()) as mock_file:
            with patch('receipt_label.embedding.word.submit.get_client_manager',
                       return_value=mock_client_manager):
                result = upload_to_openai(filepath)
        
        assert result.id == "file_test_123"
        
        # Should have called OpenAI files create
        mock_client_manager.openai.files.create.assert_called_once()
        call_kwargs = mock_client_manager.openai.files.create.call_args.kwargs
        assert call_kwargs["purpose"] == "batch"

    def test_submit_openai_batch(self, mock_client_manager):
        """Test submitting batch to OpenAI."""
        file_id = "file_test_123"
        
        with patch('receipt_label.embedding.word.submit.get_client_manager',
                   return_value=mock_client_manager):
            result = submit_openai_batch(file_id)
        
        assert result.id == "batch_test_456"
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
        batch_id = "test-batch-123"
        openai_batch_id = "batch_openai_456"
        
        # Create mock NDJSON content
        ndjson_content = '\n'.join([
            '{"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00001#WORD#00001"}',
            '{"custom_id": "IMAGE#IMG001#RECEIPT#00001#LINE#00002#WORD#00001"}',
            '{"custom_id": "IMAGE#IMG002#RECEIPT#00002#LINE#00001#WORD#00001"}',
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
            batch_id="test-batch",
            batch_type="EMBEDDING",
            openai_batch_id="batch_openai_123",
            status="PENDING"
        )
        
        with patch('receipt_label.embedding.word.submit.get_client_manager',
                   return_value=mock_client_manager):
            add_batch_summary(summary)
        
        mock_client_manager.dynamo.add_batch_summary.assert_called_once_with(summary)

    def test_update_word_embedding_status(self, mock_client_manager, sample_receipt_words):
        """Test updating word embedding status."""
        words = sample_receipt_words[:3]
        
        with patch('receipt_label.embedding.word.submit.get_client_manager',
                   return_value=mock_client_manager):
            update_word_embedding_status(words)
        
        # Should update all words to PENDING status
        for word in words:
            assert word.embedding_status == EmbeddingStatus.PENDING.value
        
        mock_client_manager.dynamo.update_receipt_words.assert_called_once_with(words)

    def test_error_handling_invalid_word_data(self, sample_receipt_words):
        """Test error handling with invalid word data."""
        # Test with word missing required attributes
        invalid_word = ReceiptWord(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            text="Invalid", x1=0, y1=0, x2=0, y2=0,  # Invalid coordinates
            confidence=0.0, embedding_status=EmbeddingStatus.NONE.value
        )
        
        # Should handle gracefully without crashing
        try:
            chunk_into_embedding_batches([invalid_word])
        except Exception as e:
            pytest.fail(f"Should handle invalid word data gracefully: {e}")

    def test_batch_chunking_efficiency(self, sample_receipt_words):
        """Test that batch chunking handles large datasets efficiently."""
        # Create large dataset
        large_dataset = []
        for i in range(1000):
            word = ReceiptWord(
                image_id=f"IMG{i//100}", receipt_id=i//10, line_id=i%10, word_id=1,
                text=f"word_{i}", x1=i, y1=i, x2=i+50, y2=i+20,
                confidence=0.95, embedding_status=EmbeddingStatus.NONE.value,
                is_noise=False
            )
            large_dataset.append(word)
        
        # Should process efficiently without timeout
        batches = chunk_into_embedding_batches(large_dataset)
        
        # Should group correctly
        assert len(batches) == 10  # 10 images
        
        total_words = sum(
            len(receipt_dict)
            for image_dict in batches.values()
            for receipt_dict in image_dict.values()
        )
        assert total_words == 1000

    def test_custom_id_format_consistency(self, sample_receipt_words):
        """Test that custom ID format is consistent across functions."""
        words_to_embed = sample_receipt_words[:1]
        all_words = sample_receipt_words[:3]
        
        # Mock required methods
        for word in all_words:
            word.bounding_box = {"y": word.y1, "height": 20, "width": 100}
            word.top_left = {"y": word.y2}
            word.bottom_left = {"y": word.y1}
        
        with patch('receipt_label.embedding.word.submit._format_word_context_embedding_input',
                   return_value="test_context"):
            inputs = format_word_context_embedding(words_to_embed, all_words)
        
        custom_id = inputs[0]["custom_id"]
        
        # Should match expected format
        expected_format = r"IMAGE#IMG001#RECEIPT#\d{5}#LINE#\d{5}#WORD#\d{5}"
        import re
        assert re.match(expected_format, custom_id)
        
        # Should be parseable by create_batch_summary
        parts = custom_id.split("#")
        assert parts[0] == "IMAGE"
        assert parts[1] == "IMG001"
        assert parts[2] == "RECEIPT" 
        assert int(parts[3]) == 1