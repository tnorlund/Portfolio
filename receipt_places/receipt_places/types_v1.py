"""
Typed models for Google Places API (New v1) responses.

The new Places API (v1) uses field masks to control response structure.
All fields are Optional because the set of fields returned is exactly
what you request via the X-Goog-FieldMask header.

Reference:
https://developers.google.com/maps/documentation/places/web-service/place-details
https://developers.google.com/maps/documentation/places/web-service/data-fields
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalizedText(BaseModel):
    """Localized text with language code."""

    model_config = ConfigDict(populate_by_name=True)

    text: str
    language_code: Optional[str] = Field(default=None, alias="languageCode")


class Location(BaseModel):
    """Geographic point: latitude and longitude."""

    model_config = ConfigDict(populate_by_name=True)

    latitude: float
    longitude: float

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
    """Geographic viewport defined by low (southwest) and high (northeast) corners."""

    model_config = ConfigDict(populate_by_name=True)

    low: Optional[Location] = None
    high: Optional[Location] = None


class PlusCode(BaseModel):
    """Plus code for a location (Open Location Code)."""

    model_config = ConfigDict(populate_by_name=True)

    global_code: Optional[str] = Field(default=None, alias="globalCode")
    compound_code: Optional[str] = Field(default=None, alias="compoundCode")


class AddressComponent(BaseModel):
    """Structured address component."""

    model_config = ConfigDict(populate_by_name=True)

    long_text: Optional[str] = Field(default=None, alias="longText")
    short_text: Optional[str] = Field(default=None, alias="shortText")
    types: Optional[List[str]] = None
    language_code: Optional[str] = Field(default=None, alias="languageCode")


class OpeningHoursPeriod(BaseModel):
    """Single period of opening hours (day and time)."""

    model_config = ConfigDict(populate_by_name=True)

    open: Optional[Dict[str, Any]] = None
    close: Optional[Dict[str, Any]] = None


class OpeningHours(BaseModel):
    """Business opening hours."""

    model_config = ConfigDict(populate_by_name=True)

    open_now: Optional[bool] = Field(default=None, alias="openNow")
    periods: Optional[List[OpeningHoursPeriod]] = None
    weekday_descriptions: Optional[List[str]] = Field(
        default=None, alias="weekdayDescriptions"
    )


class Photo(BaseModel):
    """Photo metadata from Google Places."""

    model_config = ConfigDict(populate_by_name=True)

    name: str  # Resource name format: places/{place_id}/photos/{photo_ref}
    width_px: Optional[int] = Field(default=None, alias="widthPx")
    height_px: Optional[int] = Field(default=None, alias="heightPx")
    author_attributions: Optional[List[Dict[str, Any]]] = Field(
        default=None, alias="authorAttributions"
    )


class AuthorAttribution(BaseModel):
    """Author attribution for a review or photo."""

    model_config = ConfigDict(populate_by_name=True)

    display_name: Optional[str] = Field(default=None, alias="displayName")
    uri: Optional[str] = None
    photo_uri: Optional[str] = Field(default=None, alias="photoUri")


class Review(BaseModel):
    """User-generated review."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None  # Resource name
    relative_publish_time_description: Optional[str] = Field(
        default=None, alias="relativePublishTimeDescription"
    )
    rating: Optional[int] = None
    text: Optional[LocalizedText] = None
    publish_time: Optional[str] = Field(default=None, alias="publishTime")
    author_attribution: Optional[AuthorAttribution] = Field(
        default=None, alias="authorAttribution"
    )


class PlaceV1(BaseModel):
    """
    Google Places API (New v1) Place resource.

    All fields are optional because the presence of fields is determined
    by the field mask used in the request.

    Reference: https://developers.google.com/maps/documentation/places/web-service/place-details
    """

    model_config = ConfigDict(populate_by_name=True)

    # === Identity ===
    name: Optional[str] = None  # Resource name format: places/{place_id}
    id: Optional[str] = None  # The actual place_id

    # === Names & Classification ===
    display_name: Optional[LocalizedText] = Field(default=None, alias="displayName")
    primary_type: Optional[str] = Field(default=None, alias="primaryType")
    primary_type_display_name: Optional[LocalizedText] = Field(
        default=None, alias="primaryTypeDisplayName"
    )
    types: Optional[List[str]] = None

    # === Address ===
    formatted_address: Optional[str] = Field(default=None, alias="formattedAddress")
    short_formatted_address: Optional[str] = Field(
        default=None, alias="shortFormattedAddress"
    )
    address_components: Optional[List[AddressComponent]] = Field(
        default=None, alias="addressComponents"
    )

    # === Location & Geometry ===
    location: Optional[Location] = None
    viewport: Optional[Viewport] = None
    plus_code: Optional[PlusCode] = Field(default=None, alias="plusCode")

    # === Contact Information ===
    national_phone_number: Optional[str] = Field(
        default=None, alias="nationalPhoneNumber"
    )
    international_phone_number: Optional[str] = Field(
        default=None, alias="internationalPhoneNumber"
    )
    website_uri: Optional[str] = Field(default=None, alias="websiteUri")
    google_maps_uri: Optional[str] = Field(default=None, alias="googleMapsUri")

    # === Business Status & Hours ===
    business_status: Optional[str] = Field(default=None, alias="businessStatus")
    opening_hours: Optional[OpeningHours] = Field(default=None, alias="openingHours")
    current_opening_hours: Optional[OpeningHours] = Field(default=None, alias="currentOpeningHours")

    # === Rating & Reviews ===
    rating: Optional[float] = None
    user_rating_count: Optional[int] = Field(default=None, alias="userRatingCount")
    reviews: Optional[List[Review]] = None

    # === Media ===
    photos: Optional[List[Photo]] = None

    # === Accessibility ===
    wheelchair_accessible_entrance: Optional[bool] = Field(
        default=None, alias="wheelchairAccessibleEntrance"
    )

    # === Additional Info ===
    price_level: Optional[str] = Field(default=None, alias="priceLevel")
    utc_offset_minutes: Optional[int] = Field(default=None, alias="utcOffsetMinutes")

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: Optional[float]) -> Optional[float]:
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

    @field_validator("user_rating_count")
    @classmethod
    def validate_user_rating_count(cls, v: Optional[int]) -> Optional[int]:
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
            raise ValueError(f"user_rating_count must be >= 0, got {v}")
        return v


class SearchTextResponse(BaseModel):
    """Response from places:searchText endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    places: Optional[List[PlaceV1]] = None
    next_page_token: Optional[str] = Field(default=None, alias="nextPageToken")


class SearchNearbyResponse(BaseModel):
    """Response from places:searchNearby endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    places: Optional[List[PlaceV1]] = None
    next_page_token: Optional[str] = Field(default=None, alias="nextPageToken")
