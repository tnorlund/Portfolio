# ChromaDB Compaction Migration: Testing Strategy

**Status**: Planning
**Created**: December 15, 2024
**Last Updated**: December 15, 2024

## Table of Contents

1. [Testing Philosophy](#testing-philosophy)
2. [Unit Tests](#unit-tests)
3. [Integration Tests](#integration-tests)
4. [End-to-End Tests](#end-to-end-tests)
5. [Test Implementation Guide](#test-implementation-guide)
6. [Coverage Requirements](#coverage-requirements)

## Testing Philosophy

### Testing Pyramid

```
        /\
       /  \  E2E Tests (5%)
      /    \  - Full Lambda workflows
     /------\ - AWS integration
    /        \ Integration Tests (25%)
   /          \ - Module interactions
  /            \ - Mock AWS services
 /--------------\ Unit Tests (70%)
                 - Pure functions
                 - Domain logic
                 - No AWS dependencies
```

### Test Categories

1. **Unit Tests**: Test individual functions in isolation
2. **Integration Tests**: Test module interactions and ChromaDB operations
3. **End-to-End Tests**: Test full Lambda handler workflows

## Unit Tests

Unit tests focus on pure business logic without AWS dependencies.

### Tests for `receipt_chroma/compaction/operations.py`

#### Test File: `receipt_chroma/tests/unit/test_compaction_operations.py`

```python
"""Unit tests for ChromaDB compaction operations.

These tests focus on the business logic of update/remove operations
without requiring actual ChromaDB instances or AWS services.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from receipt_chroma.compaction.operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    reconstruct_label_metadata,
)


class TestUpdateReceiptMetadata:
    """Test suite for update_receipt_metadata function."""

    def test_update_metadata_for_words_collection(self, mock_collection, mock_dynamo_client, mock_logger):
        """Test updating metadata for words collection."""
        # Arrange
        mock_collection.name = "words"
        mock_dynamo_client.list_receipt_words_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=1),
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=2),
        ]

        changes = {"merchant_name": "New Merchant"}

        # Act
        updated_count = update_receipt_metadata(
            collection=mock_collection,
            image_id="img1",
            receipt_id=1,
            changes=changes,
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo_client,
        )

        # Assert
        assert updated_count == 2
        mock_collection.update.assert_called_once()
        mock_dynamo_client.list_receipt_words_from_receipt.assert_called_once_with("img1", 1)

    def test_update_metadata_for_lines_collection(self, mock_collection, mock_dynamo_client, mock_logger):
        """Test updating metadata for lines collection."""
        # Arrange
        mock_collection.name = "lines"
        mock_dynamo_client.list_receipt_lines_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1),
            Mock(image_id="img1", receipt_id=1, line_id=2),
        ]

        changes = {"merchant_name": "New Merchant"}

        # Act
        updated_count = update_receipt_metadata(
            collection=mock_collection,
            image_id="img1",
            receipt_id=1,
            changes=changes,
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo_client,
        )

        # Assert
        assert updated_count == 2
        mock_collection.update.assert_called_once()

    def test_update_metadata_handles_empty_receipt(self, mock_collection, mock_dynamo_client, mock_logger):
        """Test updating metadata when receipt has no words/lines."""
        # Arrange
        mock_collection.name = "words"
        mock_dynamo_client.list_receipt_words_from_receipt.return_value = []

        # Act
        updated_count = update_receipt_metadata(
            collection=mock_collection,
            image_id="img1",
            receipt_id=1,
            changes={"merchant_name": "Test"},
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo_client,
        )

        # Assert
        assert updated_count == 0
        mock_collection.update.assert_not_called()

    def test_update_metadata_handles_dynamo_error(self, mock_collection, mock_dynamo_client, mock_logger):
        """Test handling DynamoDB query errors."""
        # Arrange
        mock_collection.name = "words"
        mock_dynamo_client.list_receipt_words_from_receipt.side_effect = Exception("DynamoDB error")

        # Act
        updated_count = update_receipt_metadata(
            collection=mock_collection,
            image_id="img1",
            receipt_id=1,
            changes={"merchant_name": "Test"},
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo_client,
        )

        # Assert
        assert updated_count == 0
        mock_logger.error.assert_called()


class TestRemoveReceiptMetadata:
    """Test suite for remove_receipt_metadata function."""

    def test_remove_metadata_for_words(self, mock_collection, mock_dynamo_client, mock_logger):
        """Test removing embeddings for words collection."""
        # Arrange
        mock_collection.name = "words"
        mock_dynamo_client.list_receipt_words_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=1),
        ]

        # Act
        removed_count = remove_receipt_metadata(
            collection=mock_collection,
            image_id="img1",
            receipt_id=1,
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo_client,
        )

        # Assert
        assert removed_count == 1
        mock_collection.delete.assert_called_once()


class TestUpdateWordLabels:
    """Test suite for update_word_labels function."""

    def test_update_label_with_changes(self, mock_collection, mock_logger):
        """Test updating word label with field changes."""
        # Arrange
        chromadb_id = "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"
        changes = {"label": "MERCHANT_NAME"}
        record_snapshot = {"label": "MERCHANT_NAME", "reasoning": "Clear text"}
        entity_data = {
            "image_id": "img1",
            "receipt_id": 1,
            "line_id": 1,
            "word_id": 1,
            "label": "MERCHANT_NAME",
        }

        mock_collection.get.return_value = {
            "ids": [chromadb_id],
            "metadatas": [{"label": "UNKNOWN"}],
        }

        # Act
        updated_count = update_word_labels(
            collection=mock_collection,
            chromadb_id=chromadb_id,
            changes=changes,
            record_snapshot=record_snapshot,
            entity_data=entity_data,
            logger=mock_logger,
        )

        # Assert
        assert updated_count == 1
        mock_collection.update.assert_called_once()

    def test_update_label_missing_embedding(self, mock_collection, mock_logger):
        """Test updating label when embedding doesn't exist."""
        # Arrange
        chromadb_id = "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"
        mock_collection.get.return_value = {"ids": [], "metadatas": []}

        # Act
        updated_count = update_word_labels(
            collection=mock_collection,
            chromadb_id=chromadb_id,
            changes={"label": "TEST"},
            record_snapshot=None,
            entity_data={},
            logger=mock_logger,
        )

        # Assert
        assert updated_count == 0
        mock_logger.warning.assert_called()


class TestReconstructLabelMetadata:
    """Test suite for reconstruct_label_metadata function."""

    def test_reconstruct_from_snapshot(self):
        """Test reconstructing label metadata from record snapshot."""
        # Arrange
        record_snapshot = {
            "label": "MERCHANT_NAME",
            "reasoning": "Clear merchant text",
            "validation_status": "VALIDATED",
            "label_proposed_by": "agent_v1",
        }

        # Act
        result = reconstruct_label_metadata(record_snapshot, {})

        # Assert
        assert result["label"] == "MERCHANT_NAME"
        assert result["reasoning"] == "Clear merchant text"
        assert result["validation_status"] == "VALIDATED"

    def test_reconstruct_from_entity_data_fallback(self):
        """Test falling back to entity_data when snapshot unavailable."""
        # Arrange
        entity_data = {"label": "DATE"}

        # Act
        result = reconstruct_label_metadata(None, entity_data)

        # Assert
        assert result["label"] == "DATE"


# Pytest Fixtures
@pytest.fixture
def mock_collection():
    """Mock ChromaDB collection."""
    collection = MagicMock()
    collection.name = "words"
    collection.update = MagicMock()
    collection.delete = MagicMock()
    collection.get = MagicMock()
    return collection


@pytest.fixture
def mock_dynamo_client():
    """Mock DynamoDB client."""
    client = MagicMock()
    client.list_receipt_words_from_receipt = MagicMock()
    client.list_receipt_lines_from_receipt = MagicMock()
    return client


@pytest.fixture
def mock_logger():
    """Mock logger."""
    logger = MagicMock()
    return logger
```

### Tests for `receipt_chroma/stream/change_detector.py`

#### Test File: `receipt_chroma/tests/unit/test_change_detector.py`

```python
"""Unit tests for stream change detection.

Tests the logic that identifies ChromaDB-relevant field changes.
"""

import pytest
from receipt_chroma.stream.change_detector import (
    get_chromadb_relevant_changes,
    CHROMADB_RELEVANT_FIELDS,
)
from receipt_chroma.stream.models import FieldChange
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


class TestGetChromaDBRelevantChanges:
    """Test suite for get_chromadb_relevant_changes function."""

    def test_detect_merchant_name_change(self):
        """Test detecting merchant name change in metadata."""
        # Arrange
        old_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="Old Merchant",
            canonical_merchant_name=None,
        )
        new_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="New Merchant",
            canonical_merchant_name=None,
        )

        # Act
        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA",
            old_entity,
            new_entity,
        )

        # Assert
        assert "merchant_name" in changes
        assert changes["merchant_name"].old == "Old Merchant"
        assert changes["merchant_name"].new == "New Merchant"

    def test_ignore_irrelevant_fields(self):
        """Test that irrelevant fields are ignored."""
        # Arrange - timestamp_added is not ChromaDB-relevant
        old_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="Merchant",
            timestamp_added="2024-01-01T00:00:00Z",
        )
        new_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="Merchant",
            timestamp_added="2024-01-02T00:00:00Z",
        )

        # Act
        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA",
            old_entity,
            new_entity,
        )

        # Assert
        assert len(changes) == 0

    def test_detect_label_change(self):
        """Test detecting label change in word labels."""
        # Arrange
        old_entity = ReceiptWordLabel(
            image_id="img1",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="UNKNOWN",
            reasoning="",
        )
        new_entity = ReceiptWordLabel(
            image_id="img1",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
            reasoning="Clear merchant text",
        )

        # Act
        changes = get_chromadb_relevant_changes(
            "RECEIPT_WORD_LABEL",
            old_entity,
            new_entity,
        )

        # Assert
        assert "label" in changes
        assert "reasoning" in changes
        assert changes["label"].old == "UNKNOWN"
        assert changes["label"].new == "MERCHANT_NAME"

    def test_handle_insert_event(self):
        """Test handling INSERT events (old_entity is None)."""
        # Arrange
        new_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="New Merchant",
        )

        # Act
        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA",
            None,  # INSERT event
            new_entity,
        )

        # Assert
        assert "merchant_name" in changes
        assert changes["merchant_name"].old is None
        assert changes["merchant_name"].new == "New Merchant"

    def test_handle_remove_event(self):
        """Test handling REMOVE events (new_entity is None)."""
        # Arrange
        old_entity = ReceiptMetadata(
            image_id="img1",
            receipt_id=1,
            merchant_name="Old Merchant",
        )

        # Act
        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA",
            old_entity,
            None,  # REMOVE event
        )

        # Assert
        assert "merchant_name" in changes
        assert changes["merchant_name"].old == "Old Merchant"
        assert changes["merchant_name"].new is None


class TestChromaDBRelevantFields:
    """Test suite for CHROMADB_RELEVANT_FIELDS constant."""

    def test_has_metadata_fields(self):
        """Test that metadata fields are defined."""
        assert "RECEIPT_METADATA" in CHROMADB_RELEVANT_FIELDS
        fields = CHROMADB_RELEVANT_FIELDS["RECEIPT_METADATA"]
        assert "merchant_name" in fields
        assert "canonical_merchant_name" in fields

    def test_has_label_fields(self):
        """Test that label fields are defined."""
        assert "RECEIPT_WORD_LABEL" in CHROMADB_RELEVANT_FIELDS
        fields = CHROMADB_RELEVANT_FIELDS["RECEIPT_WORD_LABEL"]
        assert "label" in fields
        assert "reasoning" in fields
        assert "validation_status" in fields
```

### Tests for `receipt_chroma/compaction/models.py`

#### Test File: `receipt_chroma/tests/unit/test_compaction_models.py`

```python
"""Unit tests for compaction data models."""

import pytest
from receipt_chroma.compaction.models import (
    StreamMessage,
    MetadataUpdateResult,
    LabelUpdateResult,
)
from receipt_dynamo.constants import ChromaDBCollection


class TestStreamMessage:
    """Test suite for StreamMessage dataclass."""

    def test_create_stream_message(self):
        """Test creating a stream message."""
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": "img1", "receipt_id": 1},
            changes={"merchant_name": "New"},
            event_name="MODIFY",
            collection=ChromaDBCollection.WORDS,
        )

        assert msg.entity_type == "RECEIPT_METADATA"
        assert msg.source == "dynamodb_stream"  # default value

    def test_stream_message_immutable(self):
        """Test that StreamMessage is immutable (frozen)."""
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={},
            changes={},
            event_name="MODIFY",
            collection=ChromaDBCollection.WORDS,
        )

        with pytest.raises(AttributeError):
            msg.entity_type = "DIFFERENT"


class TestMetadataUpdateResult:
    """Test suite for MetadataUpdateResult dataclass."""

    def test_to_dict_success(self):
        """Test converting successful result to dict."""
        result = MetadataUpdateResult(
            database="words",
            collection="words",
            updated_count=5,
            image_id="img1",
            receipt_id=1,
        )

        d = result.to_dict()
        assert d["database"] == "words"
        assert d["updated_count"] == 5
        assert "error" not in d

    def test_to_dict_with_error(self):
        """Test converting error result to dict."""
        result = MetadataUpdateResult(
            database="words",
            collection="words",
            updated_count=0,
            image_id="img1",
            receipt_id=1,
            error="Collection not found",
        )

        d = result.to_dict()
        assert d["error"] == "Collection not found"
        assert d["updated_count"] == 0


class TestLabelUpdateResult:
    """Test suite for LabelUpdateResult dataclass."""

    def test_to_dict_with_changes(self):
        """Test converting result with changes to dict."""
        result = LabelUpdateResult(
            chromadb_id="IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001",
            updated_count=1,
            event_name="MODIFY",
            changes=["label", "reasoning"],
        )

        d = result.to_dict()
        assert d["chromadb_id"] == "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"
        assert d["changes"] == ["label", "reasoning"]
        assert "error" not in d
```

## Integration Tests

Integration tests verify module interactions and real ChromaDB operations.

### Tests for ChromaDB Operations with Real Database

#### Test File: `receipt_chroma/tests/integration/test_compaction_operations_integration.py`

```python
"""Integration tests for compaction operations with real ChromaDB.

These tests use a temporary ChromaDB instance to verify that operations
work correctly end-to-end.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, MagicMock

from receipt_chroma import ChromaClient
from receipt_chroma.compaction.operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
)


@pytest.fixture
def temp_chroma_dir():
    """Create temporary directory for ChromaDB."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def chroma_client_with_data(temp_chroma_dir):
    """Create ChromaDB client with test data."""
    client = ChromaClient(persist_directory=temp_chroma_dir, mode="delta")

    # Create words collection with test embeddings
    client.upsert(
        collection_name="words",
        ids=[
            "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001",
            "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00002",
        ],
        embeddings=[[0.1] * 1536, [0.2] * 1536],
        documents=["Hello", "World"],
        metadatas=[
            {
                "image_id": "img1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 1,
                "merchant_name": "Old Merchant",
                "label": "UNKNOWN",
            },
            {
                "image_id": "img1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 2,
                "merchant_name": "Old Merchant",
                "label": "UNKNOWN",
            },
        ],
    )

    yield client
    client.close()


class TestUpdateReceiptMetadataIntegration:
    """Integration tests for update_receipt_metadata."""

    def test_update_metadata_actually_changes_embeddings(
        self, chroma_client_with_data, temp_chroma_dir
    ):
        """Test that metadata updates actually modify ChromaDB."""
        # Arrange
        collection = chroma_client_with_data.get_collection("words")
        mock_dynamo = MagicMock()
        mock_dynamo.list_receipt_words_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=1),
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=2),
        ]
        mock_logger = Mock()

        # Act
        updated_count = update_receipt_metadata(
            collection=collection,
            image_id="img1",
            receipt_id=1,
            changes={"merchant_name": "New Merchant"},
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo,
        )

        # Assert
        assert updated_count == 2

        # Verify the changes were applied
        result = collection.get(ids=["IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"])
        assert result["metadatas"][0]["merchant_name"] == "New Merchant"

    def test_update_metadata_preserves_other_fields(
        self, chroma_client_with_data
    ):
        """Test that updating metadata preserves unchanged fields."""
        # Arrange
        collection = chroma_client_with_data.get_collection("words")
        mock_dynamo = MagicMock()
        mock_dynamo.list_receipt_words_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=1),
        ]
        mock_logger = Mock()

        # Act
        update_receipt_metadata(
            collection=collection,
            image_id="img1",
            receipt_id=1,
            changes={"merchant_name": "New Merchant"},
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo,
        )

        # Assert - label should be preserved
        result = collection.get(ids=["IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"])
        assert result["metadatas"][0]["label"] == "UNKNOWN"


class TestRemoveReceiptMetadataIntegration:
    """Integration tests for remove_receipt_metadata."""

    def test_remove_actually_deletes_embeddings(
        self, chroma_client_with_data
    ):
        """Test that remove operation actually deletes from ChromaDB."""
        # Arrange
        collection = chroma_client_with_data.get_collection("words")
        mock_dynamo = MagicMock()
        mock_dynamo.list_receipt_words_from_receipt.return_value = [
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=1),
            Mock(image_id="img1", receipt_id=1, line_id=1, word_id=2),
        ]
        mock_logger = Mock()

        # Act
        removed_count = remove_receipt_metadata(
            collection=collection,
            image_id="img1",
            receipt_id=1,
            logger=mock_logger,
            get_dynamo_client_func=lambda: mock_dynamo,
        )

        # Assert
        assert removed_count == 2

        # Verify embeddings are gone
        result = collection.get(ids=["IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"])
        assert len(result["ids"]) == 0


class TestUpdateWordLabelsIntegration:
    """Integration tests for update_word_labels."""

    def test_update_label_changes_metadata(self, chroma_client_with_data):
        """Test that label updates actually modify ChromaDB metadata."""
        # Arrange
        collection = chroma_client_with_data.get_collection("words")
        chromadb_id = "IMAGE#img1#RECEIPT#00001#LINE#00001#WORD#00001"
        changes = {"label": "MERCHANT_NAME"}
        record_snapshot = {
            "label": "MERCHANT_NAME",
            "reasoning": "Clear merchant text",
        }
        entity_data = {
            "image_id": "img1",
            "receipt_id": 1,
            "line_id": 1,
            "word_id": 1,
        }
        mock_logger = Mock()

        # Act
        updated_count = update_word_labels(
            collection=collection,
            chromadb_id=chromadb_id,
            changes=changes,
            record_snapshot=record_snapshot,
            entity_data=entity_data,
            logger=mock_logger,
        )

        # Assert
        assert updated_count == 1

        # Verify the changes
        result = collection.get(ids=[chromadb_id])
        assert result["metadatas"][0]["label"] == "MERCHANT_NAME"
        assert result["metadatas"][0]["reasoning"] == "Clear merchant text"
```

## End-to-End Tests

E2E tests verify full Lambda handler workflows.

### Test File: `infra/chromadb_compaction/lambdas/tests/test_lambda_handlers_e2e.py`

```python
"""End-to-end tests for Lambda handlers.

These tests verify the full workflow from Lambda event to result,
mocking only AWS services (SQS, S3, DynamoDB).
"""

import pytest
import json
import tempfile
import shutil
from unittest.mock import Mock, MagicMock, patch
from enhanced_compaction_handler import lambda_handler as compaction_handler
from stream_processor import lambda_handler as stream_handler


class TestStreamProcessorE2E:
    """End-to-end tests for stream processor Lambda."""

    @patch("processor.sqs_publisher.boto3.client")
    @patch("processor.parsers.item_to_receipt_metadata")
    def test_process_metadata_change_event(
        self, mock_parser, mock_boto3
    ):
        """Test processing a metadata change event end-to-end."""
        # Arrange
        mock_sqs = MagicMock()
        mock_boto3.return_value = mock_sqs

        # Mock entity parser
        from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
        mock_parser.side_effect = [
            ReceiptMetadata(
                image_id="img1",
                receipt_id=1,
                merchant_name="Old",
            ),
            ReceiptMetadata(
                image_id="img1",
                receipt_id=1,
                merchant_name="New",
            ),
        ]

        event = {
            "Records": [
                {
                    "eventID": "1",
                    "eventName": "MODIFY",
                    "dynamodb": {
                        "Keys": {
                            "PK": {"S": "IMAGE#img1"},
                            "SK": {"S": "RECEIPT#00001#METADATA"},
                        },
                        "OldImage": {
                            "merchant_name": {"S": "Old"},
                        },
                        "NewImage": {
                            "merchant_name": {"S": "New"},
                        },
                    },
                }
            ]
        }
        context = Mock()

        # Act
        result = stream_handler(event, context)

        # Assert
        assert result["statusCode"] == 200
        assert result["processed_records"] > 0
        mock_sqs.send_message.assert_called()


class TestCompactionHandlerE2E:
    """End-to-end tests for compaction handler Lambda."""

    @patch("receipt_chroma.s3.snapshot.boto3.client")
    @patch("compaction.operations.os.environ")
    def test_process_metadata_update_message(
        self, mock_env, mock_boto3, temp_chroma_dir
    ):
        """Test processing metadata update message end-to-end."""
        # Arrange
        mock_env.get.return_value = temp_chroma_dir
        mock_env.__getitem__.side_effect = lambda k: {
            "CHROMADB_BUCKET": "test-bucket",
            "DYNAMODB_TABLE_NAME": "test-table",
        }[k]

        # Create SQS event with stream message
        event = {
            "Records": [
                {
                    "messageId": "msg1",
                    "receiptHandle": "handle1",
                    "body": json.dumps({
                        "entity_type": "RECEIPT_METADATA",
                        "entity_data": {
                            "image_id": "img1",
                            "receipt_id": 1,
                        },
                        "changes": {
                            "merchant_name": "New Merchant",
                        },
                        "event_name": "MODIFY",
                        "collection": "words",
                    }),
                }
            ]
        }
        context = Mock()

        # Act
        result = compaction_handler(event, context)

        # Assert - should process successfully
        assert result["statusCode"] == 200
```

## Coverage Requirements

### Minimum Coverage Targets

| Module | Unit Test Coverage | Integration Test Coverage |
|--------|-------------------|--------------------------|
| `compaction/operations.py` | 90% | 80% |
| `compaction/compaction_run.py` | 85% | 75% |
| `compaction/efs_snapshot_manager.py` | 85% | 70% |
| `stream/change_detector.py` | 95% | N/A (pure logic) |
| `compaction/models.py` | 100% | N/A (data classes) |
| `stream/models.py` | 100% | N/A (data classes) |

### Running Coverage Reports

```bash
# Unit tests with coverage
cd receipt_chroma
pytest tests/unit/ --cov=receipt_chroma --cov-report=html --cov-report=term

# Integration tests with coverage
pytest tests/integration/ --cov=receipt_chroma --cov-report=html --cov-report=term

# Combined coverage
pytest tests/ --cov=receipt_chroma --cov-report=html --cov-report=term

# View coverage report
open htmlcov/index.html
```

### Coverage Analysis

After running tests, review:

1. **Line coverage**: Are all code paths executed?
2. **Branch coverage**: Are all conditional branches tested?
3. **Edge cases**: Are error conditions tested?
4. **Integration points**: Are module interactions tested?

## Test Implementation Checklist

Before considering testing complete:

### Unit Tests
- [ ] All operations functions have tests
- [ ] All change detector functions have tests
- [ ] All model classes have tests
- [ ] Error conditions are tested
- [ ] Edge cases are covered
- [ ] Mock dependencies are used properly

### Integration Tests
- [ ] Real ChromaDB operations tested
- [ ] S3 operations tested (with moto)
- [ ] DynamoDB operations tested (with moto)
- [ ] Module interactions tested
- [ ] Temp files cleaned up properly

### End-to-End Tests
- [ ] Lambda handler workflows tested
- [ ] SQS message processing tested
- [ ] Error handling tested
- [ ] Partial batch failure tested
- [ ] Metrics and logging verified

### Coverage
- [ ] Unit test coverage meets targets
- [ ] Integration test coverage meets targets
- [ ] Critical paths have 100% coverage
- [ ] Coverage report generated
- [ ] Gaps identified and documented

## Next Steps

After implementing these tests:

1. **Run in CI/CD**: Add tests to GitHub Actions
2. **Monitor Failures**: Set up alerts for test failures
3. **Maintain Tests**: Update tests when code changes
4. **Document Gaps**: Track known testing gaps
5. **Improve Coverage**: Incrementally increase coverage

## References

- [pytest Documentation](https://docs.pytest.org/)
- [unittest.mock Documentation](https://docs.python.org/3/library/unittest.mock.html)
- [moto Documentation](http://docs.getmoto.org/) - AWS mocking
- [pytest-cov Documentation](https://pytest-cov.readthedocs.io/)

