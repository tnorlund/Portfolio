# Future: Spatial Queries for ReceiptPlace

This document describes how to implement geohash-based spatial queries for `ReceiptPlace` entities if needed in the future.

## Background

The `ReceiptPlace` entity stores geographic coordinates (`latitude`, `longitude`) from Google Places API. Previously, there was partial implementation for geohash-based spatial indexing (GSI4), but it was removed because:

1. GSI4 was never provisioned in the DynamoDB infrastructure
2. The `get_receipt_places_by_geohash()` query method would fail without the index
3. The `geohash2` dependency added installation complexity without providing value

## When to Implement Spatial Queries

Consider adding spatial queries if you need:

- **Nearby place detection**: Find receipts from merchants within a geographic area
- **Duplicate detection**: Identify potential duplicate merchants by proximity
- **Regional analytics**: Aggregate receipt data by geographic region
- **Map visualization**: Efficiently query places for map-based UIs

## Implementation Guide

### Step 1: Add the geohash2 Dependency

```toml
# In pyproject.toml
dependencies = [
    "requests",
    "boto3",
    "geohash2",  # Add this
]
```

### Step 2: Create Geospatial Utilities

Create `receipt_dynamo/utils/geospatial.py`:

```python
"""Geospatial utilities for spatial indexing."""

import geohash2 as geohash


def calculate_geohash(latitude: float, longitude: float, precision: int = 6) -> str:
    """
    Calculate geohash for a geographic point.

    Args:
        latitude: Decimal degrees latitude (-90 to 90)
        longitude: Decimal degrees longitude (-180 to 180)
        precision: Geohash precision (default 6 = ~1.2km cells)

    Returns:
        Geohash string

    Precision reference:
        - 4: ~39km x 20km
        - 5: ~4.9km x 4.9km
        - 6: ~1.2km x 0.6km (recommended for merchant queries)
        - 7: ~153m x 153m
        - 8: ~38m x 19m
    """
    if not (-90.0 <= latitude <= 90.0):
        raise ValueError(f"latitude out of range: {latitude}")
    if not (-180.0 <= longitude <= 180.0):
        raise ValueError(f"longitude out of range: {longitude}")

    return geohash.encode(latitude, longitude, precision)


def nearby_geohashes(latitude: float, longitude: float, precision: int = 6) -> list[str]:
    """
    Get geohashes of neighboring cells for range queries.

    For accurate spatial queries, you should query multiple neighboring
    geohash cells to account for boundary effects.

    Returns:
        List of 9 geohash strings (center + 8 neighbors)
    """
    center_hash = geohash.encode(latitude, longitude, precision)
    neighbors = geohash.neighbors(center_hash)
    return [center_hash] + list(neighbors.values())
```

### Step 3: Add Geohash Field to ReceiptPlace

In `receipt_dynamo/entities/receipt_place.py`:

```python
from receipt_dynamo.utils.geospatial import calculate_geohash

@dataclass
class ReceiptPlace:
    # ... existing fields ...

    # Add geohash field
    geohash: str = ""  # Precision 6 (~1km cells)

    def __post_init__(self):
        # ... existing validation ...

        # Auto-calculate geohash from coordinates
        if self.latitude is not None and self.longitude is not None:
            if not self.geohash:
                try:
                    self.geohash = calculate_geohash(
                        self.latitude, self.longitude, precision=6
                    )
                except ValueError as e:
                    logger.warning(f"Failed to calculate geohash: {e}")

    @property
    def gsi4_key(self) -> dict[str, dict[str, str]]:
        """Get GSI4 key for spatial queries."""
        if not self.geohash:
            return {}
        return {
            "GSI4PK": {"S": f"GEOHASH#{self.geohash}"},
            "GSI4SK": {"S": f"PLACE#{self.place_id}"},
        }
```

### Step 4: Provision GSI4 in Infrastructure

In `infra/dynamo_db.py`, add GSI4 to the table definition:

```python
global_secondary_indexes=[
    # ... existing GSIs ...
    aws.dynamodb.TableGlobalSecondaryIndexArgs(
        name="GSI4",
        hash_key="GSI4PK",
        range_key="GSI4SK",
        projection_type="ALL",
    ),
],
```

Also add the attribute definitions:

```python
attributes=[
    # ... existing attributes ...
    aws.dynamodb.TableAttributeArgs(name="GSI4PK", type="S"),
    aws.dynamodb.TableAttributeArgs(name="GSI4SK", type="S"),
],
```

### Step 5: Add Query Method

In `receipt_dynamo/data/_receipt_place.py`:

```python
def get_receipt_places_by_geohash(
    self,
    geohash: str,
    limit: Optional[int] = None,
    last_evaluated_key: dict | None = None,
) -> Tuple[List[ReceiptPlace], dict | None]:
    """
    Retrieve ReceiptPlace records by geohash for spatial queries.

    Args:
        geohash: The geohash to query (precision 6-7 for ~1km cells)
        limit: Maximum records to retrieve
        last_evaluated_key: Pagination key

    Returns:
        Tuple of (places, next_page_key)
    """
    if not geohash or len(geohash) < 6:
        raise EntityValidationError("geohash must be at least 6 characters")

    return self._query_entities(
        index_name="GSI4",
        key_condition_expression="GSI4PK = :pk",
        expression_attribute_values={":pk": {"S": f"GEOHASH#{geohash}"}},
        converter_func=item_to_receipt_place,
        limit=limit,
        last_evaluated_key=last_evaluated_key,
    )
```

### Step 6: Query Nearby Places

```python
from receipt_dynamo.utils.geospatial import nearby_geohashes

def find_nearby_places(
    client: DynamoClient,
    latitude: float,
    longitude: float,
    precision: int = 6,
) -> List[ReceiptPlace]:
    """Find all places near a geographic point."""
    all_places = []

    # Query center cell and all neighbors
    for gh in nearby_geohashes(latitude, longitude, precision):
        places, _ = client.get_receipt_places_by_geohash(gh)
        all_places.extend(places)

    return all_places
```

## Cost Considerations

- **GSI Storage**: GSI4 will store all projected attributes, increasing storage costs
- **Write Capacity**: Each write to ReceiptPlace will also write to GSI4
- **Query Efficiency**: Geohash queries are very efficient (single partition key lookup)

## Alternatives to Geohash

If geohash precision doesn't meet your needs, consider:

1. **S2 Geometry**: Google's S2 library offers hierarchical spatial indexing
2. **H3**: Uber's hexagonal hierarchical spatial index
3. **PostGIS**: If you need complex spatial queries, consider a PostgreSQL sidecar

## Testing

Add integration tests that:

1. Create ReceiptPlace entities with coordinates
2. Verify geohash is auto-calculated
3. Query by geohash and verify results
4. Test boundary cases (places on geohash cell edges)
