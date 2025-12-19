"""
Unit tests for adapter layer (v1 → legacy conversion).

Tests that v1 API responses are correctly converted to legacy format
while maintaining data integrity and handling edge cases.
"""

import pytest

from receipt_places.adapter import (
    adapt_v1_geometry_to_legacy,
    adapt_v1_location_to_legacy,
    adapt_v1_opening_hours_to_legacy,
    adapt_v1_photos_to_legacy,
    adapt_v1_plus_code_to_legacy,
    adapt_v1_to_candidate,
    adapt_v1_to_legacy,
    adapt_v1_viewport_to_legacy,
)
from receipt_places.types import Candidate, Geometry, LatLng, OpeningHours, Place, PlusCode, Viewport
from receipt_places.types_v1 import (
    Location,
    LocalizedText,
    OpeningHoursPeriod,
    Photo,
    PlaceV1,
    PlusCode as PlusCodeV1,
    OpeningHours as OpeningHoursV1,
    Viewport as ViewportV1,
)


class TestLocationAdapter:
    """Tests for location/coordinate conversion."""

    def test_adapt_valid_location(self) -> None:
        """Test conversion of valid location coordinates."""
        v1_location = Location(latitude=40.7128, longitude=-74.0060)
        legacy_latlng = adapt_v1_location_to_legacy(v1_location)

        assert legacy_latlng is not None
        assert legacy_latlng.latitude == 40.7128
        assert legacy_latlng.longitude == -74.0060

    def test_adapt_none_location(self) -> None:
        """Test that None location returns None."""
        legacy_latlng = adapt_v1_location_to_legacy(None)
        assert legacy_latlng is None

    def test_adapt_location_edge_cases(self) -> None:
        """Test edge case coordinates (poles, date line)."""
        # North pole
        north_pole = Location(latitude=90.0, longitude=0.0)
        result = adapt_v1_location_to_legacy(north_pole)
        assert result is not None
        assert result.latitude == 90.0

        # South pole
        south_pole = Location(latitude=-90.0, longitude=0.0)
        result = adapt_v1_location_to_legacy(south_pole)
        assert result is not None
        assert result.latitude == -90.0

        # Date line
        date_line = Location(latitude=0.0, longitude=180.0)
        result = adapt_v1_location_to_legacy(date_line)
        assert result is not None
        assert result.longitude == 180.0

    def test_location_invalid_latitude_validation(self) -> None:
        """Test that Location validates latitude range."""
        with pytest.raises(ValueError, match="latitude out of range"):
            Location(latitude=91.0, longitude=0.0)

    def test_location_invalid_longitude_validation(self) -> None:
        """Test that Location validates longitude range."""
        with pytest.raises(ValueError, match="longitude out of range"):
            Location(latitude=0.0, longitude=181.0)


class TestViewportAdapter:
    """Tests for viewport/bounding box conversion."""

    def test_adapt_valid_viewport(self) -> None:
        """Test conversion of valid viewport with low and high corners."""
        low = Location(latitude=40.7000, longitude=-74.0100)
        high = Location(latitude=40.7200, longitude=-74.0000)
        v1_viewport = ViewportV1(low=low, high=high)

        legacy_viewport = adapt_v1_viewport_to_legacy(v1_viewport)

        assert legacy_viewport is not None
        assert legacy_viewport.southwest is not None
        assert legacy_viewport.southwest.latitude == 40.7000
        assert legacy_viewport.northeast is not None
        assert legacy_viewport.northeast.latitude == 40.7200

    def test_adapt_none_viewport(self) -> None:
        """Test that None viewport returns None."""
        legacy_viewport = adapt_v1_viewport_to_legacy(None)
        assert legacy_viewport is None

    def test_adapt_partial_viewport(self) -> None:
        """Test viewport with only one corner."""
        low = Location(latitude=40.7000, longitude=-74.0100)
        v1_viewport = ViewportV1(low=low, high=None)

        legacy_viewport = adapt_v1_viewport_to_legacy(v1_viewport)

        assert legacy_viewport is not None
        assert legacy_viewport.southwest is not None
        assert legacy_viewport.northeast is None


