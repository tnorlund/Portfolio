"""Unit tests for compaction models."""

import pytest
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma.compaction.models import (
    MetadataUpdateResult,
    LabelUpdateResult,
    CollectionUpdateResult,
)


@pytest.mark.unit
class TestMetadataUpdateResult:
    """Test MetadataUpdateResult model."""

    def test_create_metadata_result_success(self):
        """Test creating a successful metadata update result."""
        result = MetadataUpdateResult(
            database="lines",
            collection="lines",
            updated_count=5,
            image_id="test-image-id",
            receipt_id=123,
        )

        assert result.database == "lines"
        assert result.collection == "lines"
        assert result.updated_count == 5
        assert result.image_id == "test-image-id"
        assert result.receipt_id == 123
        assert result.error is None

    def test_create_metadata_result_with_error(self):
        """Test creating a metadata update result with an error."""
        result = MetadataUpdateResult(
            database="words",
            collection="words",
            updated_count=0,
            image_id="test-image-id",
            receipt_id=456,
            error="Collection not found",
        )

        assert result.updated_count == 0
        assert result.error == "Collection not found"

    def test_metadata_result_is_frozen(self):
        """Test that MetadataUpdateResult is immutable."""
        result = MetadataUpdateResult(
            database="lines",
            collection="lines",
            updated_count=5,
            image_id="test-id",
            receipt_id=1,
        )

        with pytest.raises(AttributeError):
            result.updated_count = 10

    def test_metadata_result_to_dict(self):
        """Test converting MetadataUpdateResult to dictionary."""
        result = MetadataUpdateResult(
            database="lines",
            collection="lines",
            updated_count=5,
            image_id="test-id",
            receipt_id=1,
        )

        result_dict = result.to_dict()

        assert result_dict["database"] == "lines"
        assert result_dict["collection"] == "lines"
        assert result_dict["updated_count"] == 5
        assert result_dict["image_id"] == "test-id"
        assert result_dict["receipt_id"] == 1
        assert "error" not in result_dict

    def test_metadata_result_to_dict_with_error(self):
        """Test converting MetadataUpdateResult with error to dictionary."""
        result = MetadataUpdateResult(
            database="lines",
            collection="lines",
            updated_count=0,
            image_id="test-id",
            receipt_id=1,
            error="Test error",
        )

        result_dict = result.to_dict()
        assert result_dict["error"] == "Test error"


@pytest.mark.unit
class TestLabelUpdateResult:
    """Test LabelUpdateResult model."""

    def test_create_label_result_success(self):
        """Test creating a successful label update result."""
        result = LabelUpdateResult(
            chromadb_id="IMAGE#id#RECEIPT#00001#LINE#00001#WORD#00001",
            updated_count=1,
            event_name="MODIFY",
            changes=["validation_status", "label_confidence"],
        )

        assert (
            result.chromadb_id
            == "IMAGE#id#RECEIPT#00001#LINE#00001#WORD#00001"
        )
        assert result.updated_count == 1
        assert result.event_name == "MODIFY"
        assert result.changes == ["validation_status", "label_confidence"]
        assert result.error is None

    def test_create_label_result_with_error(self):
        """Test creating a label update result with an error."""
        result = LabelUpdateResult(
            chromadb_id="unknown",
            updated_count=0,
            event_name="unknown",
            changes=[],
            error="Embedding not found",
        )

        assert result.updated_count == 0
        assert result.error == "Embedding not found"

    def test_label_result_is_frozen(self):
        """Test that LabelUpdateResult is immutable."""
        result = LabelUpdateResult(
            chromadb_id="test-id",
            updated_count=1,
            event_name="MODIFY",
            changes=[],
        )

        with pytest.raises(AttributeError):
            result.updated_count = 5

    def test_label_result_to_dict(self):
        """Test converting LabelUpdateResult to dictionary."""
        result = LabelUpdateResult(
            chromadb_id="test-id",
            updated_count=1,
            event_name="MODIFY",
            changes=["validation_status"],
        )

        result_dict = result.to_dict()

        assert result_dict["chromadb_id"] == "test-id"
        assert result_dict["updated_count"] == 1
        assert result_dict["event_name"] == "MODIFY"
        assert result_dict["changes"] == ["validation_status"]


