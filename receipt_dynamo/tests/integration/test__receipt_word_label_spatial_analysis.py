"""
Integration tests for ReceiptWordLabelSpatialAnalysis operations in DynamoDB.

This module tests the ReceiptWordLabelSpatialAnalysis-related methods of 
DynamoClient, including add, get, update, delete, and query operations.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Type
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (
    ReceiptWordLabelSpatialAnalysis,
    SpatialRelationship,
)

# =============================================================================
# TEST DATA AND FIXTURES
# =============================================================================

CORRECT_SPATIAL_ANALYSIS_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "receipt_id": 1,
    "line_id": 2,
    "word_id": 3,
    "from_label": "TAX",
    "from_position": {"x": 100.5, "y": 200.75},
    "spatial_relationships": [
        SpatialRelationship(
            to_label="SUBTOTAL",
            to_line_id=1,
            to_word_id=2,
            distance=50.25,
            angle=1.57,
        ),
        SpatialRelationship(
            to_label="TOTAL",
            to_line_id=3,
            to_word_id=4,
            distance=75.80,
            angle=3.14,
        ),
    ],
    "timestamp_added": datetime(2024, 3, 20, 12, 0, 0),
    "analysis_version": "1.0",
}


@pytest.fixture(name="sample_spatial_analysis")
def _sample_spatial_analysis() -> ReceiptWordLabelSpatialAnalysis:
    """Provides a valid ReceiptWordLabelSpatialAnalysis for testing."""
    return ReceiptWordLabelSpatialAnalysis(**CORRECT_SPATIAL_ANALYSIS_PARAMS)


@pytest.fixture(name="another_spatial_analysis")
def _another_spatial_analysis() -> ReceiptWordLabelSpatialAnalysis:
    """Provides a second valid ReceiptWordLabelSpatialAnalysis for testing."""
    return ReceiptWordLabelSpatialAnalysis(
        image_id="4a63915c-22f5-4f11-a3d9-c684eb4b9ef4",
        receipt_id=2,
        line_id=1,
        word_id=1,
        from_label="SUBTOTAL",
        from_position={"x": 150.0, "y": 100.0},
        spatial_relationships=[
            SpatialRelationship(
                to_label="TAX",
                to_line_id=2,
                to_word_id=3,
                distance=50.25,
                angle=4.71,
            ),
        ],
        timestamp_added=datetime(2024, 3, 20, 13, 0, 0),
        analysis_version="1.0",
    )


@pytest.fixture(name="batch_spatial_analyses")
def _batch_spatial_analyses() -> List[ReceiptWordLabelSpatialAnalysis]:
    """Provides a list of 5 spatial analyses for batch testing."""
    analyses = []
    base_time = datetime(2024, 3, 20, 12, 0, 0)
    labels = ["TAX", "SUBTOTAL", "TOTAL", "BUSINESS_NAME", "DATE"]
    
    for i in range(5):
        analyses.append(
            ReceiptWordLabelSpatialAnalysis(
                image_id=str(uuid4()),
                receipt_id=1,
                line_id=i + 1,
                word_id=i + 1,
                from_label=labels[i],
                from_position={"x": 100.0 + i * 10, "y": 200.0 + i * 5},
                spatial_relationships=[
                    SpatialRelationship(
                        to_label=labels[(i + 1) % 5],
                        to_line_id=((i + 1) % 5) + 1,
                        to_word_id=((i + 1) % 5) + 1,
                        distance=25.5 + i * 5,
                        angle=i * 0.785,  # 45 degrees in radians
                    ),
                ],
                timestamp_added=base_time,
                analysis_version="1.0",
            )
        )
    
    return analyses


# =============================================================================
# ERROR SCENARIOS FOR PARAMETERIZED TESTS
# =============================================================================

ADD_ERROR_SCENARIOS = [
    ("ConditionalCheckFailedException", EntityAlreadyExistsError, "already exists"),
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error"),
    ("ResourceNotFoundException", OperationError, "resource not found"),
    ("UnknownError", DynamoDBError, "DynamoDB error"),
]

UPDATE_ERROR_SCENARIOS = [
    ("ConditionalCheckFailedException", EntityNotFoundError, "not found"),
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error"),
    ("ResourceNotFoundException", OperationError, "resource not found"),
    ("UnknownError", DynamoDBError, "DynamoDB error"),
]

DELETE_ERROR_SCENARIOS = [
    ("ConditionalCheckFailedException", EntityNotFoundError, "not found"),
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error"),
    ("ResourceNotFoundException", DynamoDBError, "DynamoDB error"),
    ("UnknownError", DynamoDBError, "DynamoDB error"),
]

QUERY_ERROR_SCENARIOS = [
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error"),
    ("ResourceNotFoundException", OperationError, "resource not found"),
    ("UnknownError", DynamoDBError, "DynamoDB error"),
]


# =============================================================================
# BASIC CRUD OPERATIONS
# =============================================================================

class TestReceiptWordLabelSpatialAnalysisBasicOperations:
    """Test basic CRUD operations for ReceiptWordLabelSpatialAnalysis."""

    def test_add_spatial_analysis_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test successful addition of a spatial analysis."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        
        result = client.get_receipt_word_label_spatial_analysis(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
            line_id=sample_spatial_analysis.line_id,
            word_id=sample_spatial_analysis.word_id,
        )
        assert result == sample_spatial_analysis

    def test_add_duplicate_spatial_analysis_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test that adding a duplicate spatial analysis raises error."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        
        # Create duplicate with same keys
        duplicate = ReceiptWordLabelSpatialAnalysis(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
            line_id=sample_spatial_analysis.line_id,
            word_id=sample_spatial_analysis.word_id,
            from_label="DIFFERENT_LABEL",
            from_position={"x": 300.0, "y": 400.0},
            spatial_relationships=[],
            timestamp_added=datetime.now(),
            analysis_version="2.0",
        )
        
        with pytest.raises(EntityAlreadyExistsError, match="already exists"):
            client.add_receipt_word_label_spatial_analysis(duplicate)

    def test_get_spatial_analysis_not_found_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that getting a non-existent spatial analysis raises error."""
        client = DynamoClient(dynamodb_table)
        
        with pytest.raises(EntityNotFoundError, match="does not exist"):
            client.get_receipt_word_label_spatial_analysis(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=999,
                line_id=1,
                word_id=1,
            )

    def test_update_spatial_analysis_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test successful update of a spatial analysis."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        
        # Update the analysis
        updated_analysis = ReceiptWordLabelSpatialAnalysis(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
            line_id=sample_spatial_analysis.line_id,
            word_id=sample_spatial_analysis.word_id,
            from_label=sample_spatial_analysis.from_label,
            from_position={"x": 300.0, "y": 400.0},  # Updated position
            spatial_relationships=sample_spatial_analysis.spatial_relationships,
            timestamp_added=sample_spatial_analysis.timestamp_added,
            analysis_version="2.0",  # Updated version
        )
        
        client.update_receipt_word_label_spatial_analysis(updated_analysis)
        
        result = client.get_receipt_word_label_spatial_analysis(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
            line_id=sample_spatial_analysis.line_id,
            word_id=sample_spatial_analysis.word_id,
        )
        assert result == updated_analysis
        assert result.from_position == {"x": 300.0, "y": 400.0}
        assert result.analysis_version == "2.0"

    def test_update_spatial_analysis_not_found_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test that updating a non-existent spatial analysis raises error."""
        client = DynamoClient(dynamodb_table)
        
        with pytest.raises(EntityNotFoundError, match="not found"):
            client.update_receipt_word_label_spatial_analysis(sample_spatial_analysis)

    def test_delete_spatial_analysis_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test successful deletion of a spatial analysis."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        
        # Verify it exists
        result = client.get_receipt_word_label_spatial_analysis(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
            line_id=sample_spatial_analysis.line_id,
            word_id=sample_spatial_analysis.word_id,
        )
        assert result is not None
        
        # Delete it
        client.delete_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        
        # Verify it's gone
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_word_label_spatial_analysis(
                image_id=sample_spatial_analysis.image_id,
                receipt_id=sample_spatial_analysis.receipt_id,
                line_id=sample_spatial_analysis.line_id,
                word_id=sample_spatial_analysis.word_id,
            )


# =============================================================================
# BATCH OPERATIONS
# =============================================================================

class TestReceiptWordLabelSpatialAnalysisBatchOperations:
    """Test batch operations for ReceiptWordLabelSpatialAnalysis."""

    def test_add_spatial_analyses_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        batch_spatial_analyses: List[ReceiptWordLabelSpatialAnalysis],
    ) -> None:
        """Test successful batch addition of spatial analyses."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analyses(batch_spatial_analyses)

        for analysis in batch_spatial_analyses:
            result = client.get_receipt_word_label_spatial_analysis(
                image_id=analysis.image_id,
                receipt_id=analysis.receipt_id,
                line_id=analysis.line_id,
                word_id=analysis.word_id,
            )
            assert result == analysis

    def test_update_spatial_analyses_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        batch_spatial_analyses: List[ReceiptWordLabelSpatialAnalysis],
    ) -> None:
        """Test successful batch update of spatial analyses."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analyses(batch_spatial_analyses)
        
        # Update all analyses with new version
        updated_analyses = []
        for analysis in batch_spatial_analyses:
            updated = ReceiptWordLabelSpatialAnalysis(
                image_id=analysis.image_id,
                receipt_id=analysis.receipt_id,
                line_id=analysis.line_id,
                word_id=analysis.word_id,
                from_label=analysis.from_label,
                from_position=analysis.from_position,
                spatial_relationships=analysis.spatial_relationships,
                timestamp_added=analysis.timestamp_added,
                analysis_version="2.0",  # Updated version
            )
            updated_analyses.append(updated)
        
        client.update_receipt_word_label_spatial_analyses(updated_analyses)
        
        # Verify updates
        for updated in updated_analyses:
            result = client.get_receipt_word_label_spatial_analysis(
                image_id=updated.image_id,
                receipt_id=updated.receipt_id,
                line_id=updated.line_id,
                word_id=updated.word_id,
            )
            assert result.analysis_version == "2.0"

    def test_delete_spatial_analyses_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        batch_spatial_analyses: List[ReceiptWordLabelSpatialAnalysis],
    ) -> None:
        """Test successful batch deletion of spatial analyses."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analyses(batch_spatial_analyses)
        
        # Verify they exist
        for analysis in batch_spatial_analyses:
            result = client.get_receipt_word_label_spatial_analysis(
                image_id=analysis.image_id,
                receipt_id=analysis.receipt_id,
                line_id=analysis.line_id,
                word_id=analysis.word_id,
            )
            assert result is not None
        
        # Delete them
        client.delete_receipt_word_label_spatial_analyses(batch_spatial_analyses)
        
        # Verify they're gone
        for analysis in batch_spatial_analyses:
            with pytest.raises(EntityNotFoundError):
                client.get_receipt_word_label_spatial_analysis(
                    image_id=analysis.image_id,
                    receipt_id=analysis.receipt_id,
                    line_id=analysis.line_id,
                    word_id=analysis.word_id,
                )


