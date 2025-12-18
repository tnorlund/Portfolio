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

---

# Follow-up Q&A

## Q: What's the difference between the Legacy API and the New API?

Big picture: **"Places API (Legacy)"** is the older web-service surface (`maps.googleapis.com/maps/api/place/...`) with fixed response shapes and "pick fields via `fields=` query param (in some methods)". **"Places API (New)"** is the newer v1 surface (`places.googleapis.com/v1/...`) where you **must** use a **response field mask** (typically `X-Goog-FieldMask`) so you only receive—and pay for—what you request. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/overview-legacy)

### The practical differences you'll feel in code

**1) Base URL + request style**

- **Legacy:** `https://maps.googleapis.com/maps/api/place/...` (e.g., `/details/json`, `/nearbysearch/json`). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)
- **New:** `https://places.googleapis.com/v1/...` (e.g., `/places/{place_id}`). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

**2) Field selection is fundamentally different**

- **New:** For Place Details / Nearby Search / Text Search you **must** specify desired fields using a **field mask** (otherwise you can get errors) and it's also how pricing/latency are optimized. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/choose-fields)
- **Legacy:** You typically get a broader, more fixed payload shape (and legacy methods have their own parameters/behavior). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)

**3) Response shape**

- **Legacy:** usually wraps data in `status` plus `result` (details) or `results` (search). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)
- **New:** returns a **Place object** directly (for details), and the presence of fields depends on your field mask. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/place-details)

**4) Pricing model (and why field masks matter)**

- **New:** Google explicitly frames pricing as "simplified" because you only pay for the data you request, and the mechanism is the field mask. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/overview-legacy)
- **Legacy:** pricing is not driven by that "mask = billed fields" model in the same way (and migration is often prompted by cost control + performance). [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/overview-legacy)

**5) Feature surface**

- **New:** Google notes it includes *new fields/attributes/accessibility options* compared to legacy. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/op-overview)
- **Legacy:** still supported, but clearly positioned as "legacy" in the docs. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/overview-legacy)

### What this means for your datatypes / data quality checks

If you stay on **Legacy**, your models can assume:

- `status` exists, and you should validate it (`OK` vs error states) before parsing `result/results`. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/legacy/details)
- payload shape is more consistent per endpoint.

If you move to **New**, you should change your validation strategy:

- treat nearly everything as optional **unless it's in your field mask**, and validate "required fields" by comparing against the mask. [Google for Developers](https://developers.google.com/maps/documentation/places/web-service/choose-fields)

If you tell me which legacy endpoints you're calling (details vs nearbysearch vs textsearch vs autocomplete), I'll map them to the New equivalents and give you a clean set of Python models + validators for *your* payloads.

---

## Q: Do I get more data per payload using the new API?

Short answer: **yes, potentially—but only if you ask for it.**

Longer answer: the **new Places API can return *more* data than legacy**, but **you will not automatically get more per payload** because the new API is *opt-in by design* via **field masks**.

Here's how to think about it clearly.

---

### Mental model: "fixed bundle" vs "à la carte"

#### Legacy Places API

- You get a **predefined bundle** of fields per endpoint.
- You can't finely control *which* fields come back.
- Some fields are **simply unavailable**, no matter what.
- Payloads are often **bloated** relative to what you actually need.

➡️ *Convenient, but inflexible.*

---

#### New Places API

- You get **exactly the fields you request** via a field mask.
- If you request **nothing**, you get **almost nothing** (or an error).
- If you request **many fields**, you *can* get **more data than legacy ever returned**.
- Pricing and latency scale with how much you ask for.

➡️ *Powerful, but explicit.*

---

### Do you get "more data" in practice?

#### ✅ Yes, **if you opt into it**

The new API exposes **additional and more structured fields** that legacy never had or exposed poorly, for example:

- Richer **accessibility attributes**
- More granular **opening hours** structures
- Better **place type taxonomy**
- More consistent **internationalization** (localized names)
- Cleaner **URIs and identifiers**
- Improved **business metadata** over time (Google is investing here, not legacy)

If your field mask includes these, your payload can be **larger and richer** than legacy.

---

#### ❌ No, **by default**

If you naïvely migrate and request only what legacy "felt like it gave you", your payload will often be:

- Smaller
- Missing fields you assumed were "always there"
- Missing fields your downstream code implicitly relied on

This is the #1 migration footgun.

---

### Concrete comparison example (Place Details)

#### Legacy (implicit bundle)

```json
{
  "status": "OK",
  "result": {
    "place_id": "...",
    "name": "...",
    "rating": 4.5,
    "user_ratings_total": 213,
    "geometry": { "location": { "lat": ..., "lng": ... } },
    "formatted_address": "...",
    "types": [...]
  }
}
```

You didn't ask for these fields — Google chose them.

---

#### New API (explicit)

