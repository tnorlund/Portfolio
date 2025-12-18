"""
Geospatial utility functions for DynamoDB queries.

Provides helpers for geographic calculations including geohash encoding
for spatial indexing and distance calculations.

Note: These utilities are designed for future PlaceCluster merchant deduplication
(finding receipts for the same merchant at nearby locations). Currently, geohash
is calculated and stored in ReceiptPlace, but spatial queries (GSI4) are deferred
until PlaceCluster is activated. The query methods exist in _receipt_place.py but
are not yet called.
"""

from typing import Optional, Tuple


def calculate_geohash(latitude: float, longitude: float, precision: int = 6) -> str:
    """
    Calculate geohash for a geographic point.

    Geohash is a hierarchical spatial data structure which subdivides space
    into buckets of grid shape. It's useful for indexing geographic data
    in DynamoDB using GSI4 for spatial queries.

    Precision levels and approximate areas:
    - 6: ~1.2 km x 1.2 km
    - 7: ~152 m x 152 m
    - 8: ~38 m x 19 m
    - 9: ~4.8 m x 4.8 m
    - 10: ~1.2 m x 0.6 m

    Args:
        latitude: Latitude in decimal degrees (-90 to 90)
        longitude: Longitude in decimal degrees (-180 to 180)
        precision: Geohash precision level (default 6, range 1-12)

    Returns:
        Geohash string of the specified precision

    Raises:
        ValueError: If coordinates are out of valid ranges or precision is invalid

    Example:
        ```python
        from receipt_dynamo.utils.geospatial import calculate_geohash

        # NYC coordinates
        geohash = calculate_geohash(40.7128, -74.0060, precision=6)
        # Returns something like "dr5regr" (exact value depends on implementation)
        ```

    References:
        - Geohash: https://en.wikipedia.org/wiki/Geohash
        - Precision levels: https://en.wikipedia.org/wiki/Geohash#Cell_sizes
    """
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude must be between -90 and 90, got {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude must be between -180 and 180, got {longitude}")
    if not 1 <= precision <= 12:
        raise ValueError(f"precision must be between 1 and 12, got {precision}")

    try:
        import geohash2 as geohash
    except ImportError:
        # Fallback implementation if geohash2 is not available
        return _calculate_geohash_fallback(latitude, longitude, precision)

    return geohash.encode(latitude, longitude, precision)


def _calculate_geohash_fallback(
    latitude: float, longitude: float, precision: int = 6
) -> str:
    """
    Fallback geohash calculation without external dependency.

    This is a basic implementation that provides reasonable spatial grouping
    for ~1km precision (precision=6). For finer precision, use geohash2 library.

    Note: This simplified implementation may not perfectly match standard geohash.
    For production use with spatial queries, install geohash2:
        pip install geohash2

    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        precision: Desired precision (1-12, though precision > 6 may not work well)

    Returns:
        A string representing the geohash
    """
    # Simple grid-based approach: round coordinates to grid cells
    # Precision 6 uses 0.01 degree cells (approximately 1.1km at equator)
    decimals = precision  # Rough approximation

    lat_rounded = round(latitude, decimals)
    lng_rounded = round(longitude, decimals)

    # Format as geohash-like string
    # Convert to positive coordinates and then to base36 for compactness
    lat_offset = lat_rounded + 90  # 0-180
    lng_offset = lng_rounded + 180  # 0-360

    # Create a simple hash string
    geohash_str = f"{lat_offset:08.4f}{lng_offset:09.4f}".replace(".", "").replace("-", "")

    # Return first 'precision' characters to match expected behavior
    return geohash_str[:precision]


def nearby_geohashes(
    latitude: float, longitude: float, precision: int = 6
) -> list[str]:
    """
    Get geohashes of neighboring cells for range queries.

    When searching for places near a location, you need to query multiple
    geohash cells to account for the boundaries. This function returns the
    current cell plus all neighbors.

    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        precision: Geohash precision level

    Returns:
        List of geohash strings including the current cell and neighbors

    Example:
        ```python
        from receipt_dynamo.utils.geospatial import nearby_geohashes

        # Get all cells within ~2km of NYC
        cells = nearby_geohashes(40.7128, -74.0060, precision=6)
        # Returns list like ['dr5regr', 'dr5regq', ...] (all neighbors)

        # Use to search across multiple cells:
        for geohash in cells:
            results = dynamo.get_receipt_places_by_geohash(geohash)
        ```
    """
    try:
        import geohash2 as geohash
        center_hash = geohash.encode(latitude, longitude, precision)
        return geohash.neighbors(center_hash)
    except ImportError:
        # Fallback: return just the current cell
        # For proper neighbor calculation, install geohash2
        center_hash = _calculate_geohash_fallback(latitude, longitude, precision)
        return [center_hash]


def haversine_distance(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """
    Calculate great-circle distance between two geographic points.

    Uses the Haversine formula to calculate the shortest distance between
    two points on the Earth's surface, accounting for Earth's curvature.

    Args:
        lat1: Latitude of first point in decimal degrees
        lng1: Longitude of first point in decimal degrees
        lat2: Latitude of second point in decimal degrees
        lng2: Longitude of second point in decimal degrees

    Returns:
        Distance in kilometers

    Example:
        ```python
        from receipt_dynamo.utils.geospatial import haversine_distance

        # Distance between NYC and LA
        distance = haversine_distance(
            40.7128, -74.0060,  # NYC
            34.0522, -118.2437   # LA
        )
        # Returns approximately 3944 km
        ```

    References:
        - Haversine formula: https://en.wikipedia.org/wiki/Haversine_formula
    """
    from math import asin, cos, radians, sin, sqrt

    # Earth radius in kilometers
    EARTH_RADIUS_KM = 6371

    # Convert to radians
    lat1_rad = radians(lat1)
    lng1_rad = radians(lng1)
    lat2_rad = radians(lat2)
    lng2_rad = radians(lng2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlng = lng2_rad - lng1_rad

    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlng / 2) ** 2
    c = 2 * asin(sqrt(a))

    distance = EARTH_RADIUS_KM * c
    return distance


def bounding_box_from_radius(
    latitude: float, longitude: float, radius_km: float
) -> Tuple[float, float, float, float]:
    """
    Calculate bounding box around a point for geographic range.

    Returns a bounding box (min_lat, max_lat, min_lng, max_lng) that
    approximates the area within radius_km of the given point.

    This is useful for filtering ReceiptPlace records before calculating
    exact Haversine distances.

    Args:
        latitude: Center latitude in decimal degrees
        longitude: Center longitude in decimal degrees
        radius_km: Radius in kilometers

    Returns:
        Tuple of (min_lat, max_lat, min_lng, max_lng)

    Example:
        ```python
        from receipt_dynamo.utils.geospatial import bounding_box_from_radius

        # Get bounding box for 1km around NYC
        min_lat, max_lat, min_lng, max_lng = bounding_box_from_radius(
            40.7128, -74.0060, radius_km=1.0
        )
        # Use to filter: latitude >= min_lat AND latitude <= max_lat, etc.
        ```
    """
    # Approximate degrees per km at Earth's surface
    # More precise near equator, less precise near poles
    DEGREES_PER_KM_LAT = 1 / 111.32  # Latitude (always ~111km per degree)
    DEGREES_PER_KM_LNG = 1 / 111.32 * cos(radians(latitude))  # Longitude (varies by latitude)

    lat_offset = radius_km * DEGREES_PER_KM_LAT
    lng_offset = radius_km * DEGREES_PER_KM_LNG

    min_lat = latitude - lat_offset
    max_lat = latitude + lat_offset
    min_lng = longitude - lng_offset
    max_lng = longitude + lng_offset

    return (min_lat, max_lat, min_lng, max_lng)
