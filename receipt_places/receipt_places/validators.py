"""
Data quality validators for Google Places API responses.

These validators enforce that:
1. You got the fields you requested (field mask enforcement)
2. Field values make sense (sanity checks)
3. Critical identifiers exist
"""

import logging

from receipt_places.types import Place

logger = logging.getLogger(__name__)


class PlacesValidationError(ValueError):
    """Base error for Places data validation failures."""

    pass


class MissingExpectedFieldsError(PlacesValidationError):
    """Raised when expected fields from field mask are missing."""

    pass


class DataQualityError(PlacesValidationError):
    """Raised when data values fail sanity checks."""

    pass


def validate_place_expected_fields(
    place: Place, expected_fields: set[str]
) -> None:
    """
    Field mask enforcement: ensure all requested fields exist in the Place object.

    This is your primary data quality gate. If you requested a field via the
    API's "fields" parameter, it should exist in the response (barring API errors).

    Args:
        place: The parsed Place object
        expected_fields: Set of field names you requested (e.g. {'place_id', 'geometry', 'rating'})

    Raises:
        MissingExpectedFieldsError: If any expected field is None/missing
    """
    missing = []
    for field_name in expected_fields:
        if getattr(place, field_name, None) is None:
            missing.append(field_name)

    if missing:
        raise MissingExpectedFieldsError(
            f"Missing {len(missing)} expected fields from Places API response: {missing}. "
            f"This usually means the API didn't return what you requested, or the wrapper "
            f"didn't parse it correctly."
        )


def validate_place_sanity(place: Place) -> None:
    """
    Value sanity checks: ensure numeric fields and relationships make sense.

    Args:
        place: The Place object to validate

    Raises:
        DataQualityError: If any value fails sanity checks
    """
    errors = []

    # Rating sanity
    if place.rating is not None and not (0.0 <= place.rating <= 5.0):
        errors.append(f"rating {place.rating} is outside [0.0, 5.0] range")

    # User ratings count sanity
    if place.user_ratings_total is not None and place.user_ratings_total < 0:
        errors.append(
            f"user_ratings_total {place.user_ratings_total} is negative"
        )

    # Geospatial sanity
    if place.geometry and place.geometry.location:
        loc = place.geometry.location
        if not (-90.0 <= loc.latitude <= 90.0):
            errors.append(
                f"latitude {loc.latitude} is outside [-90, 90] range"
            )
        if not (-180.0 <= loc.longitude <= 180.0):
            errors.append(
                f"longitude {loc.longitude} is outside [-180, 180] range"
            )

    # Viewport sanity (if present, corners should be logical)
    if place.geometry and place.geometry.viewport:
        vp = place.geometry.viewport
        if vp.southwest and vp.northeast:
            sw = vp.southwest
            ne = vp.northeast
            if sw.latitude > ne.latitude:
                errors.append(
                    f"viewport southwest latitude ({sw.latitude}) > "
                    f"northeast latitude ({ne.latitude})"
                )
            if sw.longitude > ne.longitude:
                errors.append(
                    f"viewport southwest longitude ({sw.longitude}) > "
                    f"northeast longitude ({ne.longitude})"
                )

    if errors:
        error_msg = "; ".join(errors)
        raise DataQualityError(
            f"Place object failed sanity checks: {error_msg}"
        )


def validate_has_identifier(place: Place) -> None:
    """
    Critical identifier check: ensure the place has at least a place_id or name.

    Without an identifier, you can't reliably refer to the place in subsequent calls.

    Args:
        place: The Place object to validate

    Raises:
        DataQualityError: If both place_id and name are missing
    """
    if not place.place_id and not place.name:
        raise DataQualityError("Place must have at least a place_id or name")


def validate_place_complete(
    place: Place,
    expected_fields: set[str] | None = None,
) -> None:
    """
    Run all validators in sequence.

    This is your "quality gate" function - call this before returning a Place
    to your application code.

    Args:
        place: The Place object to validate
        expected_fields: Optional set of fields you requested. If provided,
                        will enforce they all exist.

    Raises:
        PlacesValidationError: If any validation fails
    """
    # Sanity checks always run
    validate_place_sanity(place)

    # Identifier check always runs
    validate_has_identifier(place)

    # Field mask enforcement (if you specify what you requested)
    if expected_fields:
        validate_place_expected_fields(place, expected_fields)
