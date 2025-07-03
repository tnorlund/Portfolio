from datetime import datetime
from decimal import Decimal
from typing import Dict, List

import pytest
from receipt_label.models.validation import (
    FieldValidation,
    ValidationAnalysis,
    ValidationResult,
    ValidationResultType,
    ValidationStatus,
)

from receipt_dynamo import (
    ReceiptValidationCategory,
    ReceiptValidationResult,
    ReceiptValidationSummary,
)


@pytest.fixture
def sample_validation_result() -> ValidationResult:
    """Create a sample ValidationResult for testing."""
    return ValidationResult(
        type=ValidationResultType.ERROR,
        message="Invalid business name",
        reasoning="Expected 'Store A' but found 'Store B'",
        field="business_name",
        expected_value="Store A",
        actual_value="Store B",
        metadata={"confidence": 0.9},
    )


@pytest.fixture
def sample_field_validation(sample_validation_result) -> FieldValidation:
    """Create a sample FieldValidation for testing."""
    return FieldValidation(
        field_category="Business Identity",
        results=[sample_validation_result],
        status=ValidationStatus.INVALID,
        reasoning="Business identity validation failed with 1 error",
        metadata={"field_key": "business_identity"},
    )


@pytest.fixture
def sample_validation_analysis(sample_field_validation) -> ValidationAnalysis:
    """Create a sample ValidationAnalysis for testing."""
    analysis = ValidationAnalysis(
        business_identity=sample_field_validation,
        overall_status=ValidationStatus.INVALID,
        overall_reasoning="Validation failed with 1 error in business identity",
        validation_timestamp=datetime(2023, 3, 15, 12, 0, 0),
        receipt_id=123,
        image_id="550e8400-e29b-41d4-a716-446655440000",
        metadata={
            "version": "1.0.0",
            "processing_metrics": {"processing_time_ms": 150, "api_calls": 2},
            "source_information": {
                "model_name": "gpt-4",
                "model_version": "0.5.0",
            },
        },
    )

    # Add a warning to another field
    warning_result = ValidationResult(
        type=ValidationResultType.WARNING,
        message="Address requires review",
        reasoning="Address format is non-standard",
        field="address",
        expected_value="Standard format",
        actual_value="Non-standard format",
    )
    analysis.address_verification.add_result(warning_result)

    return analysis


@pytest.fixture
def sample_validation_summary() -> ReceiptValidationSummary:
    """Create a sample ReceiptValidationSummary for testing from_dynamo_items."""
    return ReceiptValidationSummary(
        receipt_id=123,
        image_id="550e8400-e29b-41d4-a716-446655440000",
        overall_status="invalid",
        overall_reasoning="Validation failed with 1 error in business identity",
        field_summary={
            "business_identity": {
                "status": "invalid",
                "count": 1,
                "has_errors": True,
                "has_warnings": False,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
                "success_count": 0,
            },
            "address_verification": {
                "status": "needs_review",
                "count": 1,
                "has_errors": False,
                "has_warnings": True,
                "error_count": 0,
                "warning_count": 1,
                "info_count": 0,
                "success_count": 0,
            },
        },
        validation_timestamp="2023-03-15T12:00:00",
        version="1.0.0",
        metadata={
            "processing_metrics": {"processing_time_ms": 150, "api_calls": 2},
            "source_information": {
                "model_name": "gpt-4",
                "model_version": "0.5.0",
            },
        },
        timestamp_added=datetime(2023, 3, 15, 12, 0, 0),
        timestamp_updated=datetime(2023, 3, 15, 13, 0, 0),
    )


