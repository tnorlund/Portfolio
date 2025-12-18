"""
Typed models for Google Places API (Legacy) responses.

All fields are Optional because field masks control what Google returns.
The key is to validate that you got the fields you requested via a validator.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LatLng(BaseModel):
    """Geographic point: latitude and longitude."""

    model_config = ConfigDict(populate_by_name=True)

    latitude: float = Field(..., alias="lat")
    longitude: float = Field(..., alias="lng")

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        """Validate latitude is within valid geographic range.

        Ensures latitude value is between -90.0 (South Pole) and 90.0 (North Pole).

        Args:
            v: Latitude value to validate

        Returns:
            Validated latitude value

        Raises:
            ValueError: If latitude is outside [-90.0, 90.0] range
        """
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"latitude out of range: {v}")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        """Validate longitude is within valid geographic range.

        Ensures longitude value is between -180.0 (West) and 180.0 (East).

        Args:
            v: Longitude value to validate

        Returns:
            Validated longitude value

        Raises:
            ValueError: If longitude is outside [-180.0, 180.0] range
        """
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"longitude out of range: {v}")
        return v


class Viewport(BaseModel):
    """Geographic viewport defined by southwest and northeast corners."""

    northeast: LatLng | None = None
    southwest: LatLng | None = None


class Geometry(BaseModel):
    """Location geometry including coordinates and viewport."""

    location: LatLng | None = None
    viewport: Viewport | None = None


class OpeningHoursPeriod(BaseModel):
    """Single period of opening hours (day and time)."""

    close: dict[str, Any] | None = None
    open: dict[str, Any] | None = None


class OpeningHours(BaseModel):
    """Business opening hours."""

    model_config = ConfigDict(populate_by_name=True)

    open_now: bool | None = None
    periods: list[OpeningHoursPeriod] | None = None
    weekday_text: list[str] | None = None


class Photo(BaseModel):
    """Photo metadata from Google Places."""

    model_config = ConfigDict(populate_by_name=True)

    height: int | None = None
    html_attributions: list[str] | None = None
    photo_reference: str | None = None
    width: int | None = None


class PlusCode(BaseModel):
    """Plus code for a location."""

    model_config = ConfigDict(populate_by_name=True)

    compound_code: str | None = None
    global_code: str | None = None


class Place(BaseModel):
    """
    Typed representation of a Google Places result.

    All fields are optional because field masks and different endpoints
    return different subsets of fields. Use validate_place_expected_fields()
    to ensure you got the fields you requested.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Identifiers
    place_id: str | None = None
    name: str | None = None

    # Address
    formatted_address: str | None = None
    short_formatted_address: str | None = None
    vicinity: str | None = None

    # Geo
    geometry: Geometry | None = None
    plus_code: PlusCode | None = None

    # Classification
    types: list[str] | None = None
    business_status: str | None = None

    # Ratings & Reviews
    rating: float | None = None
    user_ratings_total: int | None = None

    # Contact
    formatted_phone_number: str | None = None
    international_phone_number: str | None = None
    website: str | None = None
    url: str | None = None

    # Business Info
    opening_hours: OpeningHours | None = None
    photos: list[Photo] | None = None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: float | None) -> float | None:
        """Validate Google Places rating is within valid range.

        Google Places ratings are on a scale of 0.0 to 5.0 stars.

        Args:
            v: Rating value to validate (None allowed)

        Returns:
            Validated rating value or None

        Raises:
            ValueError: If rating is not None and outside [0.0, 5.0] range
        """
        if v is None:
            return v
        if not 0.0 <= v <= 5.0:
            raise ValueError(f"rating out of range: {v}")
        return v

    @field_validator("user_ratings_total")
    @classmethod
    def validate_user_ratings_total(cls, v: int | None) -> int | None:
        """Validate user ratings count is non-negative.

        The total number of user ratings must be >= 0.

        Args:
            v: User ratings total to validate (None allowed)

        Returns:
            Validated user ratings total or None

        Raises:
            ValueError: If value is not None and is negative
        """
        if v is None:
            return v
        if v < 0:
            raise ValueError(f"user_ratings_total must be >= 0, got {v}")
        return v


class Candidate(BaseModel):
    """A candidate result from findplacefromtext."""

    model_config = ConfigDict(populate_by_name=True)

    formatted_address: str | None = None
    geometry: Geometry | None = None
    name: str | None = None
    place_id: str | None = None
    types: list[str] | None = None
    business_status: str | None = None


class Prediction(BaseModel):
    """An autocomplete prediction."""

    model_config = ConfigDict(populate_by_name=True)

    description: str | None = None
    place_id: str | None = None
    reference: str | None = None
    structured_formatting: dict[str, Any] | None = None
    types: list[str] | None = None


# Legacy API Response Wrappers
class LegacyDetailsResponse(BaseModel):
    """Response from places/details/json endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    result: dict[str, Any] | None = None
    error_message: str | None = None


class LegacyCandidatesResponse(BaseModel):
    """Response from places/findplacefromtext/json endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    candidates: list[dict[str, Any]] | None = None
    error_message: str | None = None


class LegacySearchResponse(BaseModel):
    """Response from textsearch/json or nearbysearch/json endpoints."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    results: list[dict[str, Any]] | None = None
    next_page_token: str | None = None
    error_message: str | None = None


class LegacyAutocompleteResponse(BaseModel):
    """Response from places/autocomplete/json endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    predictions: list[dict[str, Any]] | None = None
    error_message: str | None = None