@pytest.mark.unit
class TestCollectionUpdateResult:
    """Test CollectionUpdateResult model."""

    def test_create_collection_result(self):
        """Test creating a collection update result."""
        metadata_results = [
            MetadataUpdateResult(
                database="lines",
                collection="lines",
                updated_count=5,
                image_id="test-id",
                receipt_id=1,
            )
        ]

        label_results = [
            LabelUpdateResult(
                chromadb_id="test-id",
                updated_count=2,
                event_name="MODIFY",
                changes=["validation_status"],
            )
        ]

        result = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=metadata_results,
            label_updates=label_results,
            delta_merge_count=10,
            delta_merge_results=[{"run_id": "run-1", "merged_count": 10}],
        )

        assert result.collection == ChromaDBCollection.LINES
        assert len(result.metadata_updates) == 1
        assert len(result.label_updates) == 1
        assert result.delta_merge_count == 10

    def test_collection_result_total_metadata_updated(self):
        """Test total_metadata_updated property."""
        metadata_results = [
            MetadataUpdateResult(
                "lines", "lines", 5, "id1", 1
            ),
            MetadataUpdateResult(
                "lines", "lines", 3, "id2", 2
            ),
            MetadataUpdateResult(
                "lines", "lines", 0, "id3", 3, error="Failed"
            ),
        ]

        result = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=metadata_results,
            label_updates=[],
            delta_merge_count=0,
            delta_merge_results=[],
        )

        assert result.total_metadata_updated == 8  # 5 + 3, excluding error

    def test_collection_result_total_labels_updated(self):
        """Test total_labels_updated property."""
        label_results = [
            LabelUpdateResult("id1", 1, "MODIFY", []),
            LabelUpdateResult("id2", 1, "MODIFY", []),
            LabelUpdateResult("id3", 0, "MODIFY", [], error="Failed"),
        ]

        result = CollectionUpdateResult(
            collection=ChromaDBCollection.WORDS,
            metadata_updates=[],
            label_updates=label_results,
            delta_merge_count=0,
            delta_merge_results=[],
        )

        assert result.total_labels_updated == 2  # 1 + 1, excluding error

    def test_collection_result_has_errors(self):
        """Test has_errors property."""
        # No errors
        result_no_errors = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=[
                MetadataUpdateResult("lines", "lines", 5, "id1", 1)
            ],
            label_updates=[],
            delta_merge_count=0,
            delta_merge_results=[],
        )
        assert result_no_errors.has_errors is False

        # With metadata error
        result_with_metadata_error = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=[
                MetadataUpdateResult(
                    "lines", "lines", 0, "id1", 1, error="Failed"
                )
            ],
            label_updates=[],
            delta_merge_count=0,
            delta_merge_results=[],
        )
        assert result_with_metadata_error.has_errors is True

        # With label error
        result_with_label_error = CollectionUpdateResult(
            collection=ChromaDBCollection.WORDS,
            metadata_updates=[],
            label_updates=[
                LabelUpdateResult("id1", 0, "MODIFY", [], error="Failed")
            ],
            delta_merge_count=0,
            delta_merge_results=[],
        )
        assert result_with_label_error.has_errors is True

    def test_collection_result_to_dict(self):
        """Test converting CollectionUpdateResult to dictionary."""
        result = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=[
                MetadataUpdateResult("lines", "lines", 5, "id1", 1)
            ],
            label_updates=[
                LabelUpdateResult("label-id", 2, "MODIFY", ["status"])
            ],
            delta_merge_count=10,
            delta_merge_results=[{"run_id": "run-1", "merged_count": 10}],
        )

        result_dict = result.to_dict()

        assert result_dict["collection"] == "lines"
        assert len(result_dict["metadata_updates"]) == 1
        assert len(result_dict["label_updates"]) == 1
        assert result_dict["delta_merge_count"] == 10
        assert len(result_dict["delta_merge_results"]) == 1

    def test_collection_result_is_frozen(self):
        """Test that CollectionUpdateResult is immutable."""
        result = CollectionUpdateResult(
            collection=ChromaDBCollection.LINES,
            metadata_updates=[],
            label_updates=[],
            delta_merge_count=0,
            delta_merge_results=[],
        )

        with pytest.raises(AttributeError):
            result.delta_merge_count = 5
