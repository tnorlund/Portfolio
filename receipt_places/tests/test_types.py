"""Unit tests for receipt_places type models and field validators."""

# pylint: disable=redefined-outer-name,unused-variable

import pytest
from pydantic import ValidationError

from receipt_places.types import (
    Candidate,
    Geometry,
    LatLng,
    LegacyAutocompleteResponse,
    LegacyCandidatesResponse,
    LegacyDetailsResponse,
    LegacySearchResponse,
    OpeningHours,
    Photo,
    Place,
    PlusCode,
    Prediction,
    Viewport,
)


# === FIXTURES ===


@pytest.fixture
def example_latlng():
    """Valid geographic coordinate pair."""
    return LatLng(latitude=40.7128, longitude=-74.0060)


@pytest.fixture
def example_place():
    """Valid Place object with common fields."""
    return Place(
        place_id="ChIJ1234567890",
        name="Test Restaurant",
        formatted_address="123 Main St, New York, NY 10001",
        geometry=Geometry(
            location=LatLng(latitude=40.7128, longitude=-74.0060),
        ),
        types=["restaurant", "point_of_interest"],
        rating=4.5,
        user_ratings_total=100,
        business_status="OPERATIONAL",
    )


@pytest.fixture
def example_candidate():
    """Valid Candidate object."""
    return Candidate(
        place_id="ChIJabcdefg",
        name="Coffee Shop",
        formatted_address="456 Oak Ave, Boston, MA",
        types=["cafe"],
        business_status="OPERATIONAL",
    )


@pytest.fixture
def example_legacy_details_response():
    """Valid places/details/json response."""
    return {
        "status": "OK",
        "result": {
            "place_id": "ChIJ1234567890",
            "name": "Test Place",
            "formatted_address": "123 Main St",
            "rating": 4.5,
            "user_ratings_total": 100,
        },
    }


# === LATLNG VALIDATION ===


@pytest.mark.unit
def test_latlng_valid(example_latlng):
    """Test valid latitude/longitude coordinates."""
    assert example_latlng.latitude == 40.7128
    assert example_latlng.longitude == -74.0060


@pytest.mark.unit
@pytest.mark.parametrize("bad_lat", [-91.0, 91.0, 100.0])
def test_latlng_invalid_latitude(bad_lat):
    """Test that invalid latitude raises ValueError."""
    with pytest.raises(
        ValidationError, match="latitude out of range"
    ):
        LatLng(latitude=bad_lat, longitude=0.0)


@pytest.mark.unit
@pytest.mark.parametrize("bad_lng", [-181.0, 181.0, 200.0])
def test_latlng_invalid_longitude(bad_lng):
    """Test that invalid longitude raises ValueError."""
    with pytest.raises(
        ValidationError, match="longitude out of range"
    ):
        LatLng(latitude=0.0, longitude=bad_lng)


@pytest.mark.unit
def test_latlng_boundary_values():
    """Test boundary values for lat/lng."""
    # Valid extremes
    lat_min = LatLng(latitude=-90.0, longitude=0.0)
    assert lat_min.latitude == -90.0

    lat_max = LatLng(latitude=90.0, longitude=0.0)
    assert lat_max.latitude == 90.0

    lng_min = LatLng(latitude=0.0, longitude=-180.0)
    assert lng_min.longitude == -180.0

    lng_max = LatLng(latitude=0.0, longitude=180.0)
    assert lng_max.longitude == 180.0


# === PLACE VALIDATION ===


@pytest.mark.unit
def test_place_valid(example_place):
    """Test valid Place object."""
    assert example_place.place_id == "ChIJ1234567890"
    assert example_place.name == "Test Restaurant"
    assert example_place.rating == 4.5
    assert example_place.user_ratings_total == 100


@pytest.mark.unit
@pytest.mark.parametrize("bad_rating", [-0.1, 5.1, 10.0])
def test_place_invalid_rating(bad_rating):
    """Test that invalid rating raises ValueError."""
    with pytest.raises(ValidationError, match="rating out of range"):
        Place(rating=bad_rating)


@pytest.mark.unit
@pytest.mark.parametrize("bad_count", [-1, -100])
def test_place_invalid_user_ratings_total(bad_count):
    """Test that negative user_ratings_total raises ValueError."""
    with pytest.raises(
        ValidationError, match="user_ratings_total must be >= 0"
    ):
        Place(user_ratings_total=bad_count)


@pytest.mark.unit
def test_place_rating_boundary_values():
    """Test boundary values for rating."""
    # Valid boundaries
    place_min = Place(rating=0.0)
    assert place_min.rating == 0.0

    place_max = Place(rating=5.0)
    assert place_max.rating == 5.0

    # None is allowed
    place_none = Place(rating=None)
    assert place_none.rating is None


