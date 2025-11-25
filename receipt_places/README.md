# receipt_places

Google Places API client with DynamoDB caching for receipt processing.

## Features

- **Automatic Caching**: All phone and address searches are automatically cached in DynamoDB
- **Cache Exclusions**: Intelligently excludes area searches, route-level results, and incomplete addresses
- **TTL Support**: Configurable cache expiration (default 30 days)
- **Query Analytics**: Tracks query counts for cache entries
- **Retry Logic**: Automatic retries with exponential backoff for API failures
- **Clean Interface**: Simple, type-safe API

## Installation

```bash
# From repository root
pip install -e ./receipt_places

# With development dependencies
pip install -e "./receipt_places[dev]"
```

## Configuration

Configuration via environment variables:

```bash
# Required
export RECEIPT_PLACES_API_KEY="your-google-places-api-key"

# Optional
export RECEIPT_PLACES_TABLE_NAME="receipts"      # DynamoDB table name
export RECEIPT_PLACES_AWS_REGION="us-west-2"     # AWS region
export RECEIPT_PLACES_CACHE_TTL_DAYS="30"        # Cache TTL in days
export RECEIPT_PLACES_CACHE_ENABLED="true"       # Enable/disable caching
export RECEIPT_PLACES_REQUEST_TIMEOUT="30"       # HTTP timeout in seconds
```

## Usage

### Basic Usage

```python
from receipt_places import PlacesClient

# Initialize client
client = PlacesClient()

# Search by phone number (cached)
result = client.search_by_phone("555-123-4567")
if result:
    print(f"Found: {result['name']} at {result['formatted_address']}")

# Search by address (cached)
result = client.search_by_address("123 Main St, Seattle, WA 98101")
if result:
    print(f"Place ID: {result['place_id']}")

# Text search (NOT cached due to query variability)
result = client.search_by_text("Starbucks near Pike Place Market")

# Nearby search
results = client.search_nearby(lat=47.6062, lng=-122.3321, radius=500)

# Get place details
details = client.get_place_details("ChIJcXGy...")
```

### Direct Cache Access

```python
from receipt_places import CacheManager

# Initialize cache manager
cache = CacheManager(table_name="receipts")

# Direct cache operations
cached = cache.get("PHONE", "5551234567")
cache.put("ADDRESS", "123 Main St", "ChIJxxx", {"place_id": "ChIJxxx", "name": "Test"})
cache.delete("PHONE", "5551234567")

# Query by place_id (uses GSI1)
result = cache.get_by_place_id("ChIJxxx")

# Get statistics
stats = cache.get_stats()
print(f"Total cached entries: {stats['total_entries']}")
```

### Custom Configuration

```python
from receipt_places import PlacesClient, PlacesConfig

config = PlacesConfig(
    api_key="your-api-key",
    table_name="my-table",
    cache_ttl_days=7,
    cache_enabled=True,
)

client = PlacesClient(config=config)
```

## Cache Schema

Uses the existing PlacesCache schema from `receipt_dynamo`:

| Attribute | Description |
|-----------|-------------|
| PK | `PLACES#{search_type}` (ADDRESS, PHONE, URL) |
| SK | `VALUE#{padded_value}` |
| GSI1PK | `PLACE_ID` |
| GSI1SK | `PLACE_ID#{place_id}` |
| place_id | Google Places place_id |
| places_response | JSON-encoded API response |
| last_updated | ISO timestamp |
| query_count | Access count for analytics |
| time_to_live | TTL timestamp for expiration |

## Cache Exclusions

The following are NOT cached to maintain data quality:

1. **Area Searches**: City/state-only queries (e.g., "Seattle, WA")
2. **Route-Level Results**: Street-only results without building numbers
3. **Incomplete Addresses**: Results without street numbers
4. **Invalid Responses**: NO_RESULTS and INVALID status responses

## Testing

```bash
cd receipt_places

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=receipt_places --cov-report=term-missing

# Run specific test file
pytest tests/test_cache.py -v
```

The tests use:
- **moto**: For mocking DynamoDB
- **responses**: For mocking HTTP requests to Places API

## Migration from receipt_label

If migrating from `receipt_label.data.places_api.PlacesAPI`:

```python
# Old (receipt_label)
from receipt_label.data.places_api import PlacesAPI
client = PlacesAPI(api_key="...", client_manager=manager)
result = client.find_place_by_phone(phone)

# New (receipt_places)
from receipt_places import PlacesClient
client = PlacesClient(api_key="...")
result = client.search_by_phone(phone)
```

Key differences:
- Standalone package with no `receipt_label` dependency
- Simplified configuration via environment variables
- More robust error handling and logging
- Improved cache exclusion logic

## API Reference

### PlacesClient

| Method | Description | Cached |
|--------|-------------|--------|
| `search_by_phone(phone)` | Search by phone number | ✅ |
| `search_by_address(address)` | Search by address | ✅ |
| `search_by_text(query)` | Free-form text search | ❌ |
| `search_nearby(lat, lng, radius)` | Nearby places search | ❌ |
| `get_place_details(place_id)` | Get full place details | ❌ |
| `autocomplete_address(input)` | Address autocomplete | ❌ |

### CacheManager

| Method | Description |
|--------|-------------|
| `get(search_type, search_value)` | Get cached response |
| `put(search_type, search_value, place_id, response)` | Cache response |
| `delete(search_type, search_value)` | Delete cache entry |
| `get_by_place_id(place_id)` | Lookup by place_id (GSI1) |
| `get_stats()` | Get cache statistics |

## License

MIT

