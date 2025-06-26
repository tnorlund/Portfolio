import re
from typing import Dict, Optional


def normalize_address(address: str) -> str:
    """Normalize an address string.

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

    # Replace common abbreviations
    replacements = {
        r"\bst\b": "street",
        r"\brd\b": "road",
        r"\bave\b": "avenue",
        r"\bblvd\b": "boulevard",
        r"\bdr\b": "drive",
        r"\bcir\b": "circle",
        r"\bln\b": "lane",
        r"\bpl\b": "place",
        r"\bct\b": "court",
        r"\bw\b": "west",
        r"\be\b": "east",
        r"\bn\b": "north",
        r"\bs\b": "south",
        r"\bne\b": "northeast",
        r"\bnw\b": "northwest",
        r"\bse\b": "southeast",
        r"\bsw\b": "southwest",
        r"\bste\b": "suite",
        r"\bapt\b": "apartment",
        r"\bunit\b": "unit",
    }

    for pattern, replacement in replacements.items():
        address = re.sub(pattern, replacement, address)

    # Normalize whitespace
    address = " ".join(address.split())

    return address


def parse_address(address: str) -> Dict[str, Optional[str]]:
    """Parse an address string into its components."""
    if not address:
        return {}

    components = {
        "street_number": None,
        "street_name": None,
        "street_type": None,
        "unit": None,
        "city": None,
        "state": None,
        "zip_code": None,
        "country": None,
    }

    # First extract unit/suite if present
    unit_match = re.search(
        r"(?:suite|ste|apt|apartment|unit)\s+(\w+)", address.lower()
    )
    if unit_match:
        components["unit"] = unit_match.group(1)

    # Split address into parts
    parts = [p.strip() for p in address.split(",")]

    # Parse street address from first part
    street_match = re.match(r"(\d+)\s+(\w+)\s+(\w+)", parts[0])
    if street_match:
        components["street_number"] = street_match.group(1)
        components["street_name"] = street_match.group(2)
        components["street_type"] = street_match.group(3)

    # Parse city, state, zip from remaining parts
    for part in parts[1:]:
        part = part.strip()
        # Check for unit/suite first
        if re.search(r"(?:suite|ste|apt|apartment|unit)", part.lower()):
            continue
        # Check for state and zip
        state_zip_match = re.match(r"([A-Z]{2})\s+(\d{5})", part)
        if state_zip_match:
            components["state"] = state_zip_match.group(1)
            components["zip_code"] = state_zip_match.group(2)
            continue
        # Check for country
        if part.strip().upper() == "USA":
            components["country"] = part.strip()
            continue
        # If none of the above, assume it's the city
        if not components["city"]:
            components["city"] = part.strip()

    return components


def format_address(components: Dict[str, str]) -> str:
    """Format address components into a string."""
    parts = []

    # Street address
    street_parts = []
    if components.get("street_number"):
        street_parts.append(components["street_number"])
    if components.get("street_name"):
        street_parts.append(components["street_name"])
    if components.get("street_type"):
        street_parts.append(components["street_type"])
    if street_parts:
        parts.append(" ".join(street_parts))

    # Unit/Suite
    if components.get("unit"):
        if parts:
            parts[-1] = f"{parts[-1]} Suite {components['unit']}"
        else:
            parts.append(f"Suite {components['unit']}")

    # City, State, Zip
    location_parts = []
    if components.get("city"):
        location_parts.append(components["city"])
    if components.get("state"):
        state_zip = components["state"]
        if components.get("zip_code"):
            state_zip += f" {components['zip_code']}"
        location_parts.append(state_zip)
    if location_parts:
        parts.append(", ".join(location_parts))

    # Country
    if components.get("country"):
        parts.append(components["country"])

    return ", ".join(parts)


def compare_addresses(addr1: str, addr2: str) -> float:
    """Compare two addresses and return similarity score.

    Args:
        addr1: First address
        addr2: Second address

    Returns:
        Similarity score between 0 and 1
    """
    if not addr1 or not addr2:
        return 0.0

    # Normalize both addresses
    addr1_norm = normalize_address(addr1)
    addr2_norm = normalize_address(addr2)

    # Parse both addresses
    addr1_components = parse_address(addr1)
    addr2_components = parse_address(addr2)

    # Compare components
    matches = 0
    total = 0

    # Required components that must match
    required = ["street_number", "street_name", "city", "state"]
    for component in required:
        if addr1_components.get(component) and addr2_components.get(component):
            total += 2
            if (
                addr1_components[component].lower()
                == addr2_components[component].lower()
            ):
                matches += 2

    # Optional components
    optional = ["street_type", "unit", "zip_code", "country"]
    for component in optional:
        if addr1_components.get(component) or addr2_components.get(component):
            total += 1
            if addr1_components.get(component) == addr2_components.get(
                component
            ):
                matches += 1

    # If no components matched, fall back to string similarity
    if total == 0:
        # Simple word overlap
        words1 = set(addr1_norm.split())
        words2 = set(addr2_norm.split())
        overlap = len(words1.intersection(words2))
        total = max(len(words1), len(words2))
        return overlap / total if total > 0 else 0.0

    # Boost score if street number and name match
    if addr1_components.get("street_number") == addr2_components.get(
        "street_number"
    ) and addr1_components.get("street_name") == addr2_components.get(
        "street_name"
    ):
        matches += 4
        total += 4

    # Boost score if city and state match
    if addr1_components.get("city") == addr2_components.get(
        "city"
    ) and addr1_components.get("state") == addr2_components.get("state"):
        matches += 4
        total += 4

    # Boost score if zip code matches
    if addr1_components.get("zip_code") == addr2_components.get("zip_code"):
        matches += 2
        total += 2

    # If street type is similar (e.g., St vs Street), give partial credit
    if addr1_components.get("street_type") and addr2_components.get(
        "street_type"
    ):
        st1 = normalize_address(addr1_components["street_type"])
        st2 = normalize_address(addr2_components["street_type"])
        if st1 == st2:
            matches += 2
            total += 2

    # If all required components match exactly, but one has a unit and the other doesn't,
    # return a high but not perfect score
    if (
        addr1_components.get("street_number")
        == addr2_components.get("street_number")
        and addr1_components.get("street_name")
        == addr2_components.get("street_name")
        and addr1_components.get("city") == addr2_components.get("city")
        and addr1_components.get("state") == addr2_components.get("state")
    ):
        if bool(addr1_components.get("unit")) != bool(
            addr2_components.get("unit")
        ):
            return 0.9
        return 1.0

    return matches / total if total > 0 else 0.0
