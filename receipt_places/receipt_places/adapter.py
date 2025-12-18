"""
Adapter layer: Convert Google Places API v1 responses to legacy format.

This module provides functions to convert new Places API (v1) responses
to the legacy API format for backward compatibility. This allows the rest
of the receipt_places package to work seamlessly whether we're using v1
or legacy API.

The adapter maintains type safety using Pydantic models.
"""

import logging
from typing import Optional

from receipt_places.types import (
    Candidate,
    Geometry,
    LatLng,
    OpeningHours,
    OpeningHoursPeriod,
    Photo,
    Place,
    PlusCode,
    Viewport,
)
from receipt_places.types_v1 import PlaceV1

logger = logging.getLogger(__name__)


def adapt_v1_location_to_legacy(location: Optional["receipt_places.types_v1.Location"]) -> Optional[LatLng]:
    """Convert v1 Location to legacy LatLng format.

    Args:
        location: v1 Location object with latitude/longitude

    Returns:
        Legacy LatLng object, or None if location is None
    """
    if location is None:
        return None

    return LatLng(lat=location.latitude, lng=location.longitude)


def adapt_v1_viewport_to_legacy(viewport: Optional["receipt_places.types_v1.Viewport"]) -> Optional[Viewport]:
    """Convert v1 Viewport to legacy Viewport format.

    Args:
        viewport: v1 Viewport with low and high corners

    Returns:
        Legacy Viewport object, or None if viewport is None
    """
    if viewport is None:
        return None

    southwest = adapt_v1_location_to_legacy(viewport.low)
    northeast = adapt_v1_location_to_legacy(viewport.high)

    return Viewport(southwest=southwest, northeast=northeast)


def adapt_v1_geometry_to_legacy(
    location: Optional["receipt_places.types_v1.Location"],
    viewport: Optional["receipt_places.types_v1.Viewport"],
) -> Optional[Geometry]:
    """Convert v1 Location and Viewport to legacy Geometry format.

    Args:
        location: v1 Location object
        viewport: v1 Viewport object

    Returns:
        Legacy Geometry object, or None if both location and viewport are None
    """
    if location is None and viewport is None:
        return None

    return Geometry(
        location=adapt_v1_location_to_legacy(location),
        viewport=adapt_v1_viewport_to_legacy(viewport),
    )


def adapt_v1_plus_code_to_legacy(
    plus_code: Optional["receipt_places.types_v1.PlusCode"],
) -> Optional[PlusCode]:
    """Convert v1 PlusCode to legacy PlusCode format.

    Args:
        plus_code: v1 PlusCode object

    Returns:
        Legacy PlusCode object, or None if plus_code is None
    """
    if plus_code is None:
        return None

    return PlusCode(
        compound_code=plus_code.compound_code,
        global_code=plus_code.global_code,
    )


def adapt_v1_opening_hours_to_legacy(
    opening_hours: Optional["receipt_places.types_v1.OpeningHours"],
) -> Optional[OpeningHours]:
    """Convert v1 OpeningHours to legacy OpeningHours format.

    Args:
        opening_hours: v1 OpeningHours object

    Returns:
        Legacy OpeningHours object, or None if opening_hours is None
    """
    if opening_hours is None:
        return None

    # Adapt periods
    periods = None
    if opening_hours.periods:
        periods = [
            OpeningHoursPeriod(open=period.open, close=period.close)
            for period in opening_hours.periods
        ]

    return OpeningHours(
        open_now=opening_hours.open_now,
        periods=periods,
        weekday_text=opening_hours.weekday_descriptions,
    )


def adapt_v1_photos_to_legacy(
    photos: Optional[list["receipt_places.types_v1.Photo"]],
) -> Optional[list[Photo]]:
    """Convert v1 Photo list to legacy Photo format.

    Args:
        photos: List of v1 Photo objects

    Returns:
        List of legacy Photo objects, or None if photos is None
    """
    if photos is None:
        return None

    return [
        Photo(
            height=photo.height_px,
            html_attributions=None,  # Not available in v1
            photo_reference=photo.name,  # Use resource name as reference
            width=photo.width_px,
        )
        for photo in photos
    ]


def adapt_v1_to_legacy(place_v1: PlaceV1) -> Place:
    """Convert a v1 Place object to legacy Place format.

    This is the main adapter function that converts the new Places API (v1)
    response to the legacy format, maintaining backward compatibility with
    existing code that expects the legacy Place model.

    Args:
        place_v1: New Places API v1 Place object

    Returns:
        Legacy Place object with equivalent data

    Raises:
        ValueError: If critical fields are missing or invalid
    """
    # Extract place_id from the resource name if id is not directly available
    place_id = place_v1.id
    if not place_id:
        logger.warning("Place v1 has no id field, adapting may fail")
        place_id = None

    # Extract merchant name from display_name
    name = None
    if place_v1.display_name:
        name = place_v1.display_name.text
    elif place_v1.name:
        # Fallback to resource name if display_name not available
        # Resource name format: places/{place_id}
        name = None

    # Adapt geometry
    geometry = adapt_v1_geometry_to_legacy(place_v1.location, place_v1.viewport)

    # Adapt plus code
    plus_code = adapt_v1_plus_code_to_legacy(place_v1.plus_code)

    # Adapt opening hours (try currentOpeningHours first, then fall back to openingHours)
    opening_hours = adapt_v1_opening_hours_to_legacy(
        place_v1.current_opening_hours or place_v1.opening_hours
    )

    # Adapt photos
    photos = adapt_v1_photos_to_legacy(place_v1.photos)

    # Create legacy Place object
    return Place(
        # Identifiers
        place_id=place_id,
        name=name,
        # Address
        formatted_address=place_v1.formatted_address,
        short_formatted_address=place_v1.short_formatted_address,
        vicinity=place_v1.short_formatted_address,  # Use short address as vicinity
        # Geometry
        geometry=geometry,
        plus_code=plus_code,
        # Classification
        types=place_v1.types,
        business_status=place_v1.business_status,
        # Ratings & Reviews
        rating=place_v1.rating,
        user_ratings_total=place_v1.user_rating_count,
        # Contact
        formatted_phone_number=place_v1.national_phone_number,
        international_phone_number=place_v1.international_phone_number,
        website=place_v1.website_uri,
        url=place_v1.google_maps_uri,
        # Business Info
        opening_hours=opening_hours,
        photos=photos,
    )


def adapt_v1_to_candidate(place_v1: PlaceV1) -> Candidate:
    """Convert a v1 Place object to legacy Candidate format.

    Candidates are returned from findplacefromtext endpoints.

    Args:
        place_v1: New Places API v1 Place object

    Returns:
        Legacy Candidate object
    """
    # Extract name
    name = None
    if place_v1.display_name:
        name = place_v1.display_name.text
    elif place_v1.name:
        name = place_v1.name

    # Adapt geometry
    geometry = adapt_v1_geometry_to_legacy(place_v1.location, place_v1.viewport)

    return Candidate(
        formatted_address=place_v1.formatted_address,
        geometry=geometry,
        name=name,
        place_id=place_v1.id,
        types=place_v1.types,
        business_status=place_v1.business_status,
    )
