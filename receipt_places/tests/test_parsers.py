"""Unit tests for API response parsers."""

# pylint: disable=redefined-outer-name,unused-variable

import pytest
from pydantic import ValidationError

from receipt_places.parsers import (
    APIError,
    ParseError,
    parse_place_autocomplete_response,
    parse_place_candidates_response,
    parse_place_details_response,
    parse_place_search_response,
)
from receipt_places.types import Geometry, LatLng, Place
from receipt_places.validators import DataQualityError, MissingExpectedFieldsError


# === FIXTURES ===


@pytest.fixture
def valid_details_response():
    """Valid places/details/json response."""
    return {
        "status": "OK",
        "result": {
            "place_id": "ChIJ1234567890",
            "name": "Test Restaurant",
            "formatted_address": "123 Main St, New York, NY",
            "geometry": {
                "location": {"lat": 40.7128, "lng": -74.0060},
            },
            "rating": 4.5,
            "user_ratings_total": 100,
            "types": ["restaurant"],
        },
    }


@pytest.fixture
def valid_candidates_response():
    """Valid places/findplacefromtext/json response."""
    return {
        "status": "OK",
        "candidates": [
            {
                "place_id": "ChIJabcdefg",
                "name": "Coffee Shop",
                "formatted_address": "456 Oak Ave, Boston, MA",
                "types": ["cafe"],
            }
        ],
    }


@pytest.fixture
def valid_search_response():
    """Valid textsearch/json response."""
    return {
        "status": "OK",
        "results": [
            {
                "place_id": "ChIJxyz123",
                "name": "Pizza Place",
                "formatted_address": "789 Pine St, Seattle, WA",
                "rating": 4.2,
            }
        ],
        "next_page_token": "token123",
    }


@pytest.fixture
def valid_autocomplete_response():
    """Valid places/autocomplete/json response."""
    return {
        "status": "OK",
        "predictions": [
            {
                "description": "123 Main St, New York, NY, USA",
                "place_id": "ChIJ1234567890",
            }
        ],
    }


# === PARSE_PLACE_DETAILS_RESPONSE ===


@pytest.mark.unit
def test_parse_details_response_valid(valid_details_response):
    """Test parsing valid details response."""
    place = parse_place_details_response(valid_details_response)
    assert place.place_id == "ChIJ1234567890"
    assert place.name == "Test Restaurant"
    assert place.rating == 4.5


@pytest.mark.unit
def test_parse_details_response_with_expected_fields(valid_details_response):
    """Test parsing with expected fields validation."""
    expected = {"place_id", "name", "geometry"}
    place = parse_place_details_response(
        valid_details_response, expected_fields=expected
    )
    assert place.place_id == "ChIJ1234567890"


@pytest.mark.unit
def test_parse_details_response_missing_expected_field(valid_details_response):
    """Test that missing expected fields raises error."""
    expected = {"place_id", "rating", "missing_field"}
    with pytest.raises(MissingExpectedFieldsError):
        parse_place_details_response(
            valid_details_response, expected_fields=expected
        )


@pytest.mark.unit
def test_parse_details_response_api_error_status():
    """Test parsing response with error status."""
    error_response = {
        "status": "REQUEST_DENIED",
        "error_message": "The API key is invalid",
    }
    with pytest.raises(APIError, match="REQUEST_DENIED"):
        parse_place_details_response(error_response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    ["ZERO_RESULTS", "OVER_QUERY_LIMIT", "INVALID_REQUEST", "UNKNOWN_ERROR"],
)
def test_parse_details_response_various_error_statuses(status):
    """Test parsing with various error statuses."""
    response = {"status": status}
    with pytest.raises(APIError):
        parse_place_details_response(response)


@pytest.mark.unit
def test_parse_details_response_missing_result():
    """Test parsing response with OK status but no result."""
    response = {"status": "OK", "result": None}
    with pytest.raises(APIError, match="no result dict"):
        parse_place_details_response(response)


