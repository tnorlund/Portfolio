# Google Places API: Typed Payloads & Data Quality Checks

Below is a practical way to add **typed payloads + data quality checks** to a Python wrapper around the Google Places API, while staying flexible across the two "flavors" you'll see in the wild:

- **Places API (New)**: `places.googleapis.com/v1/...` where responses are a **Place object**, and a **FieldMask is required** (typically via `X-Goog-FieldMask`). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

- **Places API (Legacy)**: `maps.googleapis.com/maps/api/place/...` where responses are usually wrapped in `status` + `result` / `results`. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)

Because field masks control which properties exist, the key trick is: **model everything as optional**, then enforce *your* "required" subset via a small validator that knows what you requested.

---

## What you can get back (New API mental model)

For Places API (New), the response is a **Place object**, and the set of fields returned is exactly what you request in your **field mask**. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

Google also publishes the canonical list of available place fields (e.g., `id`, `displayName`, `formattedAddress`, `location`, `rating`, `reviews`, `websiteUri`, `regularOpeningHours`, etc.). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/data-fields)

---

## Recommended implementation approach

### 1) Use a "typed dataclass" with runtime validation

If you truly want *dataclasses* (not just type hints), the most ergonomic approach is:

- `pydantic` for validation (fast to implement, great errors)
- expose your models as `@pydantic.dataclasses.dataclass` so your public API is still "dataclass-y"

(If you don't want an added dependency, I can also show a pure-stdlib `dataclasses + manual validators` approach, but it's more boilerplate.)

### 2) Add a "quality gate" tied to your field mask

Your wrapper knows what it asked for (field mask), so you can enforce:

- required presence (e.g. if you request `location`, require it)
- value ranges (lat/lng bounds, rating 0–5, non-negative counts)
- format checks (URI, phone number shape, etc.)

---

## A solid starting model for Place Details (New)

This covers the most common fields people ask for and bakes in a few high-value checks.

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator, model_validator


class LocalizedText(BaseModel):
    text: str
    languageCode: Optional[str] = None


class LatLng(BaseModel):
    latitude: float
    longitude: float

    @field_validator("latitude")
    @classmethod
    def lat_range(cls, v: float) -> float:
        if not (-90.0 <= v <= 90.0):
            raise ValueError(f"latitude out of range: {v}")
        return v

    @field_validator("longitude")
    @classmethod
    def lng_range(cls, v: float) -> float:
        if not (-180.0 <= v <= 180.0):
            raise ValueError(f"longitude out of range: {v}")
        return v


class Viewport(BaseModel):
    low: LatLng
    high: LatLng


class PlusCode(BaseModel):
    globalCode: str
    compoundCode: Optional[str] = None


class Place(BaseModel):
    # Identifiers
    id: Optional[str] = None
    name: Optional[str] = None  # resource name like "places/PLACE_ID" in New API

    # Human-facing
    displayName: Optional[LocalizedText] = None
    formattedAddress: Optional[str] = None
    shortFormattedAddress: Optional[str] = None

    # Geo
    location: Optional[LatLng] = None
    viewport: Optional[Viewport] = None
    plusCode: Optional[PlusCode] = None

    # Classification
    types: Optional[List[str]] = None
    primaryType: Optional[str] = None
    primaryTypeDisplayName: Optional[LocalizedText] = None

    # Popular business fields
    businessStatus: Optional[str] = None
    rating: Optional[float] = None
    userRatingCount: Optional[int] = None
    websiteUri: Optional[HttpUrl] = None
    googleMapsUri: Optional[HttpUrl] = None

    @field_validator("rating")
    @classmethod
    def rating_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not (0.0 <= v <= 5.0):
            raise ValueError(f"rating out of range: {v}")
        return v

    @field_validator("userRatingCount")
    @classmethod
    def non_negative_count(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0:
            raise ValueError(f"userRatingCount must be >= 0, got {v}")
        return v


def validate_place_expected_fields(place: Place, expected_fields: Set[str]) -> None:
    """
    Data quality gate: ensure that any field you requested via FieldMask exists in the parsed model.
    This is how you catch partial responses, upstream changes, or wrapper mistakes.
    """
    missing = []
    for f in expected_fields:
        # supports top-level fields like "id", "displayName", "location"
        # (you can extend this to nested paths if you use them)
        if getattr(place, f, None) is None:
            missing.append(f)

    if missing:
        raise ValueError(f"Missing expected fields from Places response: {missing}")
```

### How you'd use it in your requests wrapper

```python
import requests

def get_place_details_new(place_id: str, field_mask: str, api_key: str) -> Place:
    url = f"https://places.googleapis.com/v1/places/{place_id}"

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,  # required in New API
        "Content-Type": "application/json",
    }

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    payload = resp.json()
    place = Place.model_validate(payload)

    expected = {f.strip() for f in field_mask.split(",") if f.strip() and f.strip() != "*"}
    if expected:
        validate_place_expected_fields(place, expected)

    return place
