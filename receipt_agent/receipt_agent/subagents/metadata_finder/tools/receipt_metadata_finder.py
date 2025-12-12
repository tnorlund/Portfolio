"""
Receipt Metadata Finder - Find Complete Metadata for Receipts
=============================================================

Purpose
-------
Finds ALL missing metadata for receipts, not just place_ids:
- place_id (Google Place ID)
- merchant_name (business name)
- address (formatted address)
- phone_number (phone number)

Key Improvements Over Place ID Finder
--------------------------------------
1. **Comprehensive**: Finds ALL missing metadata, not just place_id
2. **Intelligent Extraction**: Extracts metadata from receipt content even if Google Places fails
3. **Partial Fills**: Can fill in some fields even if others can't be found
4. **Better Reasoning**: Understands what's missing and how to find it
5. **Source Tracking**: Tracks where each field came from (receipt_content, google_places, similar_receipts)

How It Works
------------
1. **Load receipts with missing metadata**: Query DynamoDB for receipts missing any metadata
2. **Agent-based reasoning**: Uses LLM agent to:
   - Examine receipt content (lines, words, labels)
   - Extract metadata from receipt itself
   - Search Google Places API for missing fields
   - Use similar receipts for verification
   - Reason about the best values for each field
3. **Update metadata**: Add all found fields to DynamoDB

What Gets Updated
-----------------
The finder updates ANY missing fields:
- `place_id` ← Google Place ID (if found)
- `merchant_name` ← From receipt content or Google Places
- `address` ← From receipt content or Google Places
- `phone_number` ← From receipt content or Google Places

Usage
-----
```python
from receipt_agent.subagents.metadata_finder.tools.receipt_metadata_finder import (
    ReceiptMetadataFinder,
)

finder = ReceiptMetadataFinder(dynamo_client, places_client, chroma_client, embed_fn)
report = await finder.find_all_metadata_agentic()
finder.print_summary(report)

# Apply fixes (adds all found metadata)
result = await finder.apply_fixes(dry_run=False)
print(f"Updated {result.total_updated} receipts")
```

Example Output
--------------
```
RECEIPT METADATA FINDER REPORT
======================================================================
Total receipts with missing metadata: 65
  Found all fields: 42 (64.6%)
  Found partial: 15 (23.1%)
  Not found: 8 (12.3%)

Fields found:
  place_id: 52 (80.0%)
  merchant_name: 58 (89.2%)
  address: 55 (84.6%)
  phone_number: 48 (73.8%)
```
"""

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ==============================================================================
# Module-level constants
# ==============================================================================

# Field name mapping for matched_fields (canonical format)
FIELD_NAME_MAPPING = {
    "merchant_name": "name",
    "phone_number": "phone",
    "address": "address",
    "place_id": "place_id",
}