# =============================================================================
# GSI QUERY OPERATIONS
# =============================================================================

class TestReceiptWordLabelSpatialAnalysisQueryOperations:
    """Test query operations using GSI for ReceiptWordLabelSpatialAnalysis."""

    def test_list_spatial_analyses_for_receipt(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
        another_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test listing spatial analyses for a specific receipt."""
        client = DynamoClient(dynamodb_table)
        
        # Create analyses for same receipt but different image
        same_receipt_analysis = ReceiptWordLabelSpatialAnalysis(
            image_id=sample_spatial_analysis.image_id,  # Same image
            receipt_id=sample_spatial_analysis.receipt_id,  # Same receipt
            line_id=5,  # Different line
            word_id=5,  # Different word
            from_label="BUSINESS_NAME",
            from_position={"x": 50.0, "y": 25.0},
            spatial_relationships=[],
            timestamp_added=datetime.now(),
            analysis_version="1.0",
        )
        
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        client.add_receipt_word_label_spatial_analysis(same_receipt_analysis)
        client.add_receipt_word_label_spatial_analysis(another_spatial_analysis)  # Different receipt
        
        # Query for analyses of specific receipt
        results, _ = client.list_spatial_analyses_for_receipt(
            image_id=sample_spatial_analysis.image_id,
            receipt_id=sample_spatial_analysis.receipt_id,
        )
        
        assert len(results) == 2
        result_keys = {
            (r.image_id, r.receipt_id, r.line_id, r.word_id) for r in results
        }
        expected_keys = {
            (sample_spatial_analysis.image_id, sample_spatial_analysis.receipt_id, 
             sample_spatial_analysis.line_id, sample_spatial_analysis.word_id),
            (same_receipt_analysis.image_id, same_receipt_analysis.receipt_id,
             same_receipt_analysis.line_id, same_receipt_analysis.word_id),
        }
        assert result_keys == expected_keys

    def test_get_spatial_analyses_by_label(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
        another_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test getting spatial analyses by label type."""
        client = DynamoClient(dynamodb_table)
        
        # Create analysis with same label but different receipt
        same_label_analysis = ReceiptWordLabelSpatialAnalysis(
            image_id="5b74a26d-33e6-4f22-b4ea-d795fc5c0fa5",
            receipt_id=3,
            line_id=1,
            word_id=1,
            from_label=sample_spatial_analysis.from_label,  # Same label (TAX)
            from_position={"x": 75.0, "y": 125.0},
            spatial_relationships=[],
            timestamp_added=datetime.now(),
            analysis_version="1.0",
        )
        
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        client.add_receipt_word_label_spatial_analysis(same_label_analysis)
        client.add_receipt_word_label_spatial_analysis(another_spatial_analysis)  # Different label
        
        # Query for analyses of specific label
        results, _ = client.get_spatial_analyses_by_label(
            label=sample_spatial_analysis.from_label
        )
        
        assert len(results) == 2
        for result in results:
            assert result.from_label == sample_spatial_analysis.from_label

    def test_list_spatial_analyses_for_image(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
        another_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
    ) -> None:
        """Test listing spatial analyses for a specific image."""
        client = DynamoClient(dynamodb_table)
        
        # Create analysis for same image but different receipt
        same_image_analysis = ReceiptWordLabelSpatialAnalysis(
            image_id=sample_spatial_analysis.image_id,  # Same image
            receipt_id=99,  # Different receipt
            line_id=1,
            word_id=1,
            from_label="DATE",
            from_position={"x": 200.0, "y": 50.0},
            spatial_relationships=[],
            timestamp_added=datetime.now(),
            analysis_version="1.0",
        )
        
        client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)
        client.add_receipt_word_label_spatial_analysis(same_image_analysis)
        client.add_receipt_word_label_spatial_analysis(another_spatial_analysis)  # Different image
        
        # Query for analyses of specific image
        results, _ = client.list_spatial_analyses_for_image(
            image_id=sample_spatial_analysis.image_id
        )
        
        assert len(results) == 2
        for result in results:
            assert result.image_id == sample_spatial_analysis.image_id

    def test_list_all_spatial_analyses_pagination(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        batch_spatial_analyses: List[ReceiptWordLabelSpatialAnalysis],
    ) -> None:
        """Test pagination through all spatial analyses."""
        client = DynamoClient(dynamodb_table)
        client.add_receipt_word_label_spatial_analyses(batch_spatial_analyses)

        # Get first page
        first_results, first_key = client.list_receipt_word_label_spatial_analyses(limit=2)
        assert len(first_results) == 2
        assert first_key is not None

        # Get remaining items
        remaining_results, _ = client.list_receipt_word_label_spatial_analyses(
            last_evaluated_key=first_key
        )
        assert len(remaining_results) == 3

        # Verify no overlap
        all_results = first_results + remaining_results
        assert len(all_results) == 5
        
        result_keys = {
            (r.image_id, r.receipt_id, r.line_id, r.word_id) 
            for r in all_results
        }
        expected_keys = {
            (a.image_id, a.receipt_id, a.line_id, a.word_id) 
            for a in batch_spatial_analyses
        }
        assert result_keys == expected_keys


