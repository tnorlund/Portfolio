"""
Merchant Harmonizer V2 - Place ID Based Grouping
=================================================

Purpose
-------
Ensures metadata consistency across receipts sharing the same Google Place ID.

Key Insight
-----------
Receipts with the same `place_id` MUST have consistent metadata (merchant name,
address, phone). Any inconsistency indicates a data quality issue:
- Typos or OCR errors
- Wrong place_id assignment
- Missing data

How It Works
------------
1. **Group by place_id**: Load all ReceiptMetadata and group by place_id
2. **Compute consensus**: For each group, find the most common value for each field
3. **Validate with Google**: Optionally confirm consensus against Google Places API
4. **Identify outliers**: Flag receipts that differ from consensus
5. **Apply fixes**: Update canonical fields in DynamoDB (preserving originals)

What Gets Updated
-----------------
The harmonizer updates the **main fields** to ensure consistency:
- `merchant_name` ← Consensus/Google merchant name
- `address` ← Consensus/Google address
- `phone_number` ← Consensus/Google phone

All receipts with the same place_id will have identical metadata after harmonization.

Why V2 is Better Than V1
------------------------
V1 used ChromaDB semantic similarity to find "related" receipts. This was flawed
because semantic similarity of OCR text does NOT mean same merchant. A Home Depot
receipt in Thousand Oaks might find a Sprouts receipt nearby (similar address text).

V2 groups by place_id, which is the actual source of truth for merchant identity.

Usage
-----
```python
from receipt_agent.tools.harmonizer_v2 import MerchantHarmonizerV2

harmonizer = MerchantHarmonizerV2(dynamo_client, places_client)
report = await harmonizer.harmonize_all()
harmonizer.print_summary(report)

# Apply fixes (updates canonical fields)
await harmonizer.apply_fixes(dry_run=False)
```

Example Output
--------------
```
HARMONIZER V2 REPORT (Place ID Grouping)
======================================================================
Total receipts: 571
  With place_id: 506
  Without place_id: 65 (cannot harmonize)
Place ID groups: 155
  Google validated: 152

Consistency (receipts with place_id):
  ✅ Consistent: 364 (71.9%)
  ⚠️  Needs update: 142 (28.1%)
```
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ReceiptRecord:
    """
    A single receipt's metadata.

    Attributes:
        image_id: UUID of the image containing this receipt
        receipt_id: Receipt number within the image
        merchant_name: Current merchant name (from OCR/original)
        place_id: Google Place ID
        address: Current address
        phone: Current phone number
        validation_status: Current validation status
    """
    image_id: str
    receipt_id: int
    merchant_name: Optional[str] = None
    place_id: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    validation_status: Optional[str] = None


@dataclass
class PlaceIdGroup:
    """
    A group of receipts sharing the same place_id.

    Receipts with the same place_id should have identical metadata.
    Any differences indicate data quality issues.

    Attributes:
        place_id: The Google Place ID shared by all receipts in this group
        receipts: List of receipts in this group
        consensus_*: Most common value for each field
        google_*: Values from Google Places API (if validated)
        google_validated: Whether Google Places confirmed this place_id
    """
    place_id: str
    receipts: list[ReceiptRecord] = field(default_factory=list)

    # Consensus values (most common among receipts)
    consensus_merchant_name: Optional[str] = None
    consensus_address: Optional[str] = None
    consensus_phone: Optional[str] = None

    # Google Places validation
    google_name: Optional[str] = None
    google_address: Optional[str] = None
    google_phone: Optional[str] = None
    google_validated: bool = False


@dataclass
class HarmonizerResult:
    """
    Result of harmonization for a single receipt.

    Attributes:
        image_id, receipt_id: Receipt identifier
        place_id: Google Place ID
        merchant_name: Current merchant name
        needs_update: Whether this receipt needs its canonical fields updated
        changes_needed: List of changes to apply (human-readable)
        confidence: How confident we are in the recommendation (0-100)
        group_size: Number of receipts sharing this place_id (larger = higher confidence)
        current_*: Current values in DynamoDB
        recommended_*: Values to write to canonical fields
    """
    image_id: str
    receipt_id: int
    place_id: Optional[str]
    merchant_name: str
    needs_update: bool
    changes_needed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    group_size: int = 1

    # Current values (what's in DynamoDB now)
    current_merchant_name: Optional[str] = None
    current_address: Optional[str] = None
    current_phone: Optional[str] = None

    # Recommended values (to write to canonical fields)
    recommended_merchant_name: Optional[str] = None
    recommended_address: Optional[str] = None
    recommended_phone: Optional[str] = None


@dataclass
class UpdateResult:
    """
    Result of applying fixes to DynamoDB.

    Attributes:
        total_processed: Number of receipts processed
        total_updated: Number of receipts actually updated
        total_skipped: Number of receipts skipped (already consistent)
        total_failed: Number of receipts that failed to update
        errors: List of error messages for failed updates
    """
    total_processed: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    errors: list[str] = field(default_factory=list)


class MerchantHarmonizerV2:
    """
    Harmonizes merchant metadata by grouping receipts by place_id.

    This class identifies inconsistencies in receipt metadata and can apply
    fixes by updating the canonical fields in DynamoDB.

    Key Features:
    - Groups receipts by place_id (not semantic similarity)
    - Computes consensus values within each group
    - Validates against Google Places API
    - Applies fixes to canonical fields (preserving originals)

    Example:
        ```python
        harmonizer = MerchantHarmonizerV2(dynamo_client, places_client)

        # Analyze all receipts
        report = await harmonizer.harmonize_all()
        harmonizer.print_summary(report)

        # Apply fixes (dry run first)
        result = await harmonizer.apply_fixes(dry_run=True)
        print(f"Would update {result.total_updated} receipts")

        # Actually apply fixes
        result = await harmonizer.apply_fixes(dry_run=False)
        print(f"Updated {result.total_updated} receipts")
        ```
    """

    def __init__(
        self,
        dynamo_client: Any,
        places_client: Optional[Any] = None,
    ):
        """
        Initialize the harmonizer.

        Args:
            dynamo_client: DynamoDB client with list_receipt_metadatas() method
            places_client: Optional Google Places client for validation
        """
        self.dynamo = dynamo_client
        self.places = places_client
        self._place_id_groups: dict[str, PlaceIdGroup] = {}
        self._no_place_id_receipts: list[ReceiptRecord] = []
        self._last_report: Optional[dict[str, Any]] = None

    def load_all_receipts(self) -> int:
        """
        Load all receipt metadata from DynamoDB and group by place_id.

        Returns:
            Total number of receipts loaded
        """
        logger.info("Loading all receipt metadata from DynamoDB...")

        self._place_id_groups = {}
        self._no_place_id_receipts = []
        total = 0

        try:
            # Get all receipt metadata (paginated)
            metadatas = []
            last_key = None
            while True:
                batch, last_key = self.dynamo.list_receipt_metadatas(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                metadatas.extend(batch)
                if not last_key:
                    break

            for meta in metadatas:
                receipt = ReceiptRecord(
                    image_id=meta.image_id,
                    receipt_id=meta.receipt_id,
                    merchant_name=meta.merchant_name,
                    place_id=meta.place_id,
                    address=meta.address,
                    phone=meta.phone_number,
                    validation_status=getattr(meta, 'validation_status', None),
                )

                if receipt.place_id:
                    if receipt.place_id not in self._place_id_groups:
                        self._place_id_groups[receipt.place_id] = PlaceIdGroup(
                            place_id=receipt.place_id
                        )
                    self._place_id_groups[receipt.place_id].receipts.append(receipt)
                else:
                    self._no_place_id_receipts.append(receipt)

                total += 1

            logger.info(
                f"Loaded {total} receipts: "
                f"{len(self._place_id_groups)} place_id groups, "
                f"{len(self._no_place_id_receipts)} without place_id"
            )

        except Exception as e:
            logger.error(f"Failed to load receipts: {e}")
            raise

        return total

    def _normalize_merchant_name(self, name: str) -> str:
        """
        Normalize a merchant name for comparison (case-insensitive).

        Returns the normalized key for grouping.
        """
        if not name:
            return ""
        return name.lower().strip()

    def _pick_best_name_variant(self, variants: list[str]) -> str:
        """
        Pick the best name variant from a list of case variations.

        Prefers:
        1. Title case (e.g., "Vons")
        2. Mixed case (e.g., "Trader Joe's")
        3. All caps (e.g., "VONS")
        4. All lowercase (e.g., "vons")
        """
        if not variants:
            return ""

        # Prefer title case (first letter of each word capitalized)
        for variant in variants:
            words = variant.split()
            if words:
                # Check if it's title case (first letter of each word is uppercase)
                if all(word[0].isupper() if word else False for word in words):
                    # But not all caps (all letters uppercase)
                    if not all(word.isupper() for word in words if word):
                        return variant

        # Prefer mixed case over all caps or all lowercase
        for variant in variants:
            if not variant.isupper() and not variant.islower():
                return variant

        # Prefer all caps over all lowercase
        for variant in variants:
            if variant.isupper():
                return variant

        # Fall back to first variant
        return variants[0]

    def _compute_consensus(self, group: PlaceIdGroup) -> None:
        """
        Compute consensus values for a place_id group.

        The consensus is the most common value for each field among all
        receipts in the group. Merchant names are normalized (case-insensitive)
        before counting to ensure consistency.
        """
        if not group.receipts:
            return

        # For merchant names: normalize by lowercase, then pick best variant
        merchant_normalized: dict[str, list[str]] = defaultdict(list)
        merchant_counts: dict[str, int] = defaultdict(int)

        address_counts: dict[str, int] = defaultdict(int)
        phone_counts: dict[str, int] = defaultdict(int)

        for r in group.receipts:
            if r.merchant_name:
                normalized = self._normalize_merchant_name(r.merchant_name)
                if normalized:
                    merchant_normalized[normalized].append(r.merchant_name)
                    merchant_counts[normalized] += 1
            if r.address:
                address_counts[r.address] += 1
            if r.phone:
                phone_counts[r.phone] += 1

        # Most common normalized name = consensus
        # Pick the best variant (prefer title case)
        if merchant_counts:
            most_common_normalized = max(
                merchant_counts.items(), key=lambda x: x[1]
            )[0]
            variants = merchant_normalized[most_common_normalized]
            group.consensus_merchant_name = self._pick_best_name_variant(variants)

        if address_counts:
            group.consensus_address = max(
                address_counts.items(), key=lambda x: x[1]
            )[0]

        if phone_counts:
            group.consensus_phone = max(
                phone_counts.items(), key=lambda x: x[1]
            )[0]

    def _is_address_like(self, name: Optional[str]) -> bool:
        """
        Check if a merchant name looks like an address rather than a business name.

        Address-like names typically:
        - Start with a number
        - Contain street indicators (St, Ave, Blvd, etc.)
        - Are just street addresses without business names
        """
        if not name:
            return False

        name_lower = name.lower().strip()

        # Check if it starts with a number (common for addresses)
        if name_lower and name_lower[0].isdigit():
            # Check for street indicators
            street_indicators = [
                'st', 'street', 'ave', 'avenue', 'blvd', 'boulevard',
                'rd', 'road', 'dr', 'drive', 'ln', 'lane', 'way',
                'ct', 'court', 'pl', 'place', 'cir', 'circle'
            ]
            if any(indicator in name_lower for indicator in street_indicators):
                return True

        return False

    def _validate_with_google(self, group: PlaceIdGroup) -> None:
        """
        Validate a place_id group against Google Places API.

        If the place_id is valid, Google will return canonical name, address,
        and phone which we can use to validate/override the consensus.

        If Google returns an address as the name, we try to find the real business
        name by searching nearby businesses at that address.
        """
        if not self.places or not group.place_id:
            return

        # Skip invalid place_ids
        if group.place_id.startswith("compaction_") or group.place_id == "null":
            logger.debug(f"Skipping invalid place_id: {group.place_id}")
            return

        try:
            # Get place details from Google
            details = self.places.get_place_details(group.place_id)

            if details:
                google_name = details.get("name")
                google_address = details.get("formatted_address")
                google_phone = (
                    details.get("formatted_phone_number") or
                    details.get("international_phone_number")
                )

                # Check if Google returned an address as the name
                if google_name and self._is_address_like(google_name):
                    logger.warning(
                        f"Google Places returned address as name for {group.place_id}: "
                        f"'{google_name}'. This might be a location, not a business."
                    )

                    # Try to find businesses at this address using nearby search
                    # This is more effective than text search for finding actual businesses
                    if google_address:
                        try:
                            # First, geocode the address to get lat/lng
                            geocode_result = self.places.search_by_address(google_address)
                            if geocode_result:
                                geometry = geocode_result.get("geometry", {})
                                location = geometry.get("location", {})
                                lat = location.get("lat")
                                lng = location.get("lng")

                                if lat and lng:
                                    # Search for nearby businesses (within 50 meters)
                                    nearby_businesses = self.places.search_nearby(
                                        lat=lat,
                                        lng=lng,
                                        radius=50,  # 50 meters - very close to the address
                                    )

                                    if nearby_businesses:
                                        # Look for a business name that matches receipt content or is likely correct
                                        # Priority:
                                        # 1. Business name that appears in receipt lines (best match)
                                        # 2. Business name that matches consensus merchant_name (if available)
                                        # 3. Business name that's not address-like and is a likely merchant (restaurant, store, etc.)
                                        # 4. First non-address-like business name

                                        best_match = None
                                        best_match_reason = None
                                        all_candidates = []

                                        # Get receipt lines for all receipts in this group to match against
                                        receipt_lines_text = []
                                        for receipt in group.receipts:
                                            try:
                                                receipt_details = self.dynamo.get_receipt_details(
                                                    receipt.image_id, receipt.receipt_id
                                                )
                                                if receipt_details and receipt_details.lines:
                                                    receipt_lines_text.extend([
                                                        line.text.lower() for line in receipt_details.lines
                                                    ])
                                            except Exception as e:
                                                logger.debug(f"Could not get receipt lines for {receipt.image_id}#{receipt.receipt_id}: {e}")

                                        # Combine all receipt lines into a single searchable string
                                        all_receipt_text = " ".join(receipt_lines_text) if receipt_lines_text else ""

                                        for business in nearby_businesses[:10]:  # Check first 10
                                            business_name = business.get("name")
                                            if not business_name or self._is_address_like(business_name):
                                                continue

                                            all_candidates.append(business_name)

                                            # Priority 1: Check if this business name matches consensus (highest priority)
                                            if group.consensus_merchant_name:
                                                if self._names_match(business_name, group.consensus_merchant_name):
                                                    best_match = business_name
                                                    best_match_reason = f"matches consensus '{group.consensus_merchant_name}'"
                                                    break

                                            # Priority 2: Check if business name appears in receipt lines
                                            if all_receipt_text:
                                                business_name_lower = business_name.lower()
                                                # Check if business name (or significant parts) appears in receipt text
                                                if business_name_lower in all_receipt_text:
                                                    if best_match is None:  # Only set if no consensus match found yet
                                                        best_match = business_name
                                                        best_match_reason = f"found in receipt content"
                                                        break

                                                # Also check if key words from business name appear
                                                # For multi-word names, require at least 2 key words to match
                                                business_words = [w for w in business_name_lower.split() if len(w) > 3]
                                                if business_words:
                                                    matching_words = [w for w in business_words if w in all_receipt_text]
                                                    # Require at least 2 matching words for multi-word names, or 1 for single-word
                                                    min_words_required = 2 if len(business_words) > 1 else 1
                                                    if len(matching_words) >= min_words_required:
                                                        if best_match is None:  # Only set if no better match found yet
                                                            best_match = business_name
                                                            best_match_reason = f"key words found in receipt content ({len(matching_words)}/{len(business_words)} words)"

                                            # Priority 3: Check if this is a likely merchant (restaurant, store, etc.)
                                            # Skip city/locality names - they're not actual businesses
                                            business_types = business.get("types", [])
                                            is_locality = any(t in business_types for t in ["locality", "political", "administrative_area_level_1", "administrative_area_level_2"])

                                            if not is_locality:
                                                likely_merchant_types = [
                                                    "restaurant", "food", "store", "shopping_mall", "cafe",
                                                    "bakery", "supermarket", "gas_station", "pharmacy",
                                                    "clothing_store", "hardware_store", "grocery_or_supermarket"
                                                ]
                                                is_likely_merchant = any(
                                                    t in business_types for t in likely_merchant_types
                                                )

                                                # Prefer likely merchants over doctors, government, etc.
                                                if is_likely_merchant and best_match is None:
                                                    best_match = business_name
                                                    best_match_reason = "likely merchant type"

                                            # Priority 4: If no best match yet, use first non-address-like, non-locality name
                                            if best_match is None and not is_locality:
                                                best_match = business_name
                                                best_match_reason = "first non-address-like business"

                                        if best_match:
                                            logger.info(
                                                f"Found business name '{best_match}' at address '{google_address[:50]}' "
                                                f"for place_id {group.place_id} ({best_match_reason})"
                                            )

                                            # If multiple candidates and we didn't find a match in receipt content, warn
                                            if len(all_candidates) > 1 and best_match_reason and "receipt content" not in best_match_reason:
                                                logger.warning(
                                                    f"Multiple businesses found at address '{google_address[:50]}' for place_id {group.place_id}. "
                                                    f"Candidates: {', '.join(all_candidates[:5])}. "
                                                    f"Selected '{best_match}' ({best_match_reason}) but receipt content check didn't find a match. "
                                                    f"Consider verifying with place_id_finder agent."
                                                )

                                            google_name = best_match
                        except Exception as e:
                            logger.debug(f"Could not find business name at address: {e}")

                    # If we still have an address-like name, keep it but log a warning
                    # The harmonizer will use consensus if available, or mark for review

                group.google_name = google_name
                group.google_address = google_address
                group.google_phone = google_phone
                group.google_validated = True

                logger.debug(
                    f"Google validated {group.place_id}: "
                    f"name={group.google_name}"
                )
            else:
                logger.warning(
                    f"Google Places returned no details for {group.place_id}"
                )

        except Exception as e:
            logger.warning(f"Google Places lookup failed for {group.place_id}: {e}")

    def analyze_group(
        self,
        group: PlaceIdGroup,
        validate_google: bool = True,
    ) -> list[HarmonizerResult]:
        """
        Analyze a single place_id group and return results for each receipt.

        Args:
            group: The place_id group to analyze
            validate_google: Whether to validate against Google Places API

        Returns:
            List of HarmonizerResult for each receipt in the group
        """
        results = []

        # Step 1: Compute consensus within the group
        self._compute_consensus(group)

        # Step 2: Validate against Google Places
        if validate_google:
            self._validate_with_google(group)

        # Step 3: Determine canonical values
        # Prefer Google if validated and matches consensus
        canonical_name = group.consensus_merchant_name
        canonical_address = group.consensus_address
        canonical_phone = group.consensus_phone

        if group.google_validated:
            # Use Google name if:
            # 1. Consensus is None/empty (no existing name), OR
            # 2. Google name matches consensus (validates existing name), OR
            # 3. Google found a real business name (not address-like) and consensus is address-like
            # BUT: Don't use Google name if it's an address and we have a real consensus name
            if group.google_name:
                google_name_is_address = self._is_address_like(group.google_name)
                consensus_is_address = self._is_address_like(group.consensus_merchant_name) if group.consensus_merchant_name else False

                # If Google returned an address as the name, prefer consensus if available
                if google_name_is_address and group.consensus_merchant_name:
                    # Only use Google's address-like name if consensus is also address-like
                    if not consensus_is_address:
                        # Consensus has a real name, use it instead
                        canonical_name = group.consensus_merchant_name
                        logger.debug(
                            f"Using consensus name '{canonical_name}' instead of Google's "
                            f"address-like name '{group.google_name}' for {group.place_id}"
                        )
                    else:
                        # Both are address-like, use Google's (more complete)
                        canonical_name = group.google_name
                elif not google_name_is_address and consensus_is_address:
                    # Google found a real business name, but consensus is address-like
                    # Prefer Google's real business name
                    canonical_name = group.google_name
                    logger.debug(
                        f"Using Google's real business name '{canonical_name}' instead of "
                        f"address-like consensus '{group.consensus_merchant_name}' for {group.place_id}"
                    )
                elif not group.consensus_merchant_name or self._names_match(
                    group.google_name, group.consensus_merchant_name
                ):
                    # Normal case: use Google name
                    canonical_name = group.google_name

            # Use Google address (usually more complete/formatted)
            if group.google_address:
                canonical_address = group.google_address

            # Use Google phone if available
            if group.google_phone:
                canonical_phone = group.google_phone

        # Step 4: Check each receipt against canonical values
        for receipt in group.receipts:
            changes = []

            # Check merchant name
            # Use exact string comparison to normalize case even when names match semantically
            if canonical_name:
                if not receipt.merchant_name:
                    changes.append(f"merchant_name: None → {canonical_name}")
                elif receipt.merchant_name != canonical_name:
                    # Even if names match semantically, normalize to canonical form
                    changes.append(
                        f"merchant_name: {receipt.merchant_name} → {canonical_name}"
                    )

            # Check address
            if (canonical_address and receipt.address and
                    not self._addresses_match(receipt.address, canonical_address)):
                changes.append(
                    f"address: {receipt.address} → {canonical_address}"
                )
            elif canonical_address and not receipt.address:
                changes.append(f"address: None → {canonical_address}")

            # Check phone
            if (canonical_phone and receipt.phone and
                    not self._phones_match(receipt.phone, canonical_phone)):
                changes.append(
                    f"phone: {receipt.phone} → {canonical_phone}"
                )
            elif canonical_phone and not receipt.phone:
                changes.append(f"phone: None → {canonical_phone}")

            # Calculate confidence based on group size and Google validation
            confidence = min(100.0, len(group.receipts) * 10)
            if group.google_validated:
                confidence = min(100.0, confidence + 30)

            results.append(HarmonizerResult(
                image_id=receipt.image_id,
                receipt_id=receipt.receipt_id,
                place_id=group.place_id,
                merchant_name=receipt.merchant_name or "Unknown",
                needs_update=len(changes) > 0,
                changes_needed=changes,
                confidence=confidence,
                group_size=len(group.receipts),
                current_merchant_name=receipt.merchant_name,
                current_address=receipt.address,
                current_phone=receipt.phone,
                recommended_merchant_name=canonical_name,
                recommended_address=canonical_address,
                recommended_phone=canonical_phone,
            ))

        return results

    def _names_match(self, name1: Optional[str], name2: Optional[str]) -> bool:
        """
        Check if two merchant names are essentially the same.

        Handles:
        - Case differences: "VONS" vs "Vons"
        - Apostrophe variations: "Trader Joe's" vs "Trader Joes"
        - Abbreviations: "CVS" matches "CVS Pharmacy"
        """
        if not name1 or not name2:
            return False

        # Normalize: lowercase, strip, remove common variations
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()

        # Exact match
        if n1 == n2:
            return True

        # Remove apostrophes and compare
        n1_clean = n1.replace("'", "").replace("'", "")
        n2_clean = n2.replace("'", "").replace("'", "")
        if n1_clean == n2_clean:
            return True

        # One contains the other (for abbreviations)
        if n1 in n2 or n2 in n1:
            return True

        return False

    def _addresses_match(self, addr1: Optional[str], addr2: Optional[str]) -> bool:
        """
        Check if two addresses are essentially the same.

        Handles:
        - Different formatting: "123 Main St" vs "123 Main Street"
        - Partial vs full: "123 Main" vs "123 Main St, City, ST 12345"
        """
        if not addr1 or not addr2:
            return False

        # Normalize
        a1 = addr1.lower().strip()
        a2 = addr2.lower().strip()

        if a1 == a2:
            return True

        # Extract just the street number and name for comparison
        a1_parts = a1.replace(",", " ").split()
        a2_parts = a2.replace(",", " ").split()

        # If first 3-4 tokens match, probably same address
        if len(a1_parts) >= 3 and len(a2_parts) >= 3:
            if a1_parts[:3] == a2_parts[:3]:
                return True

        return False

    def _phones_match(self, phone1: Optional[str], phone2: Optional[str]) -> bool:
        """
        Check if two phone numbers are the same.

        Handles:
        - Different formatting: "(805) 123-4567" vs "8051234567"
        - Country codes: "+1 805 123 4567" vs "805-123-4567"
        """
        if not phone1 or not phone2:
            return False

        # Extract just digits
        digits1 = "".join(c for c in phone1 if c.isdigit())
        digits2 = "".join(c for c in phone2 if c.isdigit())

        # Compare last 10 digits (ignore country code)
        if len(digits1) >= 10 and len(digits2) >= 10:
            return digits1[-10:] == digits2[-10:]
        return digits1 == digits2

    async def harmonize_all(
        self,
        validate_google: bool = True,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Harmonize all receipts grouped by place_id.

        Args:
            validate_google: Whether to validate against Google Places API
            limit: Optional limit on number of place_id groups to process

        Returns:
            Report dict with:
            - summary: Stats per place_id group
            - receipts: Individual receipt results
            - no_place_id: Receipts without place_id (can't be harmonized)
            - stats: Overall statistics
        """
        # Load all receipts if not already loaded
        if not self._place_id_groups:
            self.load_all_receipts()

        all_results: list[HarmonizerResult] = []
        group_summaries: list[dict] = []

        # Process each place_id group
        groups_to_process = list(self._place_id_groups.values())
        if limit:
            groups_to_process = groups_to_process[:limit]

        for i, group in enumerate(groups_to_process):
            if i > 0 and i % 50 == 0:
                logger.info(f"Processed {i}/{len(groups_to_process)} groups...")

            results = self.analyze_group(group, validate_google=validate_google)
            all_results.extend(results)

            # Summarize this group
            consistent = sum(1 for r in results if not r.needs_update)
            total = len(results)

            group_summaries.append({
                "place_id": group.place_id,
                "merchant_name": group.consensus_merchant_name,
                "total_receipts": total,
                "consistent": consistent,
                "needs_update": total - consistent,
                "pct_consistent": (consistent / total * 100) if total > 0 else 0,
                "google_validated": group.google_validated,
                "canonical_name": (
                    group.google_name if group.google_validated and group.google_name
                    else group.consensus_merchant_name
                ),
                "canonical_address": (
                    group.google_address if group.google_validated and group.google_address
                    else group.consensus_address
                ),
                "canonical_phone": (
                    group.google_phone if group.google_validated and group.google_phone
                    else group.consensus_phone
                ),
            })

        # Sort summaries by consistency (worst first)
        group_summaries.sort(key=lambda x: (x["pct_consistent"], -x["total_receipts"]))

        # Convert results to dicts for JSON serialization
        receipt_dicts = []
        for r in all_results:
            receipt_dicts.append({
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "place_id": r.place_id,
                "merchant": r.merchant_name,
                "group_size": r.group_size,
                "needs_update": r.needs_update,
                "changes_needed": r.changes_needed,
                "confidence": r.confidence,
                "current": {
                    "merchant_name": r.current_merchant_name,
                    "address": r.current_address,
                    "phone": r.current_phone,
                },
                "recommended": {
                    "merchant_name": r.recommended_merchant_name,
                    "address": r.recommended_address,
                    "phone": r.recommended_phone,
                },
            })

        # No place_id receipts
        no_place_id_dicts = []
        for r in self._no_place_id_receipts:
            no_place_id_dicts.append({
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "merchant_name": r.merchant_name,
                "address": r.address,
                "phone": r.phone,
            })

        self._last_report = {
            "summary": group_summaries,
            "receipts": receipt_dicts,
            "no_place_id": no_place_id_dicts,
            "stats": {
                "total_receipts": len(all_results) + len(no_place_id_dicts),
                "receipts_with_place_id": len(all_results),
                "receipts_without_place_id": len(no_place_id_dicts),
                "place_id_groups": len(groups_to_process),
                "groups_google_validated": sum(
                    1 for g in groups_to_process if g.google_validated
                ),
            },
        }

        return self._last_report

    async def _export_metadata_ndjson(self, export_path: str) -> int:
        """
        Export all receipt metadata to NDJSON (newline-delimited JSON) format.

        This creates a backup of all metadata before applying changes.
        Each line is a JSON object representing one ReceiptMetadata record.

        Args:
            export_path: Path to write the NDJSON file

        Returns:
            Number of records exported
        """
        import json
        from datetime import datetime

        logger.info(f"Exporting all receipt metadata to {export_path}...")

        # Get all receipt metadata from DynamoDB
        metadatas = []
        last_key = None
        while True:
            batch, last_key = self.dynamo.list_receipt_metadatas(
                limit=1000,
                last_evaluated_key=last_key,
            )
            metadatas.extend(batch)
            if not last_key:
                break

        # Write to NDJSON
        count = 0
        with open(export_path, 'w', encoding='utf-8') as f:
            for meta in metadatas:
                # Convert to dict using the entity's iterator
                record = dict(meta)

                # Ensure datetime is serialized as ISO string
                if 'timestamp' in record and isinstance(record['timestamp'], datetime):
                    record['timestamp'] = record['timestamp'].isoformat()

                f.write(json.dumps(record) + '\n')
                count += 1

        logger.info(f"Exported {count} receipt metadata records to {export_path}")
        return count

    async def apply_fixes(
        self,
        dry_run: bool = True,
        min_confidence: float = 50.0,
        min_group_size: int = 2,
        export_path: Optional[str] = None,
    ) -> UpdateResult:
        """
        Apply harmonization fixes to DynamoDB.

        Updates the main fields to ensure consistency across place_id groups:
        - merchant_name
        - address
        - phone_number

        All receipts with the same place_id will have identical values after
        harmonization.

        Args:
            dry_run: If True, only report what would be updated (no actual writes)
            min_confidence: Minimum confidence to apply fix (0-100)
            min_group_size: Minimum group size to apply fix (larger = more confident)
            export_path: If provided, export all metadata to NDJSON before changes

        Returns:
            UpdateResult with counts and any errors
        """
        if not self._last_report:
            await self.harmonize_all()

        # Type narrowing - we know _last_report is set after harmonize_all
        assert self._last_report is not None

        result = UpdateResult()
        receipts_to_update = []

        # Filter to receipts that need updates and meet thresholds
        for r in self._last_report["receipts"]:
            if not r["needs_update"]:
                result.total_skipped += 1
                continue

            if r["confidence"] < min_confidence:
                logger.debug(
                    f"Skipping {r['image_id']}#{r['receipt_id']}: "
                    f"confidence {r['confidence']} < {min_confidence}"
                )
                result.total_skipped += 1
                continue

            if r["group_size"] < min_group_size:
                logger.debug(
                    f"Skipping {r['image_id']}#{r['receipt_id']}: "
                    f"group_size {r['group_size']} < {min_group_size}"
                )
                result.total_skipped += 1
                continue

            receipts_to_update.append(r)

        result.total_processed = len(receipts_to_update)

        if dry_run:
            logger.info(f"[DRY RUN] Would update {len(receipts_to_update)} receipts")
            for r in receipts_to_update[:10]:  # Show first 10
                logger.info(
                    f"  {r['image_id'][:8]}...#{r['receipt_id']}: "
                    f"{r['changes_needed']}"
                )
            if len(receipts_to_update) > 10:
                logger.info(f"  ... and {len(receipts_to_update) - 10} more")
            result.total_updated = len(receipts_to_update)
            return result

        # Export all metadata to NDJSON before making changes
        if export_path:
            await self._export_metadata_ndjson(export_path)

        # Actually apply updates
        logger.info(f"Applying updates to {len(receipts_to_update)} receipts...")

        # Also update validation_status for all receipts with validated place_ids
        from receipt_dynamo.constants import MerchantValidationStatus

        # Get all receipts with place_ids that were validated
        all_receipts_with_validated_place_ids = []
        for r in self._last_report["receipts"]:
            if r["place_id"]:
                # Find the group to check if it was validated
                group = self._place_id_groups.get(r["place_id"])
                if group and group.google_validated:
                    all_receipts_with_validated_place_ids.append(r)

        logger.info(
            f"Also updating validation_status to MATCHED for "
            f"{len(all_receipts_with_validated_place_ids)} receipts with validated place_ids"
        )

        for r in receipts_to_update:
            try:
                # Get the current metadata record
                metadata = self.dynamo.get_receipt_metadata(
                    r["image_id"], r["receipt_id"]
                )

                if not metadata:
                    logger.warning(
                        f"Receipt not found: {r['image_id']}#{r['receipt_id']}"
                    )
                    result.total_failed += 1
                    result.errors.append(
                        f"{r['image_id']}#{r['receipt_id']}: not found"
                    )
                    continue

                # Update main fields to ensure consistency across place_id group
                updated_fields = []

                if r["recommended"]["merchant_name"] and metadata.merchant_name != r["recommended"]["merchant_name"]:
                    metadata.merchant_name = r["recommended"]["merchant_name"]
                    updated_fields.append(f"merchant_name={metadata.merchant_name}")

                if r["recommended"]["address"] and metadata.address != r["recommended"]["address"]:
                    metadata.address = r["recommended"]["address"]
                    updated_fields.append(f"address={metadata.address[:30]}...")

                if r["recommended"]["phone"] and metadata.phone_number != r["recommended"]["phone"]:
                    metadata.phone_number = r["recommended"]["phone"]
                    updated_fields.append(f"phone_number={metadata.phone_number}")

                # Update validation_status to MATCHED if place_id was validated
                # NOTE: __post_init__ in ReceiptMetadata recalculates validation_status
                # based on matched_fields, so we need to update matched_fields too
                group = self._place_id_groups.get(r["place_id"])
                if group and group.google_validated:
                    current_status = str(metadata.validation_status) if metadata.validation_status else None
                    if current_status != "MATCHED":
                        # Ensure matched_fields has at least 2 fields so __post_init__ sets MATCHED
                        # Check what fields we have
                        has_name = metadata.merchant_name and len(metadata.merchant_name.strip()) > 2
                        has_phone = metadata.phone_number and len("".join(c for c in metadata.phone_number if c.isdigit())) >= 7
                        has_address = metadata.address and len(metadata.address.split()) >= 2

                        # Update matched_fields to include available fields
                        matched_fields = set(metadata.matched_fields) if metadata.matched_fields else set()
                        if has_name and "name" not in matched_fields:
                            matched_fields.add("name")
                        if has_phone and "phone" not in matched_fields:
                            matched_fields.add("phone")
                        if has_address and "address" not in matched_fields:
                            matched_fields.add("address")

                        # If we have at least 2 fields, __post_init__ will set MATCHED
                        if len(matched_fields) >= 2:
                            metadata.matched_fields = list(matched_fields)
                            # __post_init__ will set validation_status to MATCHED automatically
                            updated_fields.append(f"matched_fields updated, validation_status will be MATCHED")
                        else:
                            # Fallback: set validation_status directly (but it may be reset on read)
                            metadata.validation_status = MerchantValidationStatus.MATCHED.value
                            updated_fields.append(f"validation_status={current_status}→MATCHED (may be reset by __post_init__)")

                # Update the record
                self.dynamo.update_receipt_metadata(metadata)
                result.total_updated += 1

                logger.debug(
                    f"Updated {r['image_id'][:8]}...#{r['receipt_id']}: "
                    f"{', '.join(updated_fields)}"
                )

            except Exception as e:
                logger.error(
                    f"Failed to update {r['image_id']}#{r['receipt_id']}: {e}"
                )
                result.total_failed += 1
                result.errors.append(f"{r['image_id']}#{r['receipt_id']}: {e}")

        # Also update validation_status for receipts that don't need other updates
        # but have validated place_ids
        status_updates = 0
        for r in all_receipts_with_validated_place_ids:
            # Skip if already processed above
            if any(
                u["image_id"] == r["image_id"] and u["receipt_id"] == r["receipt_id"]
                for u in receipts_to_update
            ):
                continue

            try:
                metadata = self.dynamo.get_receipt_metadata(
                    r["image_id"], r["receipt_id"]
                )
                if metadata:
                    # Check if status needs updating (use string comparison for reliability)
                    current_status = str(metadata.validation_status) if metadata.validation_status else None
                    if current_status != "MATCHED":
                        # NOTE: __post_init__ in ReceiptMetadata recalculates validation_status
                        # based on matched_fields. We need to update matched_fields to have
                        # at least 2 fields so __post_init__ sets MATCHED.
                        has_name = metadata.merchant_name and len(metadata.merchant_name.strip()) > 2
                        has_phone = metadata.phone_number and len("".join(c for c in metadata.phone_number if c.isdigit())) >= 7
                        has_address = metadata.address and len(metadata.address.split()) >= 2

                        # Update matched_fields to include available fields
                        matched_fields = set(metadata.matched_fields) if metadata.matched_fields else set()
                        if has_name and "name" not in matched_fields:
                            matched_fields.add("name")
                        if has_phone and "phone" not in matched_fields:
                            matched_fields.add("phone")
                        if has_address and "address" not in matched_fields:
                            matched_fields.add("address")

                        # If we have at least 2 fields, __post_init__ will set MATCHED
                        if len(matched_fields) >= 2:
                            metadata.matched_fields = list(matched_fields)
                            self.dynamo.update_receipt_metadata(metadata)
                            status_updates += 1
                            logger.info(
                                f"✅ Updated matched_fields and status → MATCHED for {r['image_id'][:8]}...#{r['receipt_id']}"
                            )
                        else:
                            # Not enough fields to trigger MATCHED in __post_init__
                            logger.debug(
                                f"Skipping {r['image_id'][:8]}...#{r['receipt_id']}: "
                                f"only {len(matched_fields)} matched_fields (need 2+ for MATCHED)"
                            )
                    else:
                        logger.debug(
                            f"Skipping {r['image_id'][:8]}...#{r['receipt_id']}: already MATCHED"
                        )
            except Exception as e:
                logger.error(
                    f"❌ Could not update status for {r['image_id'][:8]}...#{r['receipt_id']}: {e}",
                    exc_info=True
                )

        if status_updates > 0:
            logger.info(
                f"Updated validation_status to MATCHED for {status_updates} additional receipts"
            )

        logger.info(
            f"Update complete: {result.total_updated} updated, "
            f"{result.total_failed} failed, {result.total_skipped} skipped, "
            f"{status_updates} status-only updates"
        )

        return result

    def print_summary(self, report: dict[str, Any]) -> None:
        """Print a human-readable summary of the harmonization report."""
        stats = report["stats"]
        summaries = report["summary"]

        print("=" * 70)
        print("HARMONIZER V2 REPORT (Place ID Grouping)")
        print("=" * 70)
        print(f"Total receipts: {stats['total_receipts']}")
        print(f"  With place_id: {stats['receipts_with_place_id']}")
        print(f"  Without place_id: {stats['receipts_without_place_id']} (cannot harmonize)")
        print(f"Place ID groups: {stats['place_id_groups']}")
        print(f"  Google validated: {stats['groups_google_validated']}")
        print()

        # Overall consistency
        total_with_pid = stats['receipts_with_place_id']
        if total_with_pid > 0:
            consistent = sum(s["consistent"] for s in summaries)
            needs_update = sum(s["needs_update"] for s in summaries)
            print(f"Consistency (receipts with place_id):")
            print(f"  ✅ Consistent: {consistent} ({consistent/total_with_pid*100:.1f}%)")
            print(f"  ⚠️  Needs update: {needs_update} ({needs_update/total_with_pid*100:.1f}%)")
        print()

        # Groups with issues
        print("Groups needing attention (sorted by consistency):")
        shown = 0
        for s in summaries:
            if s["needs_update"] > 0 and shown < 20:
                status = "✅" if s["pct_consistent"] >= 80 else "⚠️"
                google = "🌐" if s["google_validated"] else "  "
                print(
                    f"  {status} {google} {s['merchant_name']}: "
                    f"{s['consistent']}/{s['total_receipts']} consistent "
                    f"({s['pct_consistent']:.0f}%)"
                )
                shown += 1

        if len([s for s in summaries if s["needs_update"] > 0]) > 20:
            remaining = len([s for s in summaries if s["needs_update"] > 0]) - 20
            print(f"  ... and {remaining} more groups")

        # Fully consistent groups
        fully_consistent = [s for s in summaries if s["pct_consistent"] == 100]
        print()
        print(f"Fully consistent groups: {len(fully_consistent)}")
        for s in fully_consistent[:10]:
            google = "🌐" if s["google_validated"] else "  "
            print(f"  ✅ {google} {s['merchant_name']}: {s['total_receipts']} receipts")