@pytest.fixture
def sample_validation_categories() -> List[ReceiptValidationCategory]:
    """Create sample ReceiptValidationCategory objects for testing from_dynamo_items."""
    return [
        ReceiptValidationCategory(
            receipt_id=123,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            field_name="business_identity",
            field_category="Business Identity",
            status="invalid",
            reasoning="Business identity validation failed with 1 error",
            result_summary={
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
                "success_count": 0,
                "total_count": 1,
            },
            validation_timestamp="2023-03-15T12:00:00",
            metadata={
                "processing_metrics": {
                    "processing_time_ms": 150,
                    "api_calls": 2,
                }
            },
        ),
        ReceiptValidationCategory(
            receipt_id=123,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            field_name="address_verification",
            field_category="Address Verification",
            status="needs_review",
            reasoning="Address verification produced warnings",
            result_summary={
                "error_count": 0,
                "warning_count": 1,
                "info_count": 0,
                "success_count": 0,
                "total_count": 1,
            },
            validation_timestamp="2023-03-15T12:00:00",
            metadata={
                "processing_metrics": {
                    "processing_time_ms": 150,
                    "api_calls": 2,
                }
            },
        ),
    ]


@pytest.fixture
def sample_validation_results() -> List[ReceiptValidationResult]:
    """Create sample ReceiptValidationResult objects for testing from_dynamo_items."""
    return [
        ReceiptValidationResult(
            receipt_id=123,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            field_name="business_identity",
            result_index=0,
            type="error",
            message="Invalid business name",
            reasoning="Expected 'Store A' but found 'Store B'",
            field="business_name",
            expected_value="Store A",
            actual_value="Store B",
            validation_timestamp="2023-03-15T12:00:00",
            metadata={"confidence": 0.9},
        ),
        ReceiptValidationResult(
            receipt_id=123,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            field_name="address_verification",
            result_index=0,
            type="warning",
            message="Address requires review",
            reasoning="Address format is non-standard",
            field="address",
            expected_value="Standard format",
            actual_value="Non-standard format",
            validation_timestamp="2023-03-15T12:00:00",
            metadata={},
        ),
    ]


