"""Tests for the Places API functionality."""

import os

import pytest

from receipt_label.data.places_api import (
    BatchPlacesProcessor,
    ConfidenceLevel,
    PlacesAPI,
    ValidationResult,
)


@pytest.fixture
def dynamodb_table(dynamodb_table_and_s3_bucket, monkeypatch):
    """
    Set up environment variables and return the test table name.
    This ensures the DYNAMODB_TABLE_NAME is set before any imports happen.
    """
    table_name, _ = dynamodb_table_and_s3_bucket
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", table_name)
    monkeypatch.setenv("OPENAI_API_KEY", "test_api_key")
    monkeypatch.setenv("PINECONE_API_KEY", "test_pinecone_key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "test_index")
    monkeypatch.setenv("PINECONE_HOST", "test_host")
    return table_name


@pytest.fixture
def sample_receipt():
    """Create a sample receipt for testing."""
    return {
        "receipt_id": "test_receipt_1",
        "words": [
            {
                "text": "WALMART",  # Business name is in text but not in extracted_data
            },
            {
                "text": "123 Main St",
                "extracted_data": {"type": "address", "value": "123 Main St"},
            },
            {
                "text": "(555) 123-4567",
                "extracted_data": {"type": "phone", "value": "(555) 123-4567"},
            },
        ],
    }


@pytest.fixture
def sample_places_response():
    """Create a sample Places API response."""
    return {
        "name": "Walmart Supercenter",
        "formatted_address": "123 Main Street, City, State 12345",
        "formatted_phone_number": "(555) 123-4567",
        "website": "https://www.walmart.com",
        "rating": 3.5,
        "types": ["grocery_or_supermarket", "store"],
    }


@pytest.mark.integration
def test_places_api_initialization(dynamodb_table):
    """Test Places API client initialization."""
    api_key = "test_api_key"
    api = PlacesAPI(api_key)
    assert api is not None


@pytest.mark.integration
def test_batch_processor_initialization(mocker, dynamodb_table):
    """Test BatchPlacesProcessor initialization."""
    api_key = "test_api_key"
    mock_places_api = mocker.patch("receipt_label.data.places_api.PlacesAPI")
    processor = BatchPlacesProcessor(api_key, dynamodb_table)
    assert processor is not None


@pytest.mark.integration
def test_classify_receipt_data(sample_receipt, mocker, dynamodb_table):
    """Test receipt data classification."""
    mocker.patch("receipt_label.data.places_api.PlacesAPI")
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    available_data = processor._classify_receipt_data(sample_receipt)

    assert available_data["address"] == ["123 Main St"]
    assert available_data["phone"] == ["(555) 123-4567"]
    assert available_data["url"] == []
    assert available_data["date"] == []
    assert "name" not in available_data  # Verify name is not in available_data


@pytest.mark.integration
def test_process_high_priority_receipt(
    mocker, sample_receipt, sample_places_response, dynamodb_table
):
    """Test processing of high priority receipt."""
    mock_places_api = mocker.Mock()
    mock_places_api.search_by_address.return_value = sample_places_response
    mocker.patch(
        "receipt_label.data.places_api.PlacesAPI", return_value=mock_places_api
    )
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    result = processor._process_high_priority_receipt(
        sample_receipt,
        {
            "address": ["123 Main St"],
            "phone": ["(555) 123-4567"],
            "url": [],
            "date": [],
        },
    )

    assert isinstance(result, ValidationResult)
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.place_details == sample_places_response
    assert result.validation_score == 1.0
    assert not result.requires_manual_review
    mock_places_api.search_by_address.assert_called_once()


@pytest.mark.integration
def test_process_medium_priority_receipt(
    mocker, sample_receipt, sample_places_response, dynamodb_table
):
    """Test processing of medium priority receipt."""
    mock_places_api = mocker.Mock()
    mock_places_api.search_by_address.return_value = sample_places_response
    mocker.patch(
        "receipt_label.data.places_api.PlacesAPI", return_value=mock_places_api
    )
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    result = processor._process_medium_priority_receipt(
        sample_receipt,
        {
            "address": ["123 Main St"],
            "phone": [],
            "url": ["https://www.walmart.com"],
            "date": [],
        },
    )

    assert isinstance(result, ValidationResult)
    assert result.confidence == ConfidenceLevel.MEDIUM
    assert result.place_details == sample_places_response
    assert result.validation_score == 0.8
    assert not result.requires_manual_review
    mock_places_api.search_by_address.assert_called_once()


@pytest.mark.integration
def test_process_low_priority_receipt(mocker, sample_receipt, dynamodb_table):
    """Test processing of low priority receipt."""
    mocker.patch("receipt_label.data.places_api.PlacesAPI")
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    result = processor._process_low_priority_receipt(
        sample_receipt,
        {"address": ["123 Main St"], "phone": [], "url": [], "date": []},
    )

    assert isinstance(result, ValidationResult)
    assert result.confidence == ConfidenceLevel.LOW
    assert result.place_details == {}
    assert result.validation_score == 0.0
    assert result.requires_manual_review


@pytest.mark.integration
def test_process_no_data_receipt(mocker, sample_receipt, dynamodb_table):
    """Test processing of receipt with no data."""
    mocker.patch("receipt_label.data.places_api.PlacesAPI")
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    result = processor._process_no_data_receipt(sample_receipt)

    assert isinstance(result, ValidationResult)
    assert result.confidence == ConfidenceLevel.NONE
    assert result.place_details == {}
    assert result.validation_score == 0.0
    assert result.requires_manual_review


@pytest.mark.integration
def test_validate_business_name(mocker, dynamodb_table):
    """Test business name validation."""
    mocker.patch("receipt_label.data.places_api.PlacesAPI")
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)

    # Test exact match
    is_valid, message, score = processor._validate_business_name(
        "Walmart", "Walmart Supercenter"
    )
    assert is_valid
    assert message == ""
    assert score == 1.0

    # Test partial match
    is_valid, message, score = processor._validate_business_name(
        "Walmart", "Super Walmart"
    )
    assert is_valid
    assert message == ""
    assert score == 1.0

    # Test address-like name
    is_valid, message, score = processor._validate_business_name(
        "Walmart", "123 Main Street"
    )
    assert not is_valid
    assert "address" in message
    assert score == 0.5

    # Test no match
    is_valid, message, score = processor._validate_business_name(
        "Walmart", "Target"
    )
    assert not is_valid
    assert "mismatch" in message
    assert score == 0.7


