"""Unit tests for Place data validation functions."""

# pylint: disable=redefined-outer-name,unused-variable

import pytest

from receipt_places.types import Geometry, LatLng, Place, Viewport
from receipt_places.validators import (
    DataQualityError,
    MissingExpectedFieldsError,
    PlacesValidationError,
    validate_has_identifier,
    validate_place_complete,
    validate_place_expected_fields,
    validate_place_sanity,
)


# === FIXTURES ===


@pytest.fixture
def valid_place():
    """Valid Place with all required fields."""
    return Place(
        place_id="ChIJ1234567890",
        name="Test Restaurant",
        rating=4.5,
        user_ratings_total=100,
        geometry=Geometry(
            location=LatLng(latitude=40.7128, longitude=-74.0060),
        ),
    )


@pytest.fixture
def minimal_place():
    """Minimal Place with just identifier."""
    return Place(place_id="ChIJ123", name="Test")


# === VALIDATE_PLACE_EXPECTED_FIELDS ===


@pytest.mark.unit
def test_validate_expected_fields_all_present(valid_place):
    """Test validation when all expected fields are present."""
    expected = {"place_id", "name", "rating"}
    # Should not raise
    validate_place_expected_fields(valid_place, expected)


@pytest.mark.unit
def test_validate_expected_fields_missing_one(minimal_place):
    """Test validation fails when expected field is missing."""
    expected = {"place_id", "rating"}  # rating is None
    with pytest.raises(MissingExpectedFieldsError):
        validate_place_expected_fields(minimal_place, expected)


@pytest.mark.unit
def test_validate_expected_fields_missing_multiple(minimal_place):
    """Test validation fails when multiple fields are missing."""
    expected = {"place_id", "rating", "user_ratings_total", "geometry"}
    with pytest.raises(MissingExpectedFieldsError) as exc_info:
        validate_place_expected_fields(minimal_place, expected)
    assert "Missing 3 expected fields" in str(exc_info.value)


@pytest.mark.unit
def test_validate_expected_fields_empty_set(minimal_place):
    """Test validation with empty expected fields set."""
    # Should not raise
    validate_place_expected_fields(minimal_place, set())


# === VALIDATE_PLACE_SANITY ===


@pytest.mark.unit
def test_validate_sanity_valid_place(valid_place):
    """Test sanity check passes for valid place."""
    # Should not raise
    validate_place_sanity(valid_place)


@pytest.mark.unit
@pytest.mark.parametrize("bad_rating", [-0.1, 5.1, 10.0])
def test_validate_sanity_invalid_rating(bad_rating):
    """Test sanity check fails for invalid rating.

    Note: Invalid ratings are caught by Pydantic field validator first,
    so this test verifies that pydantic catches them, not the sanity check.
    """
    with pytest.raises(Exception):  # ValidationError from Pydantic
        Place(place_id="123", rating=bad_rating)


@pytest.mark.unit
@pytest.mark.parametrize("bad_count", [-1, -100])
def test_validate_sanity_negative_ratings_count(bad_count):
    """Test sanity check fails for negative user_ratings_total.

    Note: Invalid counts are caught by Pydantic field validator first.
    """
    with pytest.raises(Exception):  # ValidationError from Pydantic
        Place(place_id="123", user_ratings_total=bad_count)


@pytest.mark.unit
@pytest.mark.parametrize("bad_lat", [-91.0, 91.0])
def test_validate_sanity_invalid_latitude(bad_lat):
    """Test sanity check fails for invalid latitude in geometry.

    Note: Invalid coordinates are caught by Pydantic field validator first.
    """
    with pytest.raises(Exception):  # ValidationError from Pydantic
        LatLng(latitude=bad_lat, longitude=0.0)


@pytest.mark.unit
@pytest.mark.parametrize("bad_lng", [-181.0, 181.0])
def test_validate_sanity_invalid_longitude(bad_lng):
    """Test sanity check fails for invalid longitude in geometry.

    Note: Invalid coordinates are caught by Pydantic field validator first.
    """
    with pytest.raises(Exception):  # ValidationError from Pydantic
        LatLng(latitude=0.0, longitude=bad_lng)


@pytest.mark.unit
def test_validate_sanity_viewport_invalid_southwest_northeast():
    """Test sanity check fails when viewport corners are illogical."""
    # Southwest latitude > Northeast latitude (invalid!)
    sw = LatLng(latitude=50.0, longitude=-74.0)  # Higher lat
    ne = LatLng(latitude=40.0, longitude=-70.0)  # Lower lat
    viewport = Viewport(southwest=sw, northeast=ne)
    geom = Geometry(viewport=viewport)
    place = Place(place_id="123", geometry=geom)

    with pytest.raises(
        DataQualityError, match="viewport southwest latitude"
    ):
        validate_place_sanity(place)


@pytest.mark.unit
def test_validate_sanity_viewport_invalid_longitude():
    """Test sanity check fails when viewport longitudes are illogical."""
    # Southwest longitude > Northeast longitude (invalid!)
    sw = LatLng(latitude=40.0, longitude=-70.0)  # Higher lng
    ne = LatLng(latitude=50.0, longitude=-74.0)  # Lower lng
    viewport = Viewport(southwest=sw, northeast=ne)
    geom = Geometry(viewport=viewport)
    place = Place(place_id="123", geometry=geom)

    with pytest.raises(
        DataQualityError, match="viewport southwest longitude"
    ):
        validate_place_sanity(place)


