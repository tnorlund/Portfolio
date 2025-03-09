import re
from typing import Dict, List, Optional


def normalize_address(address: str) -> str:
    """Normalize an address for comparison.

    Args:
        address: Address string to normalize

    Returns:
        Normalized address string
    """
    if not address:
        return ""

    # Convert to lowercase
    address = address.lower()

    # Remove punctuation
    address = re.sub(r"[^\w\s]", " ", address)

    # Normalize whitespace
    address = " ".join(address.split())

    # Normalize common abbreviations
    abbreviations = {
        r"(?<=\s)ave(?=\s|$)": "avenue",
        r"(?<=\s)st(?=\s|$)": "street",
        r"(?<=\s)rd(?=\s|$)": "road",
        "blvd": "boulevard",  # unique enough to use word boundary
        r"(?<=\s)ln(?=\s|$)": "lane",
        r"(?<=\s)dr(?=\s|$)": "drive",
        r"(?<=\s)ct(?=\s|$)": "court",
        "cir": "circle",  # unique enough to use word boundary
        r"(?<=\s)pl(?=\s|$)": "place",
        r"(?<=\s)ste(?=\s|$)": "suite",
        r"(?<=\s)apt(?=\s|$)": "apartment",
        "po box": "post office box",
        "p.o. box": "post office box",
        "pobox": "post office box",
    }

    # Add directional abbreviations
    directions = {
        "n": "north",
        "s": "south",
        "e": "east",
        "w": "west",
        "ne": "northeast",
        "nw": "northwest",
        "se": "southeast",
        "sw": "southwest",
    }

    # First normalize street types (blvd, st, etc)
    for abbr, full in abbreviations.items():
        # Use regex with word boundaries for unique abbreviations, whitespace bounds for others
        if any(pattern in abbr for pattern in ["(?<=\\s)", "(?=\\s|$)"]):
            address = re.sub(abbr, full, address)
        else:
            address = re.sub(rf"\b{abbr}\b", full, address)

    # Then normalize directions, being careful with word boundaries
    for abbr, full in directions.items():
        # Only replace if it's a standalone word or at the start followed by a street type
        address = re.sub(rf"\b{abbr}\b(?=\s+(?:street|avenue|road|boulevard|lane|drive|court|circle|place))", full, address)
        # Also handle cases where it's a standalone word
        address = re.sub(rf"^{abbr}\b|\b{abbr}$", full, address)

    # Remove common words that aren't part of the address
    common_words = [
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
    ]
    words = address.split()
    words = [w for w in words if w not in common_words]
    address = " ".join(words)

    return address


def parse_address(address: str) -> Dict[str, str]:
    """Parse an address into its components.

    Args:
        address: Address string to parse

    Returns:
        Dict containing address components
    """
    if not address:
        return {}

    # Initialize components
    components = {
        "street_number": None,
        "street_name": None,
        "street_type": None,
        "city": None,
        "state": None,
        "zip_code": None,
        "country": None,
        "unit": None,
    }

    # Split address into parts
    parts = address.split(",")

    # Parse street address
    if parts:
        street_part = parts[0].strip()

        # Extract street number
        street_number_match = re.match(r"^\d+", street_part)
        if street_number_match:
            components["street_number"] = street_number_match.group()
            street_part = street_part[len(components["street_number"]) :].strip()

        # Extract unit/suite if present
        unit_match = re.search(
            r"(?:suite|ste|apt|apartment)\s+(\w+)", street_part.lower()
        )
        if unit_match:
            components["unit"] = unit_match.group(1)
            street_part = re.sub(
                r"(?:suite|ste|apt|apartment)\s+\w+",
                "",
                street_part,
                flags=re.IGNORECASE,
            ).strip()

        # Extract street name and type
        street_words = street_part.split()
        if street_words:
            # Last word is usually the street type
            components["street_type"] = street_words[-1]
            components["street_name"] = " ".join(street_words[:-1])

    # Parse city, state, zip
    if len(parts) > 1:
        city_state_zip = parts[1].strip()

        # Extract zip code
        zip_match = re.search(r"\b\d{5}(?:-\d{4})?\b", city_state_zip)
        if zip_match:
            components["zip_code"] = zip_match.group()
            city_state_zip = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", city_state_zip).strip()

        # Split remaining into city and state
        city_state_parts = city_state_zip.split()
        if len(city_state_parts) >= 2:
            components["state"] = city_state_parts[-1]
            components["city"] = " ".join(city_state_parts[:-1])

    # Parse country if present
    if len(parts) > 2:
        components["country"] = parts[2].strip()

    return components


def format_address(components: Dict[str, str]) -> str:
    """Format address components into a string.

    Args:
        components: Dict containing address components

    Returns:
        Formatted address string
    """
    parts = []

    # Add street address
    street_parts = []
    if components.get("street_number"):
        street_parts.append(components["street_number"])
    if components.get("street_name"):
        street_parts.append(components["street_name"])
    if components.get("street_type"):
        street_parts.append(components["street_type"])
    if components.get("unit"):
        street_parts.append(f"Suite {components['unit']}")

    if street_parts:
        parts.append(" ".join(street_parts))

    # Add city, state, zip
    city_state_zip = []
    if components.get("city"):
        city_state_zip.append(components["city"])
    if components.get("state"):
        city_state_zip.append(components["state"])
    if components.get("zip_code"):
        city_state_zip.append(components["zip_code"])

    if city_state_zip:
        parts.append(", ".join(city_state_zip))

    # Add country
    if components.get("country"):
        parts.append(components["country"])

    return ", ".join(parts)


def compare_addresses(addr1: str, addr2: str) -> float:
    """Compare two addresses and return a similarity score.

    Args:
        addr1: First address to compare
        addr2: Second address to compare

    Returns:
        Similarity score between 0 and 1
    """
    # Normalize both addresses
    norm1 = normalize_address(addr1)
    norm2 = normalize_address(addr2)

    # Parse both addresses
    comp1 = parse_address(addr1)
    comp2 = parse_address(addr2)

    # Compare components
    matches = 0
    total = 0

    for key in [
        "street_number",
        "street_name",
        "street_type",
        "city",
        "state",
        "zip_code",
    ]:
        if comp1.get(key) and comp2.get(key):
            total += 1
            if comp1[key].lower() == comp2[key].lower():
                matches += 1

    # If no components matched, fall back to string similarity
    if total == 0:
        # Simple word overlap
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        overlap = len(words1.intersection(words2))
        total = max(len(words1), len(words2))
        return overlap / total if total > 0 else 0.0

    return matches / total