class TestGeometryAdapter:
    """Tests for geometry conversion."""

    def test_adapt_full_geometry(self) -> None:
        """Test conversion of geometry with location and viewport."""
        location = Location(latitude=40.7128, longitude=-74.0060)
        viewport = ViewportV1(
            low=Location(latitude=40.7000, longitude=-74.0100),
            high=Location(latitude=40.7200, longitude=-74.0000),
        )

        geometry = adapt_v1_geometry_to_legacy(location, viewport)

        assert geometry is not None
        assert geometry.location is not None
        assert geometry.location.latitude == 40.7128
        assert geometry.viewport is not None

    def test_adapt_geometry_location_only(self) -> None:
        """Test geometry with only location, no viewport."""
        location = Location(latitude=40.7128, longitude=-74.0060)
        geometry = adapt_v1_geometry_to_legacy(location, None)

        assert geometry is not None
        assert geometry.location is not None
        assert geometry.viewport is None

    def test_adapt_none_geometry(self) -> None:
        """Test that None for both location and viewport returns None."""
        geometry = adapt_v1_geometry_to_legacy(None, None)
        assert geometry is None


class TestPlusCodeAdapter:
    """Tests for plus code conversion."""

    def test_adapt_valid_plus_code(self) -> None:
        """Test conversion of plus code."""
        v1_plus_code = PlusCodeV1(
            global_code="87C4VW3X+HQ",
            compound_code="VW3X+HQ New York, NY",
        )

        legacy_plus_code = adapt_v1_plus_code_to_legacy(v1_plus_code)

        assert legacy_plus_code is not None
        assert legacy_plus_code.global_code == "87C4VW3X+HQ"
        assert legacy_plus_code.compound_code == "VW3X+HQ New York, NY"

    def test_adapt_none_plus_code(self) -> None:
        """Test that None plus code returns None."""
        legacy_plus_code = adapt_v1_plus_code_to_legacy(None)
        assert legacy_plus_code is None


class TestOpeningHoursAdapter:
    """Tests for opening hours conversion."""

    def test_adapt_full_opening_hours(self) -> None:
        """Test conversion of complete opening hours."""
        v1_hours = OpeningHoursV1(
            open_now=True,
            periods=[
                OpeningHoursPeriod(
                    open={"day": 0, "time": "0900"},
                    close={"day": 0, "time": "1700"},
                ),
            ],
            weekday_descriptions=[
                "Monday: 9:00 AM – 5:00 PM",
                "Tuesday: 9:00 AM – 5:00 PM",
            ],
        )

        legacy_hours = adapt_v1_opening_hours_to_legacy(v1_hours)

        assert legacy_hours is not None
        assert legacy_hours.open_now is True
        assert legacy_hours.periods is not None
        assert len(legacy_hours.periods) == 1
        assert legacy_hours.weekday_text is not None
        assert len(legacy_hours.weekday_text) == 2

    def test_adapt_none_opening_hours(self) -> None:
        """Test that None opening hours returns None."""
        legacy_hours = adapt_v1_opening_hours_to_legacy(None)
        assert legacy_hours is None


class TestPhotosAdapter:
    """Tests for photo conversion."""

    def test_adapt_valid_photos(self) -> None:
        """Test conversion of photo list."""
        v1_photos = [
            Photo(
                name="places/abc/photos/xyz",
                width_px=800,
                height_px=600,
            ),
            Photo(
                name="places/abc/photos/uvw",
                width_px=1024,
                height_px=768,
            ),
        ]

        legacy_photos = adapt_v1_photos_to_legacy(v1_photos)

        assert legacy_photos is not None
        assert len(legacy_photos) == 2
        assert legacy_photos[0].width == 800
        assert legacy_photos[0].height == 600
        assert legacy_photos[0].photo_reference == "places/abc/photos/xyz"

    def test_adapt_none_photos(self) -> None:
        """Test that None photo list returns None."""
        legacy_photos = adapt_v1_photos_to_legacy(None)
        assert legacy_photos is None

    def test_adapt_empty_photos(self) -> None:
        """Test conversion of empty photo list."""
        legacy_photos = adapt_v1_photos_to_legacy([])
        assert legacy_photos == []