```

This directly matches Google's documented behavior: **field mask drives the fields in the Place object**. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

---

## Supporting Legacy responses without pain

Legacy endpoints typically return wrappers like:

- details: `{ "status": "...", "result": {...} }`
- search: `{ "status": "...", "results": [ ... ] }`

Google still documents the legacy Place Details format. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)

You can model that with thin wrappers:

```python
class LegacyDetailsResponse(BaseModel):
    status: str
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

def parse_legacy_details(payload: Dict[str, Any]) -> Place:
    # Convert legacy "result" shape into something close to your Place model,
    # OR define a separate LegacyPlace model if you prefer.
    resp = LegacyDetailsResponse.model_validate(payload)
    if resp.status != "OK":
        raise ValueError(f"Legacy Places error: status={resp.status} error={resp.error_message}")

    # Minimal mapping example:
    r = resp.result or {}
    mapped = {
        "id": r.get("place_id"),
        "displayName": {"text": r.get("name")} if r.get("name") else None,
        "formattedAddress": r.get("formatted_address"),
        "rating": r.get("rating"),
        "userRatingCount": r.get("user_ratings_total"),
        "websiteUri": r.get("website"),
        "googleMapsUri": r.get("url"),
        "location": (
            {"latitude": r["geometry"]["location"]["lat"], "longitude": r["geometry"]["location"]["lng"]}
            if r.get("geometry", {}).get("location") else None
        ),
        "types": r.get("types"),
    }
    return Place.model_validate(mapped)
```

---

## High-value data quality checks to add next

1. **FieldMask enforcement** (already shown)

2. **Geospatial sanity**: lat/lng ranges; viewport corners make sense

3. **Rating sanity**: rating 0–5; `userRatingCount >= 0`

4. **Enum tightening** for things you rely on:
   - `businessStatus` (if you use it for filtering)
   - `priceLevel` / `priceRange` (if you request them)

5. **"At least one identifier"**: ensure `id` or `name` exists (New API has both concepts: `id` is the standalone place ID; `name` is the resource name `places/PLACE_ID`). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

6. **Error payload handling**: for New API you'll also want to detect structured error responses (HTTP 4xx/5xx) vs a Place object.

---

## What I'd do in your wrapper right away

- Add a `Place` model (like above) + `validate_place_expected_fields()`

- Centralize request execution (`requests.get/post`) into one function that:
  - checks HTTP status
  - parses JSON
  - routes to the right parser (New vs Legacy)
  - returns typed objects

- Keep your "required fields" as a **configuration per method** (details vs nearby vs text search) so you don't accidentally demand fields you didn't request.

If you paste one example payload you're currently receiving (redact the key), I can tailor the model to the exact endpoints you're calling (e.g., Place Details vs Nearby Search) and add the right nested types (opening hours, photos, reviews, etc.) based on the official field list. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/data-fields)