# =============================================================================
# VALIDATION TESTS
# =============================================================================

class TestReceiptWordLabelSpatialAnalysisValidation:
    """Test validation for ReceiptWordLabelSpatialAnalysis operations."""

    def test_add_spatial_analysis_none_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding None raises OperationError."""
        client = DynamoClient(dynamodb_table)
        
        with pytest.raises(
            OperationError, match="Unexpected error during add_receipt_word_label_spatial_analysis: spatial_analysis cannot be None"
        ):
            client.add_receipt_word_label_spatial_analysis(None)  # type: ignore

    def test_add_spatial_analysis_wrong_type_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding wrong type raises OperationError."""
        client = DynamoClient(dynamodb_table)
        
        with pytest.raises(
            OperationError,
            match="Unexpected error during add_receipt_word_label_spatial_analysis: spatial_analysis must be an instance of ReceiptWordLabelSpatialAnalysis",
        ):
            client.add_receipt_word_label_spatial_analysis("not-an-analysis")  # type: ignore

    def test_get_spatial_analysis_invalid_params_raise_errors(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that get operations with invalid parameters raise errors."""
        client = DynamoClient(dynamodb_table)

        # Test None parameters
        with pytest.raises(EntityValidationError, match="image_id cannot be None"):
            client.get_receipt_word_label_spatial_analysis(
                image_id=None,  # type: ignore
                receipt_id=1,
                line_id=1,
                word_id=1,
            )

        with pytest.raises(EntityValidationError, match="receipt_id cannot be None"):
            client.get_receipt_word_label_spatial_analysis(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=None,  # type: ignore
                line_id=1,
                word_id=1,
            )

        # Test wrong types
        with pytest.raises(EntityValidationError, match="image_id must be a string"):
            client.get_receipt_word_label_spatial_analysis(
                image_id=123,  # type: ignore
                receipt_id=1,
                line_id=1,
                word_id=1,
            )

        with pytest.raises(EntityValidationError, match="receipt_id must be an integer"):
            client.get_receipt_word_label_spatial_analysis(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id="1",  # type: ignore
                line_id=1,
                word_id=1,
            )

        # Test negative values
        with pytest.raises(EntityValidationError, match="receipt_id must be positive"):
            client.get_receipt_word_label_spatial_analysis(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=-1,
                line_id=1,
                word_id=1,
            )

        with pytest.raises(EntityValidationError, match="line_id must be positive"):
            client.get_receipt_word_label_spatial_analysis(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                line_id=0,
                word_id=1,
            )

    def test_query_operations_validation(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test validation for query operations."""
        client = DynamoClient(dynamodb_table)

        # Test invalid label
        with pytest.raises(EntityValidationError, match="label must be a non-empty string"):
            client.get_spatial_analyses_by_label(label="")

        with pytest.raises(EntityValidationError, match="label must be a non-empty string"):
            client.get_spatial_analyses_by_label(label=None)  # type: ignore

        # Test invalid limit
        with pytest.raises(EntityValidationError, match="limit must be an integer"):
            client.list_spatial_analyses_for_receipt(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                limit="10",  # type: ignore
            )

        with pytest.raises(EntityValidationError, match="limit must be greater than 0"):
            client.list_spatial_analyses_for_receipt(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                limit=0,
            )


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

@pytest.mark.parametrize("error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS)
class TestReceiptWordLabelSpatialAnalysisAddErrorHandling:
    """Test error handling for add operations."""

    def test_add_spatial_analysis_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
        mocker: MockerFixture,
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB errors are properly handled in add operations."""
        client = DynamoClient(dynamodb_table)
        
        with patch.object(
            client._client, "put_item", side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem"
            )
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.add_receipt_word_label_spatial_analysis(sample_spatial_analysis)


@pytest.mark.parametrize("error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS)
class TestReceiptWordLabelSpatialAnalysisUpdateErrorHandling:
    """Test error handling for update operations."""

    def test_update_spatial_analysis_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        sample_spatial_analysis: ReceiptWordLabelSpatialAnalysis,
        mocker: MockerFixture,
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB errors are properly handled in update operations."""
        client = DynamoClient(dynamodb_table)
        
        with patch.object(
            client._client, "put_item", side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem"
            )
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.update_receipt_word_label_spatial_analysis(sample_spatial_analysis)


@pytest.mark.parametrize("error_code,expected_exception,error_match", QUERY_ERROR_SCENARIOS)
class TestReceiptWordLabelSpatialAnalysisQueryErrorHandling:
    """Test error handling for query operations."""

    def test_query_spatial_analyses_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker: MockerFixture,
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB errors are properly handled in query operations."""
        client = DynamoClient(dynamodb_table)
        
        with patch.object(
            client._client, "query", side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "Query"
            )
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.get_spatial_analyses_by_label("TAX")