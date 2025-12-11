"""
Merchant Harmonizer V3 - Agent-Based Harmonization
===================================================

Purpose
-------
Ensures metadata consistency across receipts sharing the same Google Place ID
using an LLM agent for intelligent reasoning about edge cases.

Why V3 is Better Than V2
------------------------
V2 uses simple majority voting for consensus. This fails for:
- Groups where the majority has OCR errors
- Edge cases like address-like merchant names
- Cases where Google Places data conflicts with receipt data

V3 uses an LLM agent that:
- Reasons about all receipts in a group
- Validates against Google Places (source of truth)
- Handles edge cases intelligently
- Provides confidence scores and reasoning

How It Works
------------
1. **Load receipts and group by place_id**: Same as V2
2. **Identify inconsistent groups**: Groups where metadata differs
3. **Run agent on each inconsistent group**: Agent reasons about the correct values
4. **Apply fixes**: Update receipts to match canonical values

Usage
-----
```python
from receipt_agent.tools.harmonizer_v3 import MerchantHarmonizerV3

harmonizer = MerchantHarmonizerV3(dynamo_client, places_client)
report = await harmonizer.harmonize_all()
harmonizer.print_summary(report)

# Apply fixes (updates receipts to match canonical values)
result = await harmonizer.apply_fixes(dry_run=False)
print(f"Updated {result.total_updated} receipts")
```

Example Output
--------------
```
HARMONIZER V3 REPORT (Agent-Based)
======================================================================
Total receipts: 571
  With place_id: 506
  Without place_id: 65 (cannot harmonize)

Place ID groups: 155
  Consistent: 120 (no action needed)
  Inconsistent: 35 (processed by agent)

Agent Results (35 groups):
  ✅ High confidence (>80%): 28
  ⚠️  Medium confidence (50-80%): 5
  ❌ Low confidence (<50%): 2

Receipts needing updates: 142
```
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReceiptRecord:
    """
    A single receipt's metadata.

    Attributes:
        image_id: UUID of the image containing this receipt
        receipt_id: Receipt number within the image
        merchant_name: Current merchant name
        place_id: Google Place ID
        address: Current address
        phone: Current phone number
    """

    image_id: str
    receipt_id: int
    merchant_name: Optional[str] = None
    place_id: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None


@dataclass
class PlaceIdGroup:
    """
    A group of receipts sharing the same place_id.

    Attributes:
        place_id: Google Place ID
        receipts: List of receipts in this group
        is_consistent: Whether all receipts have the same metadata
        agent_result: Result from agent harmonization (if inconsistent)
    """

    place_id: str
    receipts: list[ReceiptRecord] = field(default_factory=list)
    is_consistent: bool = True
    agent_result: Optional[dict] = None


@dataclass
class HarmonizationResult:
    """
    Result for a single receipt.

    Attributes:
        image_id, receipt_id: Receipt identifier
        needs_update: Whether this receipt needs its metadata updated
        changes: List of changes to apply
        canonical_*: The correct values to use
    """

    image_id: str
    receipt_id: int
    needs_update: bool = False
    changes: list[str] = field(default_factory=list)
    canonical_merchant_name: Optional[str] = None
    canonical_address: Optional[str] = None
    canonical_phone: Optional[str] = None
    confidence: float = 0.0
    source: str = ""


@dataclass
class UpdateResult:
    """
    Result of applying harmonization fixes.

    Attributes:
        total_processed: Receipts processed
        total_updated: Receipts updated
        total_skipped: Receipts skipped (already consistent)
        total_failed: Receipts that failed to update
        errors: Error messages
    """

    total_processed: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    errors: list[str] = field(default_factory=list)


class MerchantHarmonizerV3:
    """
    Agent-based harmonizer for ensuring metadata consistency.

    Uses an LLM agent to reason about receipts sharing the same place_id
    and determine the correct canonical metadata.

    Key Features:
    - Agent-based reasoning for edge cases
    - Google Places validation (source of truth)
    - Confidence scoring
    - Detailed reasoning for each decision
    - Batch processing with progress tracking
    - Dry-run mode for safety

    Example:
        ```python
        harmonizer = MerchantHarmonizerV3(dynamo_client, places_client)

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
        settings: Optional[Any] = None,
    ):
        """
        Initialize the harmonizer.

        Args:
            dynamo_client: DynamoDB client with list_receipt_metadatas() method
            places_client: Optional Google Places client for validation
            settings: Optional settings for the agent
        """
        self.dynamo = dynamo_client
        self.places = places_client
        self.settings = settings
        self._place_id_groups: dict[str, PlaceIdGroup] = {}
        self._no_place_id_receipts: list[ReceiptRecord] = []
        self._last_report: Optional[dict[str, Any]] = None
        self._agent_graph: Optional[Any] = None
        self._agent_state_holder: Optional[dict] = None

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
                )

                if receipt.place_id and receipt.place_id not in (
                    "",
                    "null",
                    "NO_RESULTS",
                    "INVALID",
                ):
                    if receipt.place_id not in self._place_id_groups:
                        self._place_id_groups[receipt.place_id] = PlaceIdGroup(
                            place_id=receipt.place_id
                        )
                    self._place_id_groups[receipt.place_id].receipts.append(
                        receipt
                    )
                else:
                    self._no_place_id_receipts.append(receipt)

                total += 1

            # Mark groups as consistent or inconsistent
            for group in self._place_id_groups.values():
                group.is_consistent = self._is_group_consistent(group)

            logger.info(
                f"Loaded {total} receipts: "
                f"{len(self._place_id_groups)} place_id groups, "
                f"{len(self._no_place_id_receipts)} without place_id"
            )

        except Exception:
            logger.exception("Failed to load receipts")
            raise

        return total

    def load_receipts_for_place_ids(self, place_ids: list[str]) -> int:
        """
        Load receipt metadata from DynamoDB for specific place_ids using GSI2.

        This is much more efficient than loading all receipts when you only
        need receipts for specific place_ids.

        Args:
            place_ids: List of place_ids to load receipts for

        Returns:
            Total number of receipts loaded
        """
        logger.info(
            f"Loading receipt metadata for {len(place_ids)} place_id(s) from DynamoDB..."
        )

        self._place_id_groups = {}
        self._no_place_id_receipts = []
        total = 0

        try:
            # Query each place_id using GSI2 (efficient)
            for place_id in place_ids:
                # Skip invalid place_ids
                if not place_id or place_id in (
                    "",
                    "null",
                    "NO_RESULTS",
                    "INVALID",
                ):
                    continue

                # Extract base place_id if this is a sub-batch (format: "place_id:sub_batch_idx")
                base_place_id = place_id
                if ":" in place_id:
                    parts = place_id.split(":", 1)
                    if len(parts) == 2:
                        base_place_id = parts[0]

                # Query receipts for this place_id using GSI2
                metadatas = []
                last_key = None
                while True:
                    batch, last_key = (
                        self.dynamo.list_receipt_metadatas_with_place_id(
                            place_id=base_place_id,
                            limit=1000,
                            last_evaluated_key=last_key,
                        )
                    )
                    metadatas.extend(batch)
                    if not last_key:
                        break

                # Group receipts by place_id
                for meta in metadatas:
                    receipt = ReceiptRecord(
                        image_id=meta.image_id,
                        receipt_id=meta.receipt_id,
                        merchant_name=meta.merchant_name,
                        place_id=meta.place_id,
                        address=meta.address,
                        phone=meta.phone_number,
                    )

                    if receipt.place_id not in self._place_id_groups:
                        self._place_id_groups[receipt.place_id] = PlaceIdGroup(
                            place_id=receipt.place_id
                        )
                    self._place_id_groups[receipt.place_id].receipts.append(
                        receipt
                    )
                    total += 1

            # Mark groups as consistent or inconsistent
            for group in self._place_id_groups.values():
                group.is_consistent = self._is_group_consistent(group)

            logger.info(
                f"Loaded {total} receipts for {len(self._place_id_groups)} place_id group(s)"
            )

        except Exception:
            logger.exception("Failed to load receipts for place_ids")
            raise

        return total

    def _is_group_consistent(self, group: PlaceIdGroup) -> bool:
        """
        Check if all receipts in a group have consistent metadata.

        Returns True if all non-empty values match (case-insensitive for merchant_name).
        """
        if len(group.receipts) <= 1:
            return True

        # Collect non-empty values
        merchant_names = set()
        addresses = set()
        phones = set()

        for r in group.receipts:
            if r.merchant_name:
                merchant_names.add(r.merchant_name.lower().strip())
            if r.address:
                addresses.add(r.address.strip())
            if r.phone:
                # Normalize phone (digits only)
                digits = "".join(c for c in r.phone if c.isdigit())
                if len(digits) >= 10:
                    phones.add(digits[-10:])

        # Consistent if at most one unique value per field
        return (
            len(merchant_names) <= 1
            and len(addresses) <= 1
            and len(phones) <= 1
        )

    async def harmonize_all(
        self,
        limit: Optional[int] = None,
        skip_consistent: bool = True,
        min_group_size: int = 1,
    ) -> dict[str, Any]:
        """
        Harmonize all place_id groups using the agent.

        Args:
            limit: Maximum number of groups to process (for testing)
            skip_consistent: Skip groups that are already consistent
            min_group_size: Minimum group size to process

        Returns:
            Report dict with summary and detailed results
        """
        # Load receipts if not already loaded
        if not self._place_id_groups:
            self.load_all_receipts()

        # Initialize agent if needed
        if self._agent_graph is None:
            from receipt_agent.graph.harmonizer_workflow import (
                create_harmonizer_graph,
            )

            self._agent_graph, self._agent_state_holder = (
                create_harmonizer_graph(
                    dynamo_client=self.dynamo,
                    places_api=self.places,
                    settings=self.settings,
                )
            )

        # Filter groups to process
        groups_to_process = []
        for group in self._place_id_groups.values():
            if len(group.receipts) < min_group_size:
                continue
            if skip_consistent and group.is_consistent:
                continue
            groups_to_process.append(group)

        if limit:
            groups_to_process = groups_to_process[:limit]

        logger.info(
            f"Processing {len(groups_to_process)} inconsistent groups with agent..."
        )

        # Process each group with the agent
        results: list[dict] = []
        for i, group in enumerate(groups_to_process):
            if (i + 1) % 5 == 0:
                logger.info(
                    f"Processed {i + 1}/{len(groups_to_process)} groups..."
                )

            # Convert receipts to dict format for agent
            receipts_data = [
                {
                    "image_id": r.image_id,
                    "receipt_id": r.receipt_id,
                    "merchant_name": r.merchant_name,
                    "address": r.address,
                    "phone": r.phone,
                }
                for r in group.receipts
            ]

            # Retry logic for server errors
            max_retries = 3
            result = None
            for attempt in range(max_retries):
                try:
                    from receipt_agent.graph.harmonizer_workflow import (
                        run_harmonizer_agent,
                    )

                    result = await run_harmonizer_agent(
                        graph=self._agent_graph,
                        state_holder=self._agent_state_holder,
                        place_id=group.place_id,
                        receipts=receipts_data,
                        places_api=self.places,
                    )
                    break
                except Exception as e:
                    error_str = str(e)
                    is_retryable = (
                        "500" in error_str
                        or "Internal Server Error" in error_str
                        or "disconnected" in error_str.lower()
                    )
                    if is_retryable and attempt < max_retries - 1:
                        logger.warning(
                            f"Retryable error for {group.place_id} (attempt {attempt + 1}): {error_str[:100]}"
                        )
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    else:
                        result = {
                            "place_id": group.place_id,
                            "error": str(e),
                            "total_receipts": len(group.receipts),
                            "receipts_needing_update": 0,
                        }
                        break

            group.agent_result = result
            results.append(result)

        # Build report
        consistent_groups = [
            g for g in self._place_id_groups.values() if g.is_consistent
        ]
        inconsistent_groups = [
            g for g in self._place_id_groups.values() if not g.is_consistent
        ]

        high_confidence = [
            r
            for r in results
            if r.get("confidence", 0) >= 0.8 and "error" not in r
        ]
        medium_confidence = [
            r
            for r in results
            if 0.5 <= r.get("confidence", 0) < 0.8 and "error" not in r
        ]
        low_confidence = [
            r
            for r in results
            if r.get("confidence", 0) < 0.5 and "error" not in r
        ]
        errors = [r for r in results if "error" in r]

        total_updates_needed = sum(
            r.get("receipts_needing_update", 0) for r in results
        )

        report = {
            "summary": {
                "total_receipts": sum(
                    len(g.receipts) for g in self._place_id_groups.values()
                )
                + len(self._no_place_id_receipts),
                "total_with_place_id": sum(
                    len(g.receipts) for g in self._place_id_groups.values()
                ),
                "total_without_place_id": len(self._no_place_id_receipts),
                "total_groups": len(self._place_id_groups),
                "consistent_groups": len(consistent_groups),
                "inconsistent_groups": len(inconsistent_groups),
                "groups_processed": len(groups_to_process),
            },
            "agent_results": {
                "high_confidence": len(high_confidence),
                "medium_confidence": len(medium_confidence),
                "low_confidence": len(low_confidence),
                "errors": len(errors),
            },
            "updates": {
                "total_receipts_needing_update": total_updates_needed,
            },
            "results": results,
            "errors": [r for r in results if "error" in r],
        }

        self._last_report = report
        return report

    async def apply_fixes(
        self,
        dry_run: bool = True,
        min_confidence: float = 0.5,
    ) -> UpdateResult:
        """
        Apply harmonization fixes to DynamoDB.

        Updates receipts to match the canonical values determined by the agent.

        Args:
            dry_run: If True, only report what would be updated
            min_confidence: Minimum confidence to apply fix

        Returns:
            UpdateResult with counts and errors
        """
        if not self._last_report:
            await self.harmonize_all()

        assert self._last_report is not None

        result = UpdateResult()
        updates_to_apply = []

        # Collect all updates from agent results
        for agent_result in self._last_report.get("results", []):
            if "error" in agent_result:
                continue

            confidence = agent_result.get("confidence", 0)
            if confidence < min_confidence:
                logger.debug(
                    f"Skipping {agent_result.get('place_id')}: "
                    f"confidence {confidence} < {min_confidence}"
                )
                continue

            for update in agent_result.get("updates", []):
                updates_to_apply.append(
                    {
                        "image_id": update["image_id"],
                        "receipt_id": update["receipt_id"],
                        "canonical_merchant_name": agent_result.get(
                            "canonical_merchant_name"
                        ),
                        "canonical_address": agent_result.get(
                            "canonical_address"
                        ),
                        "canonical_phone": agent_result.get("canonical_phone"),
                        "changes": update["changes"],
                        "confidence": confidence,
                        "source": agent_result.get("source", "agent"),
                    }
                )

        result.total_processed = len(updates_to_apply)

        if dry_run:
            logger.info(
                f"[DRY RUN] Would update {len(updates_to_apply)} receipts"
            )
            for update in updates_to_apply[:10]:
                logger.info(
                    f"  {update['image_id'][:8]}...#{update['receipt_id']}: "
                    f"{', '.join(update['changes'][:2])}"
                )
            if len(updates_to_apply) > 10:
                logger.info(f"  ... and {len(updates_to_apply) - 10} more")

            result.total_updated = len(updates_to_apply)
            return result

        # Actually apply updates
        logger.info(f"Applying {len(updates_to_apply)} updates to DynamoDB...")

        for update in updates_to_apply:
            try:
                metadata = self.dynamo.get_receipt_metadata(
                    update["image_id"], update["receipt_id"]
                )

                if not metadata:
                    logger.warning(
                        f"Metadata not found for {update['image_id']}#{update['receipt_id']}"
                    )
                    result.total_failed += 1
                    result.errors.append(
                        f"{update['image_id']}#{update['receipt_id']}: Metadata not found"
                    )
                    continue

                # Update fields
                updated_fields = []
                if (
                    update["canonical_merchant_name"]
                    and metadata.merchant_name
                    != update["canonical_merchant_name"]
                ):
                    metadata.merchant_name = update["canonical_merchant_name"]
                    updated_fields.append("merchant_name")

                if (
                    update["canonical_address"]
                    and metadata.address != update["canonical_address"]
                ):
                    metadata.address = update["canonical_address"]
                    updated_fields.append("address")

                if (
                    update["canonical_phone"]
                    and metadata.phone_number != update["canonical_phone"]
                ):
                    metadata.phone_number = update["canonical_phone"]
                    updated_fields.append("phone_number")

                if updated_fields:
                    self.dynamo.update_receipt_metadata(metadata)
                    result.total_updated += 1
                    logger.debug(
                        f"Updated {update['image_id'][:8]}...#{update['receipt_id']}: "
                        f"{', '.join(updated_fields)}"
                    )
                else:
                    result.total_skipped += 1

            except Exception as e:
                logger.exception(
                    f"Failed to update {update['image_id']}#{update['receipt_id']}"
                )
                result.total_failed += 1
                result.errors.append(
                    f"{update['image_id']}#{update['receipt_id']}: {e!s}"
                )

        logger.info(
            f"Update complete: {result.total_updated} updated, "
            f"{result.total_skipped} skipped, {result.total_failed} failed"
        )

        return result

    def print_summary(self, report: Optional[dict] = None) -> None:
        """Print a human-readable summary of the harmonization report."""
        if report is None:
            report = self._last_report

        if not report:
            print("No report available. Run harmonize_all() first.")
            return

        summary = report.get("summary", {})
        agent_results = report.get("agent_results", {})
        updates = report.get("updates", {})

        print("=" * 70)
        print("HARMONIZER V3 REPORT (Agent-Based)")
        print("=" * 70)
        print(f"Total receipts: {summary.get('total_receipts', 0)}")
        print(f"  With place_id: {summary.get('total_with_place_id', 0)}")
        print(
            f"  Without place_id: {summary.get('total_without_place_id', 0)} (cannot harmonize)"
        )
        print()

        print(f"Place ID groups: {summary.get('total_groups', 0)}")
        print(
            f"  Consistent: {summary.get('consistent_groups', 0)} (no action needed)"
        )
        print(
            f"  Inconsistent: {summary.get('inconsistent_groups', 0)} (processed by agent)"
        )
        print()

        processed = summary.get("groups_processed", 0)
        if processed > 0:
            print(f"Agent Results ({processed} groups):")
            print(
                f"  ✅ High confidence (≥80%): {agent_results.get('high_confidence', 0)}"
            )
            print(
                f"  ⚠️  Medium confidence (50-80%): {agent_results.get('medium_confidence', 0)}"
            )
            print(
                f"  ❌ Low confidence (<50%): {agent_results.get('low_confidence', 0)}"
            )
            if agent_results.get("errors", 0) > 0:
                print(f"  ⛔ Errors: {agent_results.get('errors', 0)}")
            print()

        print(
            f"Receipts needing updates: {updates.get('total_receipts_needing_update', 0)}"
        )
        print()

        # Show some examples
        results = report.get("results", [])
        successful = [
            r
            for r in results
            if "error" not in r and r.get("receipts_needing_update", 0) > 0
        ]

        if successful:
            print("Sample harmonization decisions:")
            for r in successful[:5]:
                print(
                    f"  {r.get('canonical_merchant_name', 'Unknown')[:30]:30} "
                    f"({r.get('receipts_needing_update', 0)}/{r.get('total_receipts', 0)} updates, "
                    f"confidence={r.get('confidence', 0):.0%})"
                )
            if len(successful) > 5:
                print(f"  ... and {len(successful) - 5} more")
            print()

        # Show errors
        errors = report.get("errors", [])
        if errors:
            print(f"Errors ({len(errors)}):")
            for e in errors[:3]:
                print(
                    f"  ⛔ {e.get('place_id', 'Unknown')}: {e.get('error', 'Unknown error')[:50]}"
                )
            if len(errors) > 3:
                print(f"  ... and {len(errors) - 3} more")

    def get_inconsistent_groups(self) -> list[PlaceIdGroup]:
        """Get all inconsistent place_id groups."""
        return [
            g for g in self._place_id_groups.values() if not g.is_consistent
        ]

    def get_group(self, place_id: str) -> Optional[PlaceIdGroup]:
        """Get a specific place_id group."""
        return self._place_id_groups.get(place_id)