# Address suffixes for address detection
ADDRESS_SUFFIXES = [
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


# ==============================================================================
# Custom exceptions
# ==============================================================================


class AgenticSearchRequirementsError(ValueError):
    """Raised when agent-based search is requested without required dependencies."""

    def __init__(self):
        super().__init__(
            "Agent-based search requires chroma_client and embed_fn. "
            "Provide both when initializing ReceiptMetadataFinder."
        )


# ==============================================================================
# Helper functions
# ==============================================================================


def _looks_like_address(name: str) -> bool:
    """
    Check if a string looks like an address rather than a merchant name.

    Args:
        name: Name to check

    Returns:
        True if name looks like an address
    """
    if not name:
        return False

    name_upper = name.upper()
    # Use word-boundary matching to avoid false positives like "1ST BANK"
    # Match suffixes only at word boundaries
    suffix_pattern = (
        r"\b("
        + "|".join(re.escape(suffix) for suffix in ADDRESS_SUFFIXES)
        + r")\b"
    )
    has_suffix = bool(re.search(suffix_pattern, name_upper))
    has_address_markers = (
        re.match(r"^\d+", name.strip()) or "#" in name or "," in name
    )
    return has_suffix and has_address_markers


@dataclass
class ReceiptRecord:
    """
    A single receipt's metadata (may be incomplete).

    Attributes:
        image_id: UUID of the image containing this receipt
        receipt_id: Receipt number within the image
        merchant_name: Current merchant name (may be missing)
        place_id: Google Place ID (may be missing)
        address: Current address (may be missing)
        phone: Current phone number (may be missing)
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
class MetadataMatch:
    """
    Result of finding metadata for a receipt.

    Attributes:
        receipt: The receipt being processed
        place_id: Google Place ID found (if any)
        merchant_name: Merchant name found (if any)
        address: Address found (if any)
        phone_number: Phone number found (if any)
        confidence: Overall confidence (0-100)
        field_confidence: Confidence per field
        sources: Source for each field
        found: Whether any metadata was found
        fields_found: List of fields that were found
        error: Error message if processing failed
    """

    receipt: ReceiptRecord
    place_id: Optional[str] = None
    merchant_name: Optional[str] = None
    address: Optional[str] = None
    phone_number: Optional[str] = None
    confidence: float = 0.0
    field_confidence: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    found: bool = False
    fields_found: list[str] = field(default_factory=list)
    error: Optional[str] = None
    not_found_reason: Optional[str] = None
    reasoning: str = ""


@dataclass
class FinderResult:
    """
    Summary of metadata finding operation.

    Attributes:
        total_processed: Total receipts processed
        total_found_all: Number with all fields found
        total_found_partial: Number with some fields found
        total_not_found: Number with no fields found
        total_errors: Number with errors
        matches: List of MetadataMatch results
        field_counts: Count of each field found
    """

    total_processed: int = 0
    total_found_all: int = 0
    total_found_partial: int = 0
    total_not_found: int = 0
    total_errors: int = 0
    matches: list[MetadataMatch] = field(default_factory=list)
    field_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )


@dataclass
class UpdateResult:
    """
    Result of applying metadata updates to DynamoDB.

    Attributes:
        total_processed: Total receipts processed
        total_updated: Number successfully updated (or "would update" in dry-run mode)
        total_failed: Number that failed to update
        total_skipped: Number skipped (low confidence, etc.)
        errors: List of error messages
    """

    total_processed: int = 0
    total_updated: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ReceiptMetadataFinder:
    """
    Finds complete metadata for receipts using agent-based reasoning.

    This class finds ALL missing metadata (place_id, merchant_name, address, phone)
    using an LLM agent that:
    - Examines receipt content (lines, words, labels)
    - Extracts metadata from receipt itself
    - Searches Google Places API for missing fields
    - Uses similar receipts for verification
    - Reasons about the best values for each field

    Key Features:
    - Finds ALL missing metadata, not just place_id
    - Agent-based reasoning with receipt context
    - Extracts from receipt content even if Google Places fails
    - Can fill in partial metadata
    - Confidence scoring per field
    - Source tracking (where each field came from)
    - Batch processing with progress tracking
    - Dry-run mode for safety

    Example:
        ```python
        finder = ReceiptMetadataFinder(
            dynamo_client, places_client, chroma_client, embed_fn
        )

        # Find metadata using agent (recommended)
        report = await finder.find_all_metadata_agentic()
        finder.print_summary(report)

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
        Initialize the receipt metadata finder.

        Args:
            dynamo_client: DynamoDB client with list_receipt_metadatas() method
            places_client: Google Places client (PlacesClient from receipt_places)
            chroma_client: ChromaDB client for agent-based search
            embed_fn: Embedding function for agent-based search
            settings: Optional settings for agent-based search
        """
        self.dynamo = dynamo_client
        self.places = places_client
        self.chroma = chroma_client
        self.embed_fn = embed_fn
        self.settings = settings
        self._receipts_with_missing_metadata: list[ReceiptRecord] = []
        self._last_report: Optional[FinderResult] = None
        self._agent_graph: Optional[Any] = None
        self._agent_state_holder: Optional[dict] = None

    def load_receipts_with_missing_metadata(self) -> int:
        """
        Load all receipt metadata from DynamoDB that have missing fields.

        A receipt has missing metadata if ANY of these are missing:
        - place_id
        - merchant_name
        - address
        - phone_number

        Returns:
            Total number of receipts with missing metadata
        """
        logger.info(
            "Loading receipt metadata with missing fields from DynamoDB..."
        )

        self._receipts_with_missing_metadata = []
        total = 0

        try:
            # Paginate and filter in one pass to avoid accumulating all metadatas
            last_key = None
            while True:
                batch, last_key = self.dynamo.list_receipt_metadatas(
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                # Filter to receipts with missing metadata within this batch
                for meta in batch:
                    # Check if any field is missing
                    has_place_id = meta.place_id and meta.place_id not in (
                        "",
                        "null",
                        "NO_RESULTS",
                        "INVALID",
                    )
                    has_merchant_name = bool(
                        meta.merchant_name and meta.merchant_name.strip()
                    )
                    has_address = bool(meta.address and meta.address.strip())
                    has_phone = bool(
                        meta.phone_number and meta.phone_number.strip()
                    )

                    # If any field is missing, include it
                    if not (
                        has_place_id
                        and has_merchant_name
                        and has_address
                        and has_phone
                    ):
                        receipt = ReceiptRecord(
                            image_id=meta.image_id,
                            receipt_id=meta.receipt_id,
                            merchant_name=(
                                meta.merchant_name
                                if has_merchant_name
                                else None
                            ),
                            place_id=meta.place_id if has_place_id else None,
                            address=meta.address if has_address else None,
                            phone=meta.phone_number if has_phone else None,
                            validation_status=getattr(
                                meta, "validation_status", None
                            ),
                        )

                    self._receipts_with_missing_metadata.append(receipt)
                    total += 1

                if not last_key:
                    break

            logger.info(f"Loaded {total} receipts with missing metadata")

        except Exception:
            logger.exception("Failed to load receipts")
            raise

        return total

    async def find_all_metadata_agentic(
        self,
        limit: Optional[int] = None,
    ) -> FinderResult:
        """
        Find metadata using agent-based reasoning (recommended).

        This method uses an LLM agent to:
        - Examine receipt content (lines, words, labels)
        - Extract metadata from receipt itself
        - Search Google Places API for missing fields
        - Use similar receipts for verification
        - Determine the best values for each field

        Args:
            limit: Optional limit on number of receipts to process

        Returns:
            FinderResult with all matches
        """
        if not self.chroma or not self.embed_fn:
            raise AgenticSearchRequirementsError()

        # Load receipts if not already loaded
        if not self._receipts_with_missing_metadata:
            self.load_receipts_with_missing_metadata()

        # Initialize agent graph if needed
        if self._agent_graph is None:
            from receipt_agent.subagents.metadata_finder import (
                create_receipt_metadata_finder_graph,
            )

            self._agent_graph, self._agent_state_holder = (
                create_receipt_metadata_finder_graph(
                    dynamo_client=self.dynamo,
                    chroma_client=self.chroma,
                    embed_fn=self.embed_fn,
                    places_api=self.places,
                    settings=self.settings,
                )
            )

        result = FinderResult()
        receipts_to_process = self._receipts_with_missing_metadata

        if limit is not None:
            receipts_to_process = receipts_to_process[:limit]

        result.total_processed = len(receipts_to_process)

        logger.info(
            f"Finding metadata using agent for {len(receipts_to_process)} receipts..."
        )

        # Process each receipt with agent
        for i, receipt in enumerate(receipts_to_process):
            if (i + 1) % 5 == 0:
                logger.info(
                    f"Processed {i + 1}/{len(receipts_to_process)} receipts..."
                )

            # Retry logic for server errors
            max_retries = 3
            retry_delay = 2.0
            agent_result = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    from receipt_agent.subagents.metadata_finder import (
                        run_receipt_metadata_finder,
                    )

                    agent_result = await run_receipt_metadata_finder(
                        graph=self._agent_graph,
                        state_holder=self._agent_state_holder,
                        image_id=receipt.image_id,
                        receipt_id=receipt.receipt_id,
                    )
                    # Clear any previous error since this attempt succeeded
                    last_error = None
                    break

                except Exception as e:
                    last_error = e
                    error_str = str(e)

                    is_retryable = (
                        "500" in error_str
                        or "Internal Server Error" in error_str
                        or "disconnected" in error_str.lower()
                    )

                    if is_retryable and attempt < max_retries - 1:
                        logger.warning(
                            f"Retryable error for {receipt.image_id}#{receipt.receipt_id} "
                            f"(attempt {attempt + 1}/{max_retries}): {error_str[:100]}"
                        )
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        # Give up on this receipt but continue processing others
                        logger.error(
                            "Giving up on %s#%s after %d attempts: %s",
                            receipt.image_id,
                            receipt.receipt_id,
                            attempt + 1,
                            error_str[:200],
                        )
                        break  # Fall through to record match.error / total_errors

            # Convert agent result to MetadataMatch
            match = MetadataMatch(receipt=receipt)

            if agent_result and agent_result.get("found"):
                match.place_id = agent_result.get("place_id")
                match.merchant_name = agent_result.get("merchant_name")
                match.address = agent_result.get("address")
                match.phone_number = agent_result.get("phone_number")
                match.confidence = agent_result.get("confidence", 0.0) * 100.0
                match.field_confidence = agent_result.get(
                    "field_confidence", {}
                )
                match.sources = agent_result.get("sources", {})
                match.fields_found = agent_result.get("fields_found", [])
                match.reasoning = agent_result.get("reasoning", "")
                match.found = True

                # Count fields found
                for field in match.fields_found:
                    result.field_counts[field] += 1

                # Determine if all fields found or partial
                required_fields = [
                    "place_id",
                    "merchant_name",
                    "address",
                    "phone_number",
                ]
                found_required = [
                    f for f in required_fields if f in match.fields_found
                ]
                if len(found_required) == len(required_fields):
                    result.total_found_all += 1
                elif len(found_required) > 0:
                    result.total_found_partial += 1
                else:
                    # Agent said "found" but no required fields - treat as not found
                    result.total_not_found += 1
            else:
                # No metadata found - distinguish between errors and normal "not found"
                match.found = False
                match.confidence = 0.0

                if last_error:
                    # Actual exception/API failure occurred - this is a real error
                    match.error = str(last_error)
                elif agent_result:
                    # Agent completed but found no metadata - normal "not found" outcome
                    match.not_found_reason = agent_result.get(
                        "reasoning", "no_match"
                    )
                else:
                    # No result and no error - should not happen, but treat as not found
                    match.not_found_reason = "no_match"

            result.matches.append(match)

            # Count errors separately (only for actual exceptions/API failures)
            # Found cases (total_found_all, total_found_partial, or total_not_found when found=True
            # but no required fields) are already counted above
            if not match.found and match.error:
                # Only count as error if error is actually set (exceptions/API failures)
                result.total_errors += 1
            elif not match.found and not match.error:
                # Not found but no error - normal "no match" outcome
                # (This only happens when agent_result.get("found") was False/None)
                result.total_not_found += 1

        logger.info(
            f"Metadata finder complete: {result.total_found_all} all fields, "
            f"{result.total_found_partial} partial, {result.total_not_found} not found, "
            f"{result.total_errors} errors"
        )

        self._last_report = result
        return result

    async def apply_fixes(
        self,
        dry_run: bool = True,
        min_confidence: float = 50.0,
    ) -> UpdateResult:
        """
        Apply metadata updates to DynamoDB.

        Updates any missing fields with found values.

        Args:
            dry_run: If True, only report what would be updated
            min_confidence: Minimum confidence to apply fix (0-100)

        Returns:
            UpdateResult with counts and any errors
        """
        if not self._last_report:
            await self.find_all_metadata_agentic()

        assert self._last_report is not None

        result = UpdateResult()
        matches_to_update = []

        # Filter to matches that meet thresholds
        for match in self._last_report.matches:
            if not match.found:
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

        result.total_processed = len(matches_to_update)

        if dry_run:
            logger.info(
                f"[DRY RUN] Would update {len(matches_to_update)} receipts with metadata"
            )
            for match in matches_to_update[:10]:
                fields = [
                    f"{f}={getattr(match, f, None) is not None}"
                    for f in match.fields_found
                ]
                logger.info(
                    f"  {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                    f"{', '.join(fields)} (confidence={match.confidence:.1f}%)"
                )
            if len(matches_to_update) > 10:
                logger.info(f"  ... and {len(matches_to_update) - 10} more")

            # In dry-run mode, total_updated represents "would update" count
            # (no actual DynamoDB writes occur)
            result.total_updated = len(matches_to_update)
            return result

        # Actually apply updates
        logger.info(
            f"Applying metadata updates to {len(matches_to_update)} receipts..."
        )

        for match in matches_to_update:
            try:
                # Try to get existing metadata
                metadata = None
                metadata_exists = False
                try:
                    metadata = self.dynamo.get_receipt_metadata(
                        match.receipt.image_id, match.receipt.receipt_id
                    )
                    metadata_exists = True
                except Exception as e:
                    error_str = str(e)
                    error_type = type(e).__name__
                    if (
                        "does not exist" in error_str
                        or "EntityNotFoundError" in error_type
                        or "not found" in error_str.lower()
                    ):
                        metadata_exists = False
                    else:
                        raise

                if not metadata_exists:
                    # Create new metadata from found data
                    from datetime import datetime, timezone

                    from receipt_dynamo.constants import (
                        MerchantValidationStatus,
                        ValidationMethod,
                    )
                    from receipt_dynamo.entities import ReceiptMetadata

                    # Determine matched fields in canonical format
                    matched_fields = [
                        FIELD_NAME_MAPPING.get(f, f)
                        for f in match.fields_found
                    ]

                    # Create new ReceiptMetadata
                    metadata = ReceiptMetadata(
                        image_id=match.receipt.image_id,
                        receipt_id=match.receipt.receipt_id,
                        place_id=match.place_id or "",
                        merchant_name=match.merchant_name or "",
                        merchant_category="",
                        address=match.address or "",
                        phone_number=match.phone_number or "",
                        matched_fields=matched_fields,
                        validated_by=ValidationMethod.INFERENCE.value,
                        timestamp=datetime.now(timezone.utc),
                        reasoning=match.reasoning
                        or "Created by receipt_metadata_finder",
                        validation_status=(
                            MerchantValidationStatus.MATCHED.value
                            if match.place_id
                            else MerchantValidationStatus.UNSURE.value
                        ),
                    )

                    self.dynamo.add_receipt_metadata(metadata)
                    result.total_updated += 1

                    logger.info(
                        f"Created new metadata for {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                        f"{len(match.fields_found)} fields"
                    )
                    continue

                # Update existing metadata
                updated_fields = []

                if match.place_id and not metadata.place_id:
                    metadata.place_id = match.place_id
                    updated_fields.append("place_id")

                # Update merchant_name if missing OR if different (especially if current looks like an address)
                # CRITICAL: Never use an address as a merchant name
                if match.merchant_name:
                    # Validate that match.merchant_name is NOT an address
                    match_looks_like_address = _looks_like_address(
                        match.merchant_name
                    )

                    # Skip if the match itself looks like an address
                    if match_looks_like_address:
                        logger.warning(
                            f"Skipping merchant_name update for {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                            f"'{match.merchant_name}' looks like an address, not a merchant name"
                        )
                    elif not metadata.merchant_name:
                        metadata.merchant_name = match.merchant_name
                        updated_fields.append("merchant_name")
                    elif metadata.merchant_name != match.merchant_name:
                        # Check if current merchant_name looks like an address
                        looks_like_address = _looks_like_address(
                            metadata.merchant_name
                        )
                        # Always update if different and we have high confidence, or if current looks like address
                        if looks_like_address or match.confidence >= 80:
                            metadata.merchant_name = match.merchant_name
                            updated_fields.append("merchant_name")

                if match.address and not metadata.address:
                    metadata.address = match.address
                    updated_fields.append("address")

                if match.phone_number and not metadata.phone_number:
                    metadata.phone_number = match.phone_number
                    updated_fields.append("phone_number")

                # Update matched_fields to include all fields we found
                new_matched_fields = (
                    list(metadata.matched_fields)
                    if metadata.matched_fields
                    else []
                )
                for field in match.fields_found:
                    mapped_field = FIELD_NAME_MAPPING.get(field, field)
                    if mapped_field not in new_matched_fields:
                        new_matched_fields.append(mapped_field)

                if set(new_matched_fields) != set(
                    metadata.matched_fields or []
                ):
                    metadata.matched_fields = new_matched_fields
                    updated_fields.append("matched_fields")

                # Update validation_status based on whether we found a place_id and confidence
                # Note: ReceiptMetadata.__post_init__ will recalculate this, but we set it explicitly
                # to ensure it's correct based on our confidence and place_id
                from receipt_dynamo.constants import MerchantValidationStatus

                confidence = (
                    match.confidence / 100.0
                )  # Convert from percentage to decimal
                # Base status on the place_id that will actually be stored
                # (use metadata.place_id to avoid downgrading existing place_ids when match.place_id is None)
                has_place_id = bool(metadata.place_id)

                # Determine appropriate validation status
                if (
                    has_place_id and confidence >= 0.8
                ):  # 80% confidence threshold
                    new_status = MerchantValidationStatus.MATCHED.value
                elif has_place_id and confidence >= 0.5:  # 50-80% confidence
                    new_status = MerchantValidationStatus.UNSURE.value
                elif not has_place_id:
                    new_status = MerchantValidationStatus.NO_MATCH.value
                else:
                    new_status = MerchantValidationStatus.UNSURE.value

                if metadata.validation_status != new_status:
                    metadata.validation_status = new_status
                    updated_fields.append("validation_status")

                if updated_fields:
                    self.dynamo.update_receipt_metadata(metadata)
                    result.total_updated += 1

                    logger.debug(
                        f"Updated {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: "
                        f"{', '.join(updated_fields)}"
                    )
                else:
                    result.total_skipped += 1

            except Exception as e:
                logger.exception(
                    f"Failed to update {match.receipt.image_id}#{match.receipt.receipt_id}"
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
        """Print a human-readable summary of the metadata finding report."""
        if report is None:
            report = self._last_report

        if not report:
            print(
                "No report available. Run find_all_metadata_agentic() first."
            )
            return

        print("=" * 70)
        print("RECEIPT METADATA FINDER REPORT")
        print("=" * 70)
        print(
            f"Total receipts with missing metadata: {report.total_processed}"
        )
        # Calculate percentages safely (guard against division by zero)
        if report.total_processed == 0:
            found_all_percentage = 0.0
            found_partial_percentage = 0.0
            not_found_percentage = 0.0
        else:
            found_all_percentage = (
                report.total_found_all / report.total_processed * 100
            )
            found_partial_percentage = (
                report.total_found_partial / report.total_processed * 100
            )
            not_found_percentage = (
                report.total_not_found / report.total_processed * 100
            )

        print(
            f"  ✅ Found all fields: {report.total_found_all} ({found_all_percentage:.1f}%)"
        )
        print(
            f"  ⚠️  Found partial: {report.total_found_partial} ({found_partial_percentage:.1f}%)"
        )
        print(
            f"  ❌ Not found: {report.total_not_found} ({not_found_percentage:.1f}%)"
        )
        if report.total_errors > 0:
            print(f"  ⛔ Errors: {report.total_errors}")
        print()

        # Field breakdown
        if report.field_counts:
            print("Fields found:")
            total = report.total_processed
            for field, count in sorted(
                report.field_counts.items(), key=lambda x: -x[1]
            ):
                pct = count / total * 100 if total > 0 else 0
                print(f"  {field}: {count} ({pct:.1f}%)")
            print()

        # Show some examples
        found_matches = [m for m in report.matches if m.found]
        if found_matches:
            print("Sample matches (first 10):")
            for match in found_matches[:10]:
                fields_str = ", ".join(match.fields_found)
                print(
                    f"  ✅ {fields_str} "
                    f"(confidence={match.confidence:.0f}%)"
                )
            if len(found_matches) > 10:
                print(f"  ... and {len(found_matches) - 10} more")
            print()

        # Show some not found
        not_found = [m for m in report.matches if not m.found and not m.error]
        if not_found:
            print("Sample not found (first 5):")
            for match in not_found[:5]:
                print(
                    f"  ❌ {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}"
                )
            if len(not_found) > 5:
                print(f"  ... and {len(not_found) - 5} more")
