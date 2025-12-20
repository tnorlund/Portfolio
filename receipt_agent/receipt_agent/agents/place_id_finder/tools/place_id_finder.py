"""
Place ID Finder - Find Google Place IDs for Receipts
====================================================

Purpose
-------
Finds Google Place IDs for receipt place data that don't have place_ids yet.
This complements the harmonizer v3 workflow which works on receipts that
already have place_ids.

Key Insight
-----------
Receipts without place_ids are missing a critical piece of data that enables:
- Grouping receipts by actual merchant location
- Validating merchant information against Google Places
- Ensuring consistency across receipts from the same place

How It Works
------------
1. **Load receipts without place_id**: Query DynamoDB for all ReceiptPlace without place_id
2. **Search Google Places**: For each receipt, try multiple search strategies:
   - Phone number lookup (most reliable)
   - Address geocoding
   - Merchant name text search
3. **Match confidence**: Score matches based on how well they align with receipt data
4. **Update place data**: Add place_id and optionally update other fields from Google Places

What Gets Updated
-----------------
The finder updates:
- `place_id` ← Google Place ID (primary goal)
- Optionally: `merchant_name`, `formatted_address`, `phone_number` from Google Places

Usage
-----
```python
from receipt_agent.tools.place_id_finder import PlaceIdFinder

finder = PlaceIdFinder(dynamo_client, places_client)
report = await finder.find_all_place_ids()
finder.print_summary(report)

# Apply fixes (adds place_ids)
result = await finder.apply_fixes(dry_run=False)
print(f"Updated {result.total_updated} receipts with place_ids")
```

Example Output
--------------
```
PLACE ID FINDER REPORT
======================================================================
Total receipts without place_id: 65
  Found place_id: 52 (80.0%)
  Not found: 13 (20.0%)

Search methods:
  Phone lookup: 28 (53.8%)
  Address search: 18 (34.6%)
  Text search: 6 (11.5%)
```
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReceiptRecord:
    """
    A single receipt's place data (without place_id).

    Attributes:
        image_id: UUID of the image containing this receipt
        receipt_id: Receipt number within the image
        merchant_name: Current merchant name (from OCR/original)
        address: Current formatted address
        phone: Current phone number
        validation_status: Current validation status
    """

    image_id: str
    receipt_id: int
    merchant_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    validation_status: Optional[str] = None


@dataclass
class PlaceIdMatch:
    """
    Result of searching Google Places for a receipt.

    Attributes:
        receipt: The receipt being searched
        place_id: Google Place ID found (if any)
        place_name: Official name from Google Places
        place_address: Formatted address from Google Places
        place_phone: Phone number from Google Places
        search_method: How the place was found (phone/address/text)
        confidence: Confidence score (0-100) based on match quality
        found: Whether a place_id was found
        error: Error message if search failed
    """

    receipt: ReceiptRecord
    place_id: Optional[str] = None
    place_name: Optional[str] = None
    place_address: Optional[str] = None
    place_phone: Optional[str] = None
    search_method: Optional[str] = None
    confidence: float = 0.0
    found: bool = False
    error: Optional[str] = None
    not_found_reason: Optional[str] = None
    needs_review: bool = False


@dataclass
class FinderResult:
    """
    Summary of place ID finding operation.

    Attributes:
        total_processed: Total receipts processed
        total_found: Number of place_ids found
        total_not_found: Number of receipts where no place_id was found
        total_errors: Number of receipts with errors
        matches: List of PlaceIdMatch results
        search_method_counts: Count of each search method used
    """

    total_processed: int = 0
    total_found: int = 0
    total_not_found: int = 0
    total_errors: int = 0
    matches: list[PlaceIdMatch] = field(default_factory=list)
    search_method_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )


@dataclass
class UpdateResult:
    """
    Result of applying place_id updates to DynamoDB.

    Attributes:
        total_processed: Total receipts processed
        total_updated: Number successfully updated
        total_failed: Number that failed to update
        total_skipped: Number skipped (low confidence, etc.)
        errors: List of error messages
    """

    total_processed: int = 0
    total_updated: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class PlaceIdFinder:
    """
    Finds Google Place IDs for receipts missing place_ids.

    This class can use two approaches:
    1. **Simple search** (default): Direct Google Places API searches
    2. **Agent-based** (recommended): Uses LLM agent to reason about receipts

    The agent-based approach is more powerful because it:
    - Examines receipt content (lines, words, labels)
    - Searches ChromaDB for similar receipts
    - Uses multiple Google Places search strategies
    - Reasons about the best match

    Key Features:
    - Multiple search strategies (phone, address, text)
    - Agent-based reasoning with receipt context
    - Confidence scoring based on match quality
    - Batch processing with progress tracking
    - Dry-run mode for safety

    Example:
        ```python
        finder = PlaceIdFinder(dynamo_client, places_client, chroma_client)

        # Find place_ids using agent (recommended)
        report = await finder.find_all_place_ids_agentic()
        finder.print_summary(report)

        # Or use simple search
        report = await finder.find_all_place_ids()

        # Apply fixes (dry run first)
        result = await finder.apply_fixes(dry_run=True)
        print(f"Would update {result.total_updated} receipts")

        # Actually apply fixes
        result = await finder.apply_fixes(dry_run=False)
        print(f"Updated {result.total_updated} receipts")
        ```
    """

    def __init__(
        self,
        dynamo_client: Any,
        places_client: Optional[Any] = None,
        chroma_client: Optional[Any] = None,
        embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
        settings: Optional[Any] = None,
    ):
        """
        Initialize the place ID finder.

        Args:
            dynamo_client: DynamoDB client with list_receipt_places() method
            places_client: Google Places client (PlacesClient from receipt_places)
            chroma_client: Optional ChromaDB client for agent-based search
            embed_fn: Optional embedding function for agent-based search
            settings: Optional settings for agent-based search
        """
        self.dynamo = dynamo_client
        self.places = places_client
        self.chroma = chroma_client
        self.embed_fn = embed_fn
        self.settings = settings
        self._receipts_without_place_id: list[ReceiptRecord] = []
        self._last_report: Optional[FinderResult] = None
        self._agent_graph: Optional[Any] = None
        self._agent_state_holder: Optional[dict] = None

    def load_receipts_without_place_id(self) -> int:
        """
        Load all receipt metadata from DynamoDB that don't have place_ids.

        Returns:
            Total number of receipts without place_id
        """
        logger.info(
            "Loading receipt metadata without place_id from DynamoDB..."
        )

        self._receipts_without_place_id = []
        total = 0

        try:
            # Get all receipt places (paginated)
            places = []
            last_key = None
            while True:
                batch, last_key = self.dynamo.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                places.extend(batch)
                if not last_key:
                    break

            # Filter to receipts without place_id
            for place in places:
                # Skip if place_id exists and is valid
                if place.place_id and place.place_id not in (
                    "",
                    "null",
                    "NO_RESULTS",
                    "INVALID",
                ):
                    continue

                receipt = ReceiptRecord(
                    image_id=place.image_id,
                    receipt_id=place.receipt_id,
                    merchant_name=place.merchant_name,
                    address=place.formatted_address,
                    phone=place.phone_number,
                    validation_status=getattr(place, "validation_status", None),
                )

                self._receipts_without_place_id.append(receipt)
                total += 1

            logger.info(
                f"Loaded {total} receipts without place_id "
                f"(out of {len(places)} total receipts)"
            )

        except Exception:
            logger.exception("Failed to load receipts")
            raise

        return total

    def _search_places_for_receipt(
        self,
        receipt: ReceiptRecord,
    ) -> PlaceIdMatch:
        """
        Search Google Places API for a receipt using multiple strategies.

        Tries in order:
        1. Phone number lookup (most reliable)
        2. Address geocoding
        3. Merchant name text search

        Args:
            receipt: The receipt to search for

        Returns:
            PlaceIdMatch with results
        """
        match = PlaceIdMatch(receipt=receipt)

        if not self.places:
            match.error = "Google Places client not configured"
            return match

        # Strategy 1: Try phone lookup first (most reliable)
        if receipt.phone:
            # Validate phone number format before searching
            phone_digits = "".join(c for c in receipt.phone if c.isdigit())
            if len(phone_digits) >= 10:  # Valid phone has at least 10 digits
                try:
                    logger.debug(f"Searching by phone: {receipt.phone}")
                    place_data = self.places.search_by_phone(receipt.phone)
                    if place_data and place_data.get("place_id"):
                        match.place_id = place_data.get("place_id")
                        match.place_name = place_data.get("name")
                        match.place_address = place_data.get(
                            "formatted_address"
                        )
                        match.place_phone = place_data.get(
                            "formatted_phone_number"
                        ) or place_data.get("international_phone_number")
                        match.search_method = "phone"
                        match.found = True
                        match.confidence = self._calculate_confidence(
                            receipt, place_data, "phone"
                        )
                        return match
                except Exception as e:
                    logger.debug(
                        f"Phone search failed for {receipt.image_id}#{receipt.receipt_id}: {e}"
                    )
                    match.error = f"Phone search error: {e!s}"
            else:
                logger.debug(
                    f"Invalid phone number format: {receipt.phone} (only {len(phone_digits)} digits)"
                )

        # Strategy 2: Try address geocoding
        if receipt.address and not match.found:
            # Validate address has some content
            if (
                len(receipt.address.strip()) >= 10
            ):  # Address should have some content
                try:
                    logger.debug(
                        f"Searching by address: {receipt.address[:50]}"
                    )
                    place_data = self.places.search_by_address(receipt.address)
                    if place_data and place_data.get("place_id"):
                        match.place_id = place_data.get("place_id")
                        match.place_name = place_data.get("name")
                        match.place_address = place_data.get(
                            "formatted_address"
                        )
                        match.place_phone = place_data.get(
                            "formatted_phone_number"
                        ) or place_data.get("international_phone_number")
                        match.search_method = "address"
                        match.found = True
                        match.confidence = self._calculate_confidence(
                            receipt, place_data, "address"
                        )
                        return match
                except Exception as e:
                    logger.debug(
                        f"Address search failed for {receipt.image_id}#{receipt.receipt_id}: {e}"
                    )
                    if not match.error:  # Don't overwrite previous error
                        match.error = f"Address search error: {str(e)}"
            else:
                logger.debug(f"Address too short: {receipt.address}")

        # Strategy 3: Try text search with merchant name
        if receipt.merchant_name and not match.found:
            # Validate merchant name has some content
            if (
                len(receipt.merchant_name.strip()) >= 3
            ):  # Name should have some content
                try:
                    logger.debug(f"Searching by text: {receipt.merchant_name}")
                    place_data = self.places.search_by_text(
                        receipt.merchant_name
                    )
                    if place_data and place_data.get("place_id"):
                        match.place_id = place_data.get("place_id")
                        match.place_name = place_data.get("name")
                        match.place_address = place_data.get(
                            "formatted_address"
                        )
                        match.place_phone = place_data.get(
                            "formatted_phone_number"
                        ) or place_data.get("international_phone_number")
                        match.search_method = "text"
                        match.found = True
                        match.confidence = self._calculate_confidence(
                            receipt, place_data, "text"
                        )
                        return match
                except Exception as e:
                    logger.debug(
                        f"Text search failed for {receipt.image_id}#{receipt.receipt_id}: {e}"
                    )
                    if not match.error:  # Don't overwrite previous error
                        match.error = f"Text search error: {str(e)}"
            else:
                logger.debug(
                    f"Merchant name too short: {receipt.merchant_name}"
                )

        # No match found - this is a normal outcome, not an error
        match.found = False
        if not match.error:  # Only set not_found_reason if no error occurred
            match.not_found_reason = "no_match"
        return match

    def _calculate_confidence(
        self,
        receipt: ReceiptRecord,
        place_data: dict[str, Any],
        search_method: str,
    ) -> float:
        """
        Calculate confidence score (0-100) for a place match.

        Higher confidence when:
        - Phone matches (phone search = 95-100)
        - Address matches well (address search = 80-95)
        - Name matches well (text search = 60-85)
        - Multiple fields align

        Args:
            receipt: The receipt being matched
            place_data: Google Places result
            search_method: How the place was found

        Returns:
            Confidence score 0-100
        """
        base_confidence = {
            "phone": 95.0,  # Phone is very reliable
            "address": 80.0,  # Address is good but can have variations
            "text": 60.0,  # Text search is least reliable
        }.get(search_method, 50.0)

        # Boost confidence if multiple fields match
        matches = 0
        total_fields = 0

        # Check name match
        if receipt.merchant_name and place_data.get("name"):
            total_fields += 1
            if self._names_match(
                receipt.merchant_name, place_data.get("name")
            ):
                matches += 1

        # Check address match
        if receipt.address and place_data.get("formatted_address"):
            total_fields += 1
            if self._addresses_match(
                receipt.address, place_data.get("formatted_address")
            ):
                matches += 1

        # Check phone match
        if receipt.phone and place_data.get("formatted_phone_number"):
            total_fields += 1
            if self._phones_match(
                receipt.phone, place_data.get("formatted_phone_number")
            ):
                matches += 1

        # Boost confidence based on field matches
        if total_fields > 0:
            match_ratio = matches / total_fields
            confidence = base_confidence + (
                match_ratio * 20
            )  # Up to +20 points
        else:
            confidence = base_confidence

        return min(100.0, confidence)

    def _names_match(self, name1: Optional[str], name2: Optional[str]) -> bool:
        """Check if two merchant names are essentially the same."""
        if not name1 or not name2:
            return False

        # Normalize: lowercase, remove punctuation
        n1 = "".join(c.lower() for c in name1 if c.isalnum() or c.isspace())
        n2 = "".join(c.lower() for c in name2 if c.isalnum() or c.isspace())

        # Check if one contains the other (handles abbreviations)
        return n1 in n2 or n2 in n1 or n1 == n2

    def _addresses_match(
        self, addr1: Optional[str], addr2: Optional[str]
    ) -> bool:
        """Check if two addresses are similar."""
        if not addr1 or not addr2:
            return False

        # Extract street number and name (first line)
        def extract_street(address: str) -> str:
            parts = address.split(",")
            if parts:
                return "".join(
                    c.lower() for c in parts[0] if c.isalnum() or c.isspace()
                )
            return ""

        street1 = extract_street(addr1)
        street2 = extract_street(addr2)

        # Check if streets match (allowing for minor variations)
        return street1 in street2 or street2 in street1 or street1 == street2

    def _phones_match(
        self, phone1: Optional[str], phone2: Optional[str]
    ) -> bool:
        """Check if two phone numbers match (digits only)."""
        if not phone1 or not phone2:
            return False

        # Extract digits only
        digits1 = "".join(c for c in phone1 if c.isdigit())
        digits2 = "".join(c for c in phone2 if c.isdigit())

        # Match if last 10 digits are the same (handles country codes)
        if len(digits1) >= 10 and len(digits2) >= 10:
            return digits1[-10:] == digits2[-10:]
        return digits1 == digits2

    async def find_all_place_ids(
        self,
        limit: Optional[int] = None,
    ) -> FinderResult:
        """
        Find place_ids for all receipts without them.

        Args:
            limit: Optional limit on number of receipts to process

        Returns:
            FinderResult with all matches
        """
        # Load receipts if not already loaded
        if not self._receipts_without_place_id:
            self.load_receipts_without_place_id()

        result = FinderResult()
        receipts_to_process = self._receipts_without_place_id

        if limit:
            receipts_to_process = receipts_to_process[:limit]

        result.total_processed = len(receipts_to_process)

        logger.info(
            f"Searching Google Places for {len(receipts_to_process)} receipts..."
        )

        # Process each receipt
        for i, receipt in enumerate(receipts_to_process):
            if (i + 1) % 10 == 0:
                logger.info(
                    f"Processed {i + 1}/{len(receipts_to_process)} receipts..."
                )

            match = self._search_places_for_receipt(receipt)
            result.matches.append(match)

            if match.found:
                result.total_found += 1
                if match.search_method:
                    result.search_method_counts[match.search_method] += 1
            elif match.error:
                # Only count as error if error is actually set (exceptions/API failures)
                result.total_errors += 1
            else:
                # Not found but no error - normal "no match" outcome
                result.total_not_found += 1

        logger.info(
            f"Search complete: {result.total_found} found, "
            f"{result.total_not_found} not found, {result.total_errors} errors"
        )

        self._last_report = result
        return result

    async def find_all_place_ids_agentic(
        self,
        limit: Optional[int] = None,
    ) -> FinderResult:
        """
        Find place_ids using agent-based reasoning (recommended).

        This method uses an LLM agent to:
        - Examine receipt content (lines, words, labels)
        - Search ChromaDB for similar receipts
        - Use Google Places API with reasoning
        - Determine the best match

        Args:
            limit: Optional limit on number of receipts to process

        Returns:
            FinderResult with all matches
        """
        if not self.chroma or not self.embed_fn:
            raise ValueError(
                "Agent-based search requires chroma_client and embed_fn. "
                "Use find_all_place_ids() for simple search."
            )

        # Load receipts if not already loaded
        if not self._receipts_without_place_id:
            self.load_receipts_without_place_id()

        # Initialize agent graph if needed
        if self._agent_graph is None:
            from receipt_agent.agents.place_id_finder import (
                create_place_id_finder_graph,
            )

            self._agent_graph, self._agent_state_holder = (
                create_place_id_finder_graph(
                    dynamo_client=self.dynamo,
                    chroma_client=self.chroma,
                    embed_fn=self.embed_fn,
                    places_api=self.places,
                    settings=self.settings,
                )
            )

        result = FinderResult()
        receipts_to_process = self._receipts_without_place_id

        if limit:
            receipts_to_process = receipts_to_process[:limit]

        result.total_processed = len(receipts_to_process)

        logger.info(
            f"Searching Google Places using agent for {len(receipts_to_process)} receipts..."
        )

        # Process each receipt with agent
        for i, receipt in enumerate(receipts_to_process):
            if (i + 1) % 5 == 0:
                logger.info(
                    f"Processed {i + 1}/{len(receipts_to_process)} receipts..."
                )

            # Retry logic for server errors
            max_retries = 3
            retry_delay = 2.0  # seconds
            agent_result = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    from receipt_agent.agents.place_id_finder import (
                        run_place_id_finder,
                    )

                    agent_result = await run_place_id_finder(
                        graph=self._agent_graph,
                        state_holder=self._agent_state_holder,
                        image_id=receipt.image_id,
                        receipt_id=receipt.receipt_id,
                    )
                    break  # Success, exit retry loop

                except Exception as e:
                    last_error = e
                    error_str = str(e)

                    # Check if it's a retryable server error
                    is_retryable = (
                        "500" in error_str
                        or "Internal Server Error" in error_str
                        or "Server disconnected" in error_str
                        or "disconnected" in error_str.lower()
                    )

                    if is_retryable and attempt < max_retries - 1:
                        logger.warning(
                            f"Retryable error for {receipt.image_id}#{receipt.receipt_id} "
                            f"(attempt {attempt + 1}/{max_retries}): {error_str[:100]}"
                        )
                        await asyncio.sleep(
                            retry_delay * (attempt + 1)
                        )  # Exponential backoff
                        continue
                    else:
                        # Not retryable or max retries reached
                        raise

            # Convert agent result to PlaceIdMatch
            match = PlaceIdMatch(receipt=receipt)

            if agent_result and agent_result.get("found"):
                match.place_id = agent_result.get("place_id")
                match.place_name = agent_result.get("place_name")
                match.place_address = agent_result.get("place_address")
                match.place_phone = agent_result.get("place_phone")

                # Prioritize Google Places API methods in reporting
                # The place_id MUST come from Google Places API, not from similar receipts
                search_methods = agent_result.get("search_methods_used", [])
                google_places_methods = [
                    "phone",
                    "address",
                    "text",
                    "text_search",
                    "place_id",
                ]
                primary_method = None
                verification_methods = []

                # Find the primary Google Places API method
                for method in search_methods:
                    if method in google_places_methods:
                        if primary_method is None:
                            primary_method = method
                        else:
                            verification_methods.append(method)
                    else:
                        verification_methods.append(method)

                # Report primary method first, then verification methods
                if primary_method:
                    if verification_methods:
                        match.search_method = f"{primary_method} (verified: {', '.join(verification_methods)})"
                    else:
                        match.search_method = primary_method
                else:
                    # Fallback: use all methods if no Google Places method found (shouldn't happen)
                    match.search_method = ", ".join(search_methods)

                match.confidence = agent_result.get("confidence", 0.0) * 100.0
                match.found = True
            else:
                # No place_id found - distinguish between errors and normal "not found"
                match.found = False
                match.confidence = 0.0

                # Check if receipt has no searchable data
                has_phone = bool(
                    receipt.phone
                    and len("".join(c for c in receipt.phone if c.isdigit()))
                    >= 10
                )
                has_address = bool(
                    receipt.address and len(receipt.address.strip()) >= 10
                )
                has_name = bool(
                    receipt.merchant_name
                    and len(receipt.merchant_name.strip()) >= 3
                )

                if not (has_phone or has_address or has_name):
                    # Mark as needing review - no searchable data
                    match.needs_review = True
                    match.not_found_reason = "no_searchable_data"
                elif last_error:
                    # Actual exception/API failure occurred - this is a real error
                    match.error = str(last_error)
                elif agent_result:
                    # Agent completed but found no match - normal "not found" outcome
                    match.not_found_reason = agent_result.get(
                        "reasoning", "no_match"
                    )
                else:
                    # No result and no error - should not happen, but treat as not found
                    match.not_found_reason = "no_match"

            result.matches.append(match)

            if match.found:
                result.total_found += 1
                if match.search_method:
                    # Count primary search method
                    methods = match.search_method.split(", ")
                    if methods:
                        result.search_method_counts[methods[0]] = (
                            result.search_method_counts.get(methods[0], 0) + 1
                        )
            elif match.error:
                # Only count as error if error is actually set (exceptions/API failures)
                result.total_errors += 1
            else:
                # Not found but no error - normal "no match" outcome
                result.total_not_found += 1

        logger.info(
            f"Agent search complete: {result.total_found} found, "
            f"{result.total_not_found} not found, {result.total_errors} errors"
        )

        self._last_report = result
        return result

    async def apply_fixes(
        self,
        dry_run: bool = True,
        min_confidence: float = 50.0,
    ) -> UpdateResult:
        """
        Apply place_id updates to DynamoDB.

        Updates the place_id field and other fields (merchant_name, address, phone_number)
        from Google Places for receipts where a place_id was found.
        Creates new metadata records if they don't exist.

        Args:
            dry_run: If True, only report what would be updated (no actual writes)
            min_confidence: Minimum confidence to apply fix (0-100)

        Returns:
            UpdateResult with counts and any errors
        """
        if not self._last_report:
            await self.find_all_place_ids()

        # Type narrowing - we know _last_report is set after find_all_place_ids
        assert self._last_report is not None

        result = UpdateResult()
        matches_to_update = []
        matches_needing_review = []

        # Filter to matches that meet thresholds
        for match in self._last_report.matches:
            if not match.found:
                # Check if this needs review (no searchable data)
                if match.needs_review:
                    matches_needing_review.append(match)
                else:
                    result.total_skipped += 1
                continue

            if match.confidence < min_confidence:
                logger.debug(
                    f"Skipping {match.receipt.image_id}#{match.receipt.receipt_id}: "
                    f"confidence {match.confidence} < {min_confidence}"
                )
                result.total_skipped += 1
                continue

            matches_to_update.append(match)

        result.total_processed = len(matches_to_update) + len(
            matches_needing_review
        )

        if dry_run:
            logger.info(
                f"[DRY RUN] Would update {len(matches_to_update)} receipts with place_ids and Google Places data"
            )
            for match in matches_to_update[:10]:  # Show first 10
                fields = [f"place_id={match.place_id}"]
                if match.place_name:
                    fields.append(f"merchant_name={match.place_name[:20]}")
                if match.place_address:
                    fields.append(f"address={match.place_address[:20]}...")
                if match.place_phone:
                    fields.append(f"phone={match.place_phone}")
                logger.info(
                    f"  {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                    f"{', '.join(fields)} (confidence={match.confidence:.1f}, method={match.search_method})"
                )
            if len(matches_to_update) > 10:
                logger.info(f"  ... and {len(matches_to_update) - 10} more")

            if matches_needing_review:
                logger.info(
                    f"[DRY RUN] Would mark {len(matches_needing_review)} receipts as needing review"
                )
                for match in matches_needing_review[:5]:
                    logger.info(
                        f"  {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                        f"needs_review=True ({match.error})"
                    )
                if len(matches_needing_review) > 5:
                    logger.info(
                        f"  ... and {len(matches_needing_review) - 5} more"
                    )

            result.total_updated = len(matches_to_update)
            return result

        # Actually apply updates
        logger.info(
            f"Applying place_id updates to {len(matches_to_update)} receipts..."
        )

        for match in matches_to_update:
            try:
                # Try to get existing place data
                place = None
                place_exists = False
                try:
                    place = self.dynamo.get_receipt_place(
                        match.receipt.image_id, match.receipt.receipt_id
                    )
                    place_exists = True
                except Exception as e:
                    # Check if it's a "not found" error
                    error_str = str(e)
                    error_type = type(e).__name__
                    if (
                        "does not exist" in error_str
                        or "EntityNotFoundError" in error_type
                        or "not found" in error_str.lower()
                    ):
                        place_exists = False
                    else:
                        # Some other error - re-raise
                        raise

                if not place_exists:
                    # Create new place from Google Places data
                    from datetime import datetime, timezone

                    from receipt_dynamo.constants import (
                        MerchantValidationStatus,
                    )
                    from receipt_dynamo.entities import ReceiptPlace

                    # Use Google Places data (preferred) or fallback to receipt data
                    merchant_name = (
                        match.place_name or match.receipt.merchant_name or ""
                    )
                    address = (
                        match.place_address or match.receipt.address or ""
                    )
                    phone = match.place_phone or match.receipt.phone or ""

                    # Create new ReceiptPlace
                    place = ReceiptPlace(
                        image_id=match.receipt.image_id,
                        receipt_id=match.receipt.receipt_id,
                        place_id=match.place_id,
                        merchant_name=merchant_name,
                        formatted_address=address,
                        phone_number=phone,
                        reasoning=f"Created by place_id_finder: {match.search_method or 'unknown method'}",
                        validation_status=MerchantValidationStatus.MATCHED.value,
                    )

                    # Create the record
                    self.dynamo.add_receipt_place(place)
                    result.total_updated += 1

                    logger.info(
                        f"Created new place for {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                        f"place_id={match.place_id}, merchant_name={merchant_name[:30] if merchant_name else 'N/A'}"
                    )
                    continue

                # Update existing place
                updated_fields = []
                place.place_id = match.place_id
                updated_fields.append(f"place_id={match.place_id}")

                # Always update other fields from Google Places
                # CRITICAL: Never use an address as a merchant name
                if (
                    match.place_name
                    and place.merchant_name != match.place_name
                ):
                    # Validate that place_name is NOT an address
                    import re

                    place_name_upper = match.place_name.upper()
                    looks_like_address = any(
                        suffix in place_name_upper
                        for suffix in [
                            "BLVD",
                            "RD",
                            "ST",
                            "STREET",
                            "AVE",
                            "AVENUE",
                            "DR",
                            "DRIVE",
                            "LANE",
                            "LN",
                            "WAY",
                            "CT",
                            "COURT",
                            "PL",
                            "PLACE",
                        ]
                    ) and (
                        re.match(r"^\d+", match.place_name.strip())
                        or "#" in match.place_name
                        or "," in match.place_name
                    )

                    if looks_like_address:
                        logger.warning(
                            f"Skipping merchant_name update for {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                            f"'{match.place_name}' from Google Places looks like an address, not a merchant name"
                        )
                    else:
                        place.merchant_name = match.place_name
                        updated_fields.append(
                            f"merchant_name={match.place_name}"
                        )

                if (
                    match.place_address
                    and place.formatted_address != match.place_address
                ):
                    place.formatted_address = match.place_address
                    updated_fields.append(
                        f"address={match.place_address[:30]}..."
                    )

                if (
                    match.place_phone
                    and place.phone_number != match.place_phone
                ):
                    place.phone_number = match.place_phone
                    updated_fields.append(f"phone_number={match.place_phone}")

                # Update the record
                self.dynamo.update_receipt_place(place)
                result.total_updated += 1

                logger.debug(
                    f"Updated {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                    f"{', '.join(updated_fields)}"
                )

            except Exception as e:
                logger.exception(
                    f"Failed to update {match.receipt.image_id}#{match.receipt.receipt_id}"
                )
                result.total_failed += 1
                result.errors.append(
                    f"{match.receipt.image_id}#{match.receipt.receipt_id}: {e!s}"
                )

        # Mark receipts as needing review (no searchable data)
        if matches_needing_review:
            logger.info(
                f"Marking {len(matches_needing_review)} receipts as needing review..."
            )

            for match in matches_needing_review:
                try:
                    # Try to get existing place data
                    place = None
                    place_exists = False
                    try:
                        place = self.dynamo.get_receipt_place(
                            match.receipt.image_id, match.receipt.receipt_id
                        )
                        place_exists = True
                    except Exception as e:
                        # Check if it's a "not found" error
                        error_str = str(e)
                        error_type = type(e).__name__
                        if (
                            "does not exist" in error_str
                            or "EntityNotFoundError" in error_type
                            or "not found" in error_str.lower()
                        ):
                            place_exists = False
                        else:
                            # Some other error - re-raise
                            raise

                    if not place_exists:
                        # Create minimal place marked as needing review
                        from receipt_dynamo.constants import (
                            MerchantValidationStatus,
                        )
                        from receipt_dynamo.entities import ReceiptPlace

                        place = ReceiptPlace(
                            image_id=match.receipt.image_id,
                            receipt_id=match.receipt.receipt_id,
                            place_id="",  # No place_id found
                            merchant_name=match.receipt.merchant_name or "",
                            formatted_address=match.receipt.address or "",
                            phone_number=match.receipt.phone or "",
                            reasoning=match.error
                            or "No searchable data available for place_id lookup",
                            validation_status=MerchantValidationStatus.UNSURE.value,
                        )

                        self.dynamo.add_receipt_place(place)
                        result.total_updated += 1

                        logger.info(
                            f"Created place (needs review) for {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}"
                        )
                        continue

                    # Update existing place to mark as needing review
                    from receipt_dynamo.constants import (
                        MerchantValidationStatus,
                    )

                    # Update the record
                    place.validation_status = (
                        MerchantValidationStatus.UNSURE.value
                    )
                    place.reasoning = (
                        match.error
                        or "No searchable data available for place_id lookup"
                    )
                    self.dynamo.update_receipt_place(place)
                    result.total_updated += 1

                    logger.debug(
                        f"Marked {match.receipt.image_id[:8]}...#{match.receipt.receipt_id} as needing review"
                    )

                except Exception as e:
                    logger.exception(
                        f"Failed to mark {match.receipt.image_id}#{match.receipt.receipt_id} as needing review"
                    )
                    result.total_failed += 1
                    result.errors.append(
                        f"{match.receipt.image_id}#{match.receipt.receipt_id}: {e!s}"
                    )

        logger.info(
            f"Update complete: {result.total_updated} updated, "
            f"{result.total_failed} failed, {result.total_skipped} skipped"
        )

        return result

    def print_summary(self, report: Optional[FinderResult] = None) -> None:
        """Print a human-readable summary of the place ID finding report."""
        if report is None:
            report = self._last_report

        if not report:
            print("No report available. Run find_all_place_ids() first.")
            return

        print("=" * 70)
        print("PLACE ID FINDER REPORT")
        print("=" * 70)
        print(f"Total receipts without place_id: {report.total_processed}")
        # Calculate percentages safely (guard against division by zero)
        safe_denominator = (
            report.total_processed if report.total_processed > 0 else 1
        )
        found_percentage = (
            (report.total_found / safe_denominator * 100)
            if report.total_processed > 0
            else 0.0
        )
        not_found_percentage = (
            (report.total_not_found / safe_denominator * 100)
            if report.total_processed > 0
            else 0.0
        )

        print(
            f"  ✅ Found place_id: {report.total_found} ({found_percentage:.1f}%)"
        )
        print(
            f"  ❌ Not found: {report.total_not_found} ({not_found_percentage:.1f}%)"
        )
        if report.total_errors > 0:
            print(f"  ⚠️  Errors: {report.total_errors}")
        print()

        # Search method breakdown
        if report.search_method_counts:
            print("Search methods:")
            total_found = sum(report.search_method_counts.values())
            for method, count in sorted(
                report.search_method_counts.items(), key=lambda x: -x[1]
            ):
                pct = count / total_found * 100 if total_found > 0 else 0
                print(f"  {method.capitalize()}: {count} ({pct:.1f}%)")
            print()

        # Confidence distribution
        confidences = [m.confidence for m in report.matches if m.found]
        if confidences:
            avg_confidence = sum(confidences) / len(confidences)
            min_confidence = min(confidences)
            max_confidence = max(confidences)
            print("Confidence scores:")
            print(f"  Average: {avg_confidence:.1f}")
            print(f"  Range: {min_confidence:.1f} - {max_confidence:.1f}")
            print()

        # Show some examples
        found_matches = [m for m in report.matches if m.found]
        if found_matches:
            print("Sample matches (first 10):")
            for match in found_matches[:10]:
                merchant = match.receipt.merchant_name or "Unknown"
                print(
                    f"  ✅ {merchant[:30]:30} → {match.place_name[:30]:30} "
                    f"(confidence={match.confidence:.0f}%, method={match.search_method})"
                )
            if len(found_matches) > 10:
                print(f"  ... and {len(found_matches) - 10} more")
            print()

        # Show some not found
        not_found = [m for m in report.matches if not m.found and not m.error]
        if not_found:
            print("Sample not found (first 5):")
            for match in not_found[:5]:
                merchant = match.receipt.merchant_name or "Unknown"
                print(f"  ❌ {merchant[:50]}")
            if len(not_found) > 5:
                print(f"  ... and {len(not_found) - 5} more")
            print()

        # Show some errors with context
        errors = [m for m in report.matches if m.error]
        if errors:
            print("Sample errors (first 5):")
            for match in errors[:5]:
                merchant = match.receipt.merchant_name or "Unknown"
                error_msg = match.error or "Unknown error"
                # Show what data was available
                has_phone = bool(match.receipt.phone)
                has_address = bool(match.receipt.address)
                has_name = bool(
                    match.receipt.merchant_name
                    and len(match.receipt.merchant_name.strip()) >= 3
                )
                data_info = []
                if has_phone:
                    data_info.append("phone")
                if has_address:
                    data_info.append("address")
                if has_name:
                    data_info.append("name")
                data_str = (
                    ", ".join(data_info) if data_info else "no searchable data"
                )
                print(
                    f"  ⚠️  {merchant[:30]:30} ({data_str}) - {error_msg[:40]}"
                )
            if len(errors) > 5:
                print(f"  ... and {len(errors) - 5} more")