@pytest.mark.unit
def test_parse_details_response_malformed_result():
    """Test parsing with malformed result data."""
    response = {
        "status": "OK",
        "result": {
            "place_id": "ChIJ123",
            "rating": "not-a-number",  # Invalid type
        },
    }
    with pytest.raises(ParseError, match="Failed to parse Place"):
        parse_place_details_response(response)


@pytest.mark.unit
def test_parse_details_response_malformed_envelope():
    """Test parsing with malformed response envelope."""
    response = {
        "status": "OK",
        "result": None,
    }
    # When result is None but status is OK, raises APIError
    with pytest.raises(APIError, match="no result dict"):
        parse_place_details_response(response)


@pytest.mark.unit
def test_parse_details_response_invalid_rating():
    """Test that invalid rating in response fails validation.

    Invalid rating is caught by Pydantic field validator during model_validate,
    so it raises ParseError (wrapping ValidationError).
    """
    response = {
        "status": "OK",
        "result": {
            "place_id": "ChIJ123",
            "name": "Bad Place",
            "rating": 10.0,  # Invalid: > 5.0
        },
    }
    with pytest.raises(ParseError, match="Failed to parse Place"):
        parse_place_details_response(response)


# === PARSE_PLACE_CANDIDATES_RESPONSE ===


@pytest.mark.unit
def test_parse_candidates_response_valid(valid_candidates_response):
    """Test parsing valid candidates response."""
    place = parse_place_candidates_response(valid_candidates_response)
    assert place is not None
    assert place.place_id == "ChIJabcdefg"
    assert place.name == "Coffee Shop"


@pytest.mark.unit
def test_parse_candidates_response_multiple_candidates():
    """Test parsing response with multiple candidates (returns first)."""
    response = {
        "status": "OK",
        "candidates": [
            {"place_id": "ChIJ1", "name": "First"},
            {"place_id": "ChIJ2", "name": "Second"},
        ],
    }
    place = parse_place_candidates_response(response)
    assert place.place_id == "ChIJ1"
    assert place.name == "First"


@pytest.mark.unit
def test_parse_candidates_response_no_candidates():
    """Test parsing response with no candidates."""
    response = {"status": "OK", "candidates": []}
    place = parse_place_candidates_response(response)
    assert place is None


@pytest.mark.unit
def test_parse_candidates_response_none_candidates():
    """Test parsing response with None candidates."""
    response = {"status": "OK", "candidates": None}
    place = parse_place_candidates_response(response)
    assert place is None


@pytest.mark.unit
def test_parse_candidates_response_api_error():
    """Test parsing candidates with error status."""
    response = {"status": "REQUEST_DENIED", "error_message": "Invalid API key"}
    with pytest.raises(APIError):
        parse_place_candidates_response(response)


@pytest.mark.unit
def test_parse_candidates_response_with_expected_fields(valid_candidates_response):
    """Test parsing candidates with expected fields validation."""
    expected = {"place_id", "name"}
    place = parse_place_candidates_response(
        valid_candidates_response, expected_fields=expected
    )
    assert place is not None


# === PARSE_PLACE_SEARCH_RESPONSE ===


@pytest.mark.unit
def test_parse_search_response_valid(valid_search_response):
    """Test parsing valid search response."""
    places = parse_place_search_response(valid_search_response)
    assert len(places) == 1
    assert places[0].place_id == "ChIJxyz123"
    assert places[0].name == "Pizza Place"


@pytest.mark.unit
def test_parse_search_response_multiple_results():
    """Test parsing response with multiple results."""
    response = {
        "status": "OK",
        "results": [
            {"place_id": "ChIJ1", "name": "First"},
            {"place_id": "ChIJ2", "name": "Second"},
        ],
    }
    places = parse_place_search_response(response)
    assert len(places) == 2
    assert places[0].place_id == "ChIJ1"
    assert places[1].place_id == "ChIJ2"