```http
X-Goog-FieldMask:
id,displayName,formattedAddress,location,rating,userRatingCount,types
```

```json
{
  "id": "...",
  "displayName": { "text": "..." },
  "formattedAddress": "...",
  "location": { "latitude": ..., "longitude": ... },
  "rating": 4.5,
  "userRatingCount": 213,
  "types": [...]
}
```

Same data — but now **you are in control**.

Add more fields to the mask and you get more.

---

### Why Google did this (important for you as an engineer)

This change is **not cosmetic**. It enables:

1. **Predictable pricing**
   - You pay for what you request, not a mystery bundle.

2. **Performance guarantees**
   - Smaller responses → lower latency → better mobile/server performance.

3. **Schema evolution**
   - Google can add new fields without breaking clients.
   - Legacy could not evolve safely.

4. **Strong typing**
   - Field masks + structured objects = better SDKs and validation (what you're building).

---

### Implications for your Python dataclasses & data quality checks

Because you're on **legacy today**, your models likely assume:

- Certain fields are *always present*
- Payload shape is stable

If/when you move to the new API, you should change your strategy:

#### ✅ Recommended approach

- **Model everything as optional**
- Maintain a **"required fields" list per endpoint**
- Validate *against the field mask*, not against the schema

This matches how modern Google APIs are meant to be consumed.

---

### TL;DR

- **Yes**, the new API can return *more data than legacy*
- **No**, it won't unless you explicitly request it
- Field masks trade convenience for **control, cost efficiency, and future-proofing**
- For typed Python wrappers, the new API is *strictly better* once you adapt your validation model

If you want, next we can:

- Map *your exact legacy endpoint* → *new equivalent*
- Design a **migration-safe model** that supports both without breaking downstream code
- Define **canonical field masks** for your use cases (details vs enrichment vs ranking)

---

## Q: Do I need a separate API key for the new API?

Short answer: **no — you do NOT need a separate API key.**

You can use the **same API key** for both the **legacy Places API** and the **new Places API**, *as long as the new API is enabled in the same GCP project*.

Here's the clear breakdown, step by step.

---

### How API keys actually work in GCP

An **API key is scoped to a GCP project**, not to a specific API version.

So:

- ✅ One project
- ✅ One API key
- ➕ Multiple enabled APIs
- ➕ Multiple endpoints (legacy + new)

---

### What you *do* need to do

#### 1️⃣ Enable **Places API (New)** in your project

Even if legacy works, the new API is a **separate service toggle**.

In GCP Console:

```
APIs & Services → Library → Places API (New) → Enable
```

You do **not** need to disable the legacy API to do this.

---

#### 2️⃣ Use the same API key

You'll keep using:

```http
X-Goog-Api-Key: YOUR_EXISTING_KEY
```

The difference is **where** and **how** you send it:

| Legacy | New |
|--------|-----|
| `?key=API_KEY` query param | `X-Goog-Api-Key` header (recommended) |
| Fixed payloads | Field-mask–driven payloads |

---

#### 3️⃣ Make sure billing is enabled

Both legacy and new Places APIs **require billing**.

If legacy is working, billing is already on — no extra action needed.

---

### What *does not* change

- ❌ You do **not** need a new key
- ❌ You do **not** need a new project
- ❌ You do **not** need OAuth
- ❌ You do **not** need to rotate secrets just to migrate

---

### Recommended best practice (since you're building a wrapper)

Since you're early in GCP, I *strongly* recommend:

#### 🔐 Create a **second API key** (optional but smart)

Use the same project, but separate keys for:

- legacy traffic
- new API traffic

Why this helps:

- Independent quotas
- Cleaner metrics
- Easier rollback
- Tighter restrictions

This is optional, but very clean for production systems.

---

### Key restrictions you should double-check

For server-side Python usage:

- ✅ **Application restriction**: "None" or IP-based
- ✅ **API restriction**:
  - `Places API`
  - `Places API (New)`

If the new API isn't listed, it's not enabled yet.

---

### Quick sanity check: minimal new API request

If this works, you're good:

```bash
curl -X GET \
  "https://places.googleapis.com/v1/places/PLACE_ID" \
  -H "X-Goog-Api-Key: YOUR_EXISTING_KEY" \
  -H "X-Goog-FieldMask: id,displayName,formattedAddress"
```

If you get:

- ❌ `PERMISSION_DENIED` → API not enabled
- ❌ `INVALID_ARGUMENT` → field mask issue
- ✅ JSON Place object → you're set

---

### TL;DR

- ✅ Same API key works for legacy **and** new
- ❗ You must enable **Places API (New)** explicitly
- 🧠 Field masks + headers are the real difference
- 🔐 Optional: separate keys for cleaner ops

If you want, next I can:

- Walk you through **enabling + restricting the API** safely
- Help design **field masks** that match your legacy payloads
- Show how to dual-run legacy + new during migration without breaking consumers