@pytest.mark.integration
def test_process_receipt_batch(
    mocker, sample_receipt, sample_places_response, dynamodb_table
):
    """Test batch processing of receipts."""
    mock_places_api = mocker.Mock()
    mock_places_api.search_by_address.return_value = sample_places_response
    mocker.patch(
        "receipt_label.data.places_api.PlacesAPI", return_value=mock_places_api
    )
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    results = processor.process_receipt_batch([sample_receipt])

    assert len(results) == 1
    result = results[0]
    assert result["receipt_id"] == "test_receipt_1"
    assert result["places_api_match"] == sample_places_response
    assert result["confidence_level"] == "high"
    assert result["validation_score"] == 1.0
    assert not result["requires_manual_review"]
    mock_places_api.search_by_address.assert_called_once()


@pytest.mark.integration
def test_places_api_search_by_phone(mocker, dynamodb_table):
    """Test Places API phone search functionality."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "status": "OK",
        "candidates": [{"place_id": "test_place_id"}],
    }
    mock_response.raise_for_status.return_value = None

    mocker.patch("requests.get", return_value=mock_response)
    api = PlacesAPI("test_api_key")
    api.get_place_details = mocker.Mock(return_value={"name": "Test Place"})

    result = api.search_by_phone("(555) 123-4567")

    assert result == {"name": "Test Place"}
    api.get_place_details.assert_called_once_with("test_place_id")


@pytest.mark.integration
def test_process_receipt_batch_error(mocker, sample_receipt, dynamodb_table):
    """Test batch processing of receipts with error handling."""
    mock_places_api = mocker.Mock()
    mock_places_api.search_by_address.side_effect = Exception("Test error")
    mocker.patch(
        "receipt_label.data.places_api.PlacesAPI", return_value=mock_places_api
    )
    processor = BatchPlacesProcessor("test_api_key", dynamodb_table)
    results = processor.process_receipt_batch([sample_receipt])

    assert len(results) == 1
    result = results[0]
    assert result["receipt_id"] == "test_receipt_1"
    assert result["places_api_match"] is None
    assert result["confidence_level"] == "none"
    assert result["validation_score"] == 0.0
    assert result["requires_manual_review"]
    assert "processing_error" in result
    assert result["matched_fields"] == []
    mock_places_api.search_by_address.assert_called_once()