@pytest.mark.unit
def test_parse_search_response_no_results():
    """Test parsing response with no results."""
    response = {"status": "OK", "results": []}
    places = parse_place_search_response(response)
    assert places == []


@pytest.mark.unit
def test_parse_search_response_with_pagination():
    """Test parsing search response with next_page_token."""
    response = {
        "status": "OK",
        "results": [{"place_id": "ChIJ1", "name": "Place"}],
        "next_page_token": "token123",
    }
    places = parse_place_search_response(response)
    assert len(places) == 1
    assert places[0].place_id == "ChIJ1"


@pytest.mark.unit
def test_parse_search_response_api_error():
    """Test parsing search with error status."""
    response = {"status": "OVER_QUERY_LIMIT"}
    with pytest.raises(APIError):
        parse_place_search_response(response)


# === PARSE_PLACE_AUTOCOMPLETE_RESPONSE ===


@pytest.mark.unit
def test_parse_autocomplete_response_valid(valid_autocomplete_response):
    """Test parsing valid autocomplete response.

    Returns dict with description, place_id, and types (not Prediction objects).
    """
    predictions = parse_place_autocomplete_response(
        valid_autocomplete_response
    )
    assert len(predictions) == 1
    assert predictions[0]["place_id"] == "ChIJ1234567890"
    assert predictions[0]["description"] == "123 Main St, New York, NY, USA"


@pytest.mark.unit
def test_parse_autocomplete_response_multiple_predictions():
    """Test parsing response with multiple predictions."""
    response = {
        "status": "OK",
        "predictions": [
            {"description": "123 Main St", "place_id": "ChIJ1"},
            {"description": "123 Main Ave", "place_id": "ChIJ2"},
        ],
    }
    predictions = parse_place_autocomplete_response(response)
    assert len(predictions) == 2
    assert predictions[0]["description"] == "123 Main St"
    assert predictions[1]["description"] == "123 Main Ave"


@pytest.mark.unit
def test_parse_autocomplete_response_no_predictions():
    """Test parsing response with no predictions."""
    response = {"status": "OK", "predictions": []}
    predictions = parse_place_autocomplete_response(response)
    assert predictions == []


@pytest.mark.unit
def test_parse_autocomplete_response_api_error():
    """Test parsing autocomplete with error status."""
    response = {"status": "REQUEST_DENIED"}
    with pytest.raises(APIError):
        parse_place_autocomplete_response(response)


@pytest.mark.unit
def test_parse_autocomplete_response_without_description():
    """Test parsing predictions without description (should be skipped)."""
    response = {
        "status": "OK",
        "predictions": [
            # This one has no description, should be skipped
            {"place_id": "ChIJ1"},
            # This one has description, should be included
            {"description": "Valid Prediction", "place_id": "ChIJ2"},
        ],
    }
    predictions = parse_place_autocomplete_response(response)
    assert len(predictions) == 1
    assert predictions[0]["place_id"] == "ChIJ2"


# === ERROR HANDLING ===


@pytest.mark.unit
def test_parse_error_exception_type():
    """Test that ParseError is an Exception."""
    error = ParseError("test error")
    assert isinstance(error, Exception)


@pytest.mark.unit
def test_api_error_is_parse_error():
    """Test that APIError is a ParseError."""
    error = APIError("API error")
    assert isinstance(error, ParseError)


@pytest.mark.unit
def test_catch_all_parse_errors():
    """Test that all parsing errors can be caught with ParseError."""
    response_with_error = {"status": "REQUEST_DENIED"}

    with pytest.raises(ParseError):
        parse_place_details_response(response_with_error)

    with pytest.raises(ParseError):
        parse_place_candidates_response(response_with_error)

    with pytest.raises(ParseError):
        parse_place_search_response(response_with_error)

    with pytest.raises(ParseError):
        parse_place_autocomplete_response(response_with_error)