@pytest.mark.unit
def test_place_user_ratings_total_boundary():
    """Test boundary values for user_ratings_total."""
    place_zero = Place(user_ratings_total=0)
    assert place_zero.user_ratings_total == 0

    place_large = Place(user_ratings_total=1000000)
    assert place_large.user_ratings_total == 1000000

    place_none = Place(user_ratings_total=None)
    assert place_none.user_ratings_total is None


# === GEOMETRY VALIDATION ===


@pytest.mark.unit
def test_geometry_with_location(example_latlng):
    """Test Geometry with location."""
    geom = Geometry(location=example_latlng)
    assert geom.location == example_latlng


@pytest.mark.unit
def test_geometry_with_viewport():
    """Test Geometry with viewport."""
    sw = LatLng(latitude=40.7, longitude=-74.1)
    ne = LatLng(latitude=40.8, longitude=-73.9)
    viewport = Viewport(southwest=sw, northeast=ne)
    geom = Geometry(viewport=viewport)
    assert geom.viewport == viewport


# === OPTIONAL FIELDS ===


@pytest.mark.unit
def test_place_optional_fields():
    """Test that Place fields are truly optional."""
    # Minimal place
    minimal_place = Place()
    assert minimal_place.place_id is None
    assert minimal_place.name is None
    assert minimal_place.rating is None
    assert minimal_place.geometry is None


@pytest.mark.unit
def test_opening_hours_optional_fields():
    """Test that OpeningHours fields are optional."""
    empty_hours = OpeningHours()
    assert empty_hours.open_now is None
    assert empty_hours.periods is None
    assert empty_hours.weekday_text is None


# === CANDIDATE VALIDATION ===


@pytest.mark.unit
def test_candidate_valid(example_candidate):
    """Test valid Candidate object."""
    assert example_candidate.place_id == "ChIJabcdefg"
    assert example_candidate.name == "Coffee Shop"
    assert example_candidate.types == ["cafe"]


# === PREDICTION VALIDATION ===


@pytest.mark.unit
def test_prediction_valid():
    """Test valid Prediction object."""
    pred = Prediction(
        description="123 Main St, New York, NY, USA",
        place_id="ChIJ1234567890",
    )
    assert pred.description == "123 Main St, New York, NY, USA"
    assert pred.place_id == "ChIJ1234567890"


# === LEGACY RESPONSE TYPES ===


@pytest.mark.unit
def test_legacy_details_response_valid(example_legacy_details_response):
    """Test valid LegacyDetailsResponse."""
    resp = LegacyDetailsResponse.model_validate(
        example_legacy_details_response
    )
    assert resp.status == "OK"
    assert resp.result is not None


@pytest.mark.unit
def test_legacy_details_response_with_error():
    """Test LegacyDetailsResponse with error."""
    payload = {
        "status": "REQUEST_DENIED",
        "error_message": "The API key is invalid",
    }
    resp = LegacyDetailsResponse.model_validate(payload)
    assert resp.status == "REQUEST_DENIED"
    assert resp.error_message == "The API key is invalid"


@pytest.mark.unit
def test_legacy_candidates_response_valid():
    """Test valid LegacyCandidatesResponse."""
    payload = {
        "status": "OK",
        "candidates": [
            {"place_id": "ChIJ1", "name": "Place 1"},
            {"place_id": "ChIJ2", "name": "Place 2"},
        ],
    }
    resp = LegacyCandidatesResponse.model_validate(payload)
    assert resp.status == "OK"
    assert len(resp.candidates) == 2


@pytest.mark.unit
def test_legacy_search_response_valid():
    """Test valid LegacySearchResponse."""
    payload = {
        "status": "OK",
        "results": [
            {"place_id": "ChIJ1", "name": "Result 1"},
        ],
        "next_page_token": "token123",
    }
    resp = LegacySearchResponse.model_validate(payload)
    assert resp.status == "OK"
    assert len(resp.results) == 1
    assert resp.next_page_token == "token123"


@pytest.mark.unit
def test_legacy_autocomplete_response_valid():
    """Test valid LegacyAutocompleteResponse."""
    payload = {
        "status": "OK",
        "predictions": [
            {
                "description": "123 Main St",
                "place_id": "ChIJ1",
            },
        ],
    }
    resp = LegacyAutocompleteResponse.model_validate(payload)
    assert resp.status == "OK"
    assert len(resp.predictions) == 1


# === PYDANTIC CONFIG ===


@pytest.mark.unit
def test_place_populate_by_name():
    """Test that Place accepts both field names and aliases."""
    # Using field names (should work)
    place1 = Place.model_validate({"place_id": "ChIJ123"})
    assert place1.place_id == "ChIJ123"

    # Minimal valid model
    assert place1.name is None
    assert place1.rating is None