@pytest.mark.unit
class TestValidationAnalysisSerialization:
    """Tests for ValidationAnalysis serialization/deserialization methods."""

    def test_to_dynamo_validation_summary(self, sample_validation_analysis):
        """Test conversion to ReceiptValidationSummary."""
        summary = sample_validation_analysis.to_dynamo_validation_summary()

        # Check that we got the right type of object
        assert isinstance(summary, ReceiptValidationSummary)

        # Check basic fields
        assert summary.receipt_id == 123
        assert summary.image_id == "550e8400-e29b-41d4-a716-446655440000"
        assert summary.overall_status == "invalid"
        assert (
            summary.overall_reasoning
            == "Validation failed with 1 error in business identity"
        )
        assert summary.validation_timestamp == "2023-03-15T12:00:00"
        assert summary.version == "1.0.0"

        # Check field summary
        assert "business_identity" in summary.field_summary
        assert "address_verification" in summary.field_summary
        assert (
            summary.field_summary["business_identity"]["status"] == "invalid"
        )
        assert (
            bool(summary.field_summary["business_identity"]["has_errors"])
            is True
        )
        assert (
            bool(summary.field_summary["address_verification"]["has_warnings"])
            is True
        )

        # Check metadata
        assert "processing_metrics" in summary.metadata
        assert (
            summary.metadata["processing_metrics"]["processing_time_ms"] == 150
        )
        assert "source_information" in summary.metadata
        assert summary.metadata["source_information"]["model_name"] == "gpt-4"

    def test_to_dynamo_validation_categories(self, sample_validation_analysis):
        """Test conversion to ReceiptValidationCategory objects."""
        categories = (
            sample_validation_analysis.to_dynamo_validation_categories()
        )

        # Check that we got a list of the right type of objects
        assert isinstance(categories, list)
        assert len(categories) == 6  # One for each field
        assert all(
            isinstance(cat, ReceiptValidationCategory) for cat in categories
        )

        # Find the business_identity category
        business_identity_cat = next(
            cat for cat in categories if cat.field_name == "business_identity"
        )

        # Check category fields
        assert business_identity_cat.receipt_id == 123
        assert (
            business_identity_cat.image_id
            == "550e8400-e29b-41d4-a716-446655440000"
        )
        assert business_identity_cat.field_category == "Business Identity"
        assert business_identity_cat.status == "invalid"
        assert "failed with 1 error" in business_identity_cat.reasoning

        # Check result summary
        assert business_identity_cat.result_summary["error_count"] == 1
        assert business_identity_cat.result_summary["warning_count"] == 0
        assert business_identity_cat.result_summary["total_count"] == 1

        # Find the address_verification category
        address_cat = next(
            cat
            for cat in categories
            if cat.field_name == "address_verification"
        )

        # Check that it has the warning correctly reflected
        assert address_cat.status == "needs_review"
        assert address_cat.result_summary["warning_count"] == 1

    def test_to_dynamo_validation_results(self, sample_validation_analysis):
        """Test conversion to ReceiptValidationResult objects."""
        results = sample_validation_analysis.to_dynamo_validation_results()

        # Check that we got a list of the right type of objects
        assert isinstance(results, list)
        assert len(results) == 2  # One error and one warning
        assert all(isinstance(res, ReceiptValidationResult) for res in results)

        # Find the business_identity result
        business_result = next(
            res for res in results if res.field_name == "business_identity"
        )

        # Check result fields
        assert business_result.receipt_id == 123
        assert (
            business_result.image_id == "550e8400-e29b-41d4-a716-446655440000"
        )
        assert business_result.type == "error"
        assert business_result.message == "Invalid business name"
        assert (
            business_result.reasoning
            == "Expected 'Store A' but found 'Store B'"
        )
        assert business_result.field == "business_name"
        assert business_result.expected_value == "Store A"
        assert business_result.actual_value == "Store B"
        assert business_result.metadata["confidence"] == 0.9

        # Find the address_verification result
        address_result = next(
            res for res in results if res.field_name == "address_verification"
        )

        # Check that it has the warning correctly reflected
        assert address_result.type == "warning"
        assert address_result.message == "Address requires review"
        assert address_result.field == "address"

    def test_from_dynamo_items(
        self,
        sample_validation_summary,
        sample_validation_categories,
        sample_validation_results,
    ):
        """Test reconstruction from DynamoDB items."""
        analysis = ValidationAnalysis.from_dynamo_items(
            sample_validation_summary,
            sample_validation_categories,
            sample_validation_results,
        )

        # Check that we got the right type of object
        assert isinstance(analysis, ValidationAnalysis)

        # Check basic fields
        assert analysis.receipt_id == 123
        assert analysis.image_id == "550e8400-e29b-41d4-a716-446655440000"
        assert analysis.overall_status == ValidationStatus.INVALID
        assert (
            analysis.overall_reasoning
            == "Validation failed with 1 error in business identity"
        )

        # Check validation timestamp
        assert isinstance(analysis.validation_timestamp, datetime)
        assert (
            analysis.validation_timestamp.isoformat() == "2023-03-15T12:00:00"
        )

        # Check business_identity field validation
        assert analysis.business_identity.field_category == "Business Identity"
        assert analysis.business_identity.status == ValidationStatus.INVALID
        assert len(analysis.business_identity.results) == 1

        # Check business_identity result
        result = analysis.business_identity.results[0]
        assert result.type == ValidationResultType.ERROR
        assert result.message == "Invalid business name"
        assert result.reasoning == "Expected 'Store A' but found 'Store B'"
        assert result.field == "business_name"
        assert result.expected_value == "Store A"
        assert result.actual_value == "Store B"
        assert result.metadata["confidence"] == 0.9

        # Check address_verification field validation
        assert (
            analysis.address_verification.field_category
            == "Address Verification"
        )
        assert (
            analysis.address_verification.status
            == ValidationStatus.NEEDS_REVIEW
        )
        assert len(analysis.address_verification.results) == 1

        # Check address_verification result
        result = analysis.address_verification.results[0]
        assert result.type == ValidationResultType.WARNING
        assert result.message == "Address requires review"
        assert result.reasoning == "Address format is non-standard"
        assert result.field == "address"
        assert result.expected_value == "Standard format"
        assert result.actual_value == "Non-standard format"

        # Check metadata
        assert "processing_metrics" in analysis.metadata
        assert (
            analysis.metadata["processing_metrics"]["processing_time_ms"]
            == 150
        )
        assert "source_information" in analysis.metadata
        assert analysis.metadata["source_information"]["model_name"] == "gpt-4"

    def test_roundtrip_conversion(
        self,
        sample_validation_analysis,
        sample_validation_summary,
        sample_validation_categories,
        sample_validation_results,
    ):
        """Test roundtrip conversion from ValidationAnalysis to DynamoDB items and back."""
        # Convert to DynamoDB objects
        summary = sample_validation_analysis.to_dynamo_validation_summary()
        categories = (
            sample_validation_analysis.to_dynamo_validation_categories()
        )
        results = sample_validation_analysis.to_dynamo_validation_results()

        # Convert back to ValidationAnalysis
        reconstructed = ValidationAnalysis.from_dynamo_items(
            summary, categories, results
        )

        # Check that essential properties match
        assert (
            reconstructed.receipt_id == sample_validation_analysis.receipt_id
        )
        assert reconstructed.image_id == sample_validation_analysis.image_id
        assert (
            reconstructed.overall_status
            == sample_validation_analysis.overall_status
        )
        assert (
            reconstructed.overall_reasoning
            == sample_validation_analysis.overall_reasoning
        )

        # Check that ValidationResult objects were properly reconstructed
        assert len(reconstructed.business_identity.results) == len(
            sample_validation_analysis.business_identity.results
        )
        original_result = sample_validation_analysis.business_identity.results[
            0
        ]
        reconstructed_result = reconstructed.business_identity.results[0]

        assert reconstructed_result.type == original_result.type
        assert reconstructed_result.message == original_result.message
        assert reconstructed_result.reasoning == original_result.reasoning
        assert reconstructed_result.field == original_result.field
        assert (
            reconstructed_result.expected_value
            == original_result.expected_value
        )
        assert (
            reconstructed_result.actual_value == original_result.actual_value
        )

        # Check that metadata was preserved
        assert "version" in reconstructed.metadata
        assert (
            reconstructed.metadata["version"]
            == sample_validation_analysis.metadata["version"]
        )
        assert "processing_metrics" in reconstructed.metadata
        assert (
            reconstructed.metadata["processing_metrics"]["processing_time_ms"]
            == sample_validation_analysis.metadata["processing_metrics"][
                "processing_time_ms"
            ]
        )

    def test_to_dynamo_validation_summary_missing_fields(self):
        """Test that to_dynamo_validation_summary raises ValueError when required fields are missing."""
        analysis = ValidationAnalysis()

        # Missing both image_id and receipt_id
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_summary()

        # Missing image_id
        analysis.receipt_id = 123
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_summary()

        # Missing receipt_id
        analysis = ValidationAnalysis()
        analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_summary()

    def test_to_dynamo_validation_categories_missing_fields(self):
        """Test that to_dynamo_validation_categories raises ValueError when required fields are missing."""
        analysis = ValidationAnalysis()

        # Missing both image_id and receipt_id
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_categories()

        # Missing image_id
        analysis.receipt_id = 123
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_categories()

        # Missing receipt_id
        analysis = ValidationAnalysis()
        analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_categories()

    def test_to_dynamo_validation_results_missing_fields(self):
        """Test that to_dynamo_validation_results raises ValueError when required fields are missing."""
        analysis = ValidationAnalysis()

        # Missing both image_id and receipt_id
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_results()

        # Missing image_id
        analysis.receipt_id = 123
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_results()

        # Missing receipt_id
        analysis = ValidationAnalysis()
        analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
        with pytest.raises(
            ValueError, match="receipt_id and image_id must be set"
        ):
            analysis.to_dynamo_validation_results()