class TestFullPlaceAdapterWithMocks:
    """Tests for full v1→legacy Place conversion with realistic data."""

    @staticmethod
    def create_v1_place(
        place_id: str = "ChIJIQBpAG2ahYAR_6128GltTXQ",
        name: str = "Google Sydney",
        latitude: float = -33.8688,
        longitude: float = 151.2093,
        rating: float = 4.5,
        user_rating_count: int = 500,
    ) -> PlaceV1:
        """Helper to create a realistic v1 Place object."""
        return PlaceV1(
            id=place_id,
            display_name=LocalizedText(text=name, language_code="en"),
            location=Location(latitude=latitude, longitude=longitude),
            formatted_address="48 Pirrama Rd, Pyrmont NSW 2009, Australia",
            short_formatted_address="48 Pirrama Rd, Pyrmont",
            types=["point_of_interest", "establishment"],
            business_status="OPERATIONAL",
            rating=rating,
            user_rating_count=user_rating_count,
            national_phone_number="+61 2 8374 4000",
            international_phone_number="+61 2 8374 4000",
            website_uri="https://www.google.com/about/locations/sydney/",
            google_maps_uri="https://maps.google.com/maps/place/Google+Sydney",
            opening_hours=OpeningHoursV1(
                open_now=True,
                weekday_descriptions=[
                    "Monday: 9:00 AM – 5:00 PM",
                    "Tuesday: 9:00 AM – 5:00 PM",
                ],
            ),
        )

    def test_adapt_full_realistic_place(self) -> None:
        """Test conversion of realistic v1 Place with all fields."""
        v1_place = self.create_v1_place()
        legacy_place = adapt_v1_to_legacy(v1_place)

        assert isinstance(legacy_place, Place)
        assert legacy_place.place_id == "ChIJIQBpAG2ahYAR_6128GltTXQ"
        assert legacy_place.name == "Google Sydney"
        assert legacy_place.geometry is not None
        assert legacy_place.geometry.location is not None
        assert legacy_place.geometry.location.latitude == -33.8688
        assert legacy_place.formatted_address == "48 Pirrama Rd, Pyrmont NSW 2009, Australia"
        assert legacy_place.rating == 4.5
        assert legacy_place.user_ratings_total == 500
        assert legacy_place.formatted_phone_number == "+61 2 8374 4000"
        assert legacy_place.website == "https://www.google.com/about/locations/sydney/"

    def test_adapt_place_with_minimal_fields(self) -> None:
        """Test conversion of v1 Place with only required fields."""
        v1_place = PlaceV1(id="ChIJ...", display_name=LocalizedText(text="Test Place"))
        legacy_place = adapt_v1_to_legacy(v1_place)

        assert legacy_place.place_id == "ChIJ..."
        assert legacy_place.name == "Test Place"
        # Optional fields should be None
        assert legacy_place.rating is None
        assert legacy_place.formatted_phone_number is None

    def test_adapt_rating_validation(self) -> None:
        """Test that rating is validated during adaptation."""
        v1_place = PlaceV1(
            id="test",
            display_name=LocalizedText(text="Test"),
            rating=4.5,
        )
        legacy_place = adapt_v1_to_legacy(v1_place)
        assert legacy_place.rating == 4.5

    def test_adapt_invalid_rating_fails(self) -> None:
        """Test that invalid rating raises error."""
        with pytest.raises(ValueError, match="rating out of range"):
            PlaceV1(
                id="test",
                display_name=LocalizedText(text="Test"),
                rating=5.5,  # Invalid: > 5.0
            )


class TestCandidateAdapter:
    """Tests for Place → Candidate conversion."""

    def test_adapt_v1_to_candidate(self) -> None:
        """Test conversion of v1 Place to legacy Candidate."""
        v1_place = PlaceV1(
            id="ChIJ...",
            display_name=LocalizedText(text="Test Business"),
            formatted_address="123 Main St, City, ST",
            location=Location(latitude=40.7128, longitude=-74.0060),
            types=["restaurant", "point_of_interest"],
            business_status="OPERATIONAL",
        )

        candidate = adapt_v1_to_candidate(v1_place)

        assert isinstance(candidate, Candidate)
        assert candidate.place_id == "ChIJ..."
        assert candidate.name == "Test Business"
        assert candidate.formatted_address == "123 Main St, City, ST"
        assert candidate.types == ["restaurant", "point_of_interest"]
        assert candidate.business_status == "OPERATIONAL"