@pytest.mark.unit
def test_validate_sanity_valid_with_boundary_values():
    """Test sanity check passes for boundary values that are valid."""
    place = Place(
        place_id="123",
        rating=0.0,  # Valid boundary
        user_ratings_total=0,  # Valid boundary
    )
    # Should not raise
    validate_place_sanity(place)


@pytest.mark.unit
def test_validate_sanity_geometry_none():
    """Test sanity check handles None geometry gracefully."""
    place = Place(place_id="123", geometry=None)
    # Should not raise
    validate_place_sanity(place)


# === VALIDATE_HAS_IDENTIFIER ===


@pytest.mark.unit
def test_validate_has_identifier_place_id(valid_place):
    """Test validation passes when place_id exists."""
    # Should not raise
    validate_has_identifier(valid_place)


@pytest.mark.unit
def test_validate_has_identifier_name_only():
    """Test validation passes when name exists."""
    place = Place(name="Test Place")
    # Should not raise
    validate_has_identifier(place)


@pytest.mark.unit
def test_validate_has_identifier_missing():
    """Test validation fails when both place_id and name are missing."""
    place = Place(rating=4.5)
    with pytest.raises(
        DataQualityError, match="must have at least a place_id or name"
    ):
        validate_has_identifier(place)


@pytest.mark.unit
def test_validate_has_identifier_empty_strings():
    """Test validation with empty string identifiers (treated as missing)."""
    place = Place(place_id="", name="")
    # Empty strings are falsy, so both are considered missing
    with pytest.raises(DataQualityError):
        validate_has_identifier(place)


# === VALIDATE_PLACE_COMPLETE ===


@pytest.mark.unit
def test_validate_complete_valid(valid_place):
    """Test complete validation passes for valid place."""
    # Should not raise
    validate_place_complete(valid_place)


@pytest.mark.unit
def test_validate_complete_with_expected_fields(valid_place):
    """Test complete validation with expected fields enforcement."""
    expected = {"place_id", "name", "rating"}
    # Should not raise
    validate_place_complete(valid_place, expected_fields=expected)


@pytest.mark.unit
def test_validate_complete_fails_sanity():
    """Test complete validation fails sanity check for viewport logic."""
    # Create a place with illogical viewport (SW latitude > NE latitude)
    sw = LatLng(latitude=50.0, longitude=-74.0)
    ne = LatLng(latitude=40.0, longitude=-70.0)
    viewport = Viewport(southwest=sw, northeast=ne)
    geom = Geometry(viewport=viewport)
    place = Place(place_id="123", name="Bad Place", geometry=geom)

    with pytest.raises(DataQualityError, match="viewport"):
        validate_place_complete(place)


@pytest.mark.unit
def test_validate_complete_fails_identifier():
    """Test complete validation fails identifier check."""
    place = Place(rating=4.5)  # No place_id or name
    with pytest.raises(DataQualityError, match="place_id or name"):
        validate_place_complete(place)


@pytest.mark.unit
def test_validate_complete_fails_expected_fields(minimal_place):
    """Test complete validation fails expected fields check."""
    expected = {"place_id", "rating"}  # rating is missing
    with pytest.raises(MissingExpectedFieldsError):
        validate_place_complete(minimal_place, expected_fields=expected)


@pytest.mark.unit
def test_validate_complete_without_expected_fields(minimal_place):
    """Test complete validation without expected fields only checks sanity and identifier."""
    # minimal_place has place_id and name, and no invalid values
    # Should not raise (skips field enforcement)
    validate_place_complete(minimal_place)


# === EXCEPTION HIERARCHY ===


@pytest.mark.unit
def test_validation_error_hierarchy():
    """Test that validation errors are properly hierarchized."""
    assert issubclass(MissingExpectedFieldsError, PlacesValidationError)
    assert issubclass(DataQualityError, PlacesValidationError)
    assert issubclass(PlacesValidationError, ValueError)


@pytest.mark.unit
def test_catch_all_places_validation_errors():
    """Test that all validation errors can be caught with PlacesValidationError."""
    place_invalid_id = Place(rating=4.5)  # Missing identifier

    with pytest.raises(PlacesValidationError):
        validate_place_complete(place_invalid_id)

    # Test with bad viewport (sanity check error)
    sw = LatLng(latitude=50.0, longitude=-74.0)
    ne = LatLng(latitude=40.0, longitude=-70.0)
    viewport = Viewport(southwest=sw, northeast=ne)
    geom = Geometry(viewport=viewport)
    place_invalid_geom = Place(place_id="123", geometry=geom)

    with pytest.raises(PlacesValidationError):
        validate_place_complete(place_invalid_geom)

    place_missing_fields = Place(place_id="123")

    with pytest.raises(PlacesValidationError):
        validate_place_complete(
            place_missing_fields, expected_fields={"rating"}
        )
