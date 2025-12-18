"""
Backfill script: Migrate ReceiptMetadata to ReceiptPlace entities.

This script creates ReceiptPlace records for all existing ReceiptMetadata
records in DynamoDB, enriching them with data from Google Places API v1.

Usage:
    # Dry run: see what would be created
    python backfill_receipt_place.py --dry-run

    # Actually backfill (in batches)
    python backfill_receipt_place.py --batch-size 100 --limit 1000

    # Resume from last position
    python backfill_receipt_place.py --resume

    # Process specific receipts
    python backfill_receipt_place.py --image-id IMAGE123 --receipt-id 1

Migration Strategy:
- Phase 1: Create ReceiptPlace for receipts with complete ReceiptMetadata
- Phase 2: Verify data consistency between ReceiptMetadata and ReceiptPlace
- Phase 3: Monitor cache hit rates for v1 API calls
- Phase 4: Mark migration complete when all receipts have ReceiptPlace
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class BackfillStats:
    """Statistics for backfill operation."""
    total_processed: int = 0
    total_created: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class ReceiptPlaceBackfiller:
    """
    Backfill ReceiptPlace entities from existing ReceiptMetadata.

    This backfiller:
    1. Scans all ReceiptMetadata records from DynamoDB
    2. For each metadata with place_id, fetches v1 API data
    3. Creates ReceiptPlace entity with rich geographic and business data
    4. Writes ReceiptPlace to DynamoDB
    5. Handles errors gracefully with detailed logging
    """

    def __init__(
        self,
        dynamo_client: Any,
        places_client: Any,
        batch_size: int = 50,
    ):
        """
        Initialize the backfiller.

        Args:
            dynamo_client: DynamoDB client with receipt_metadata methods
            places_client: Google Places API client with get_place_details()
            batch_size: Number of receipts to process before logging progress
        """
        self.dynamo = dynamo_client
        self.places = places_client
        self.batch_size = batch_size
        self.stats = BackfillStats()

    async def backfill_all(
        self,
        dry_run: bool = True,
        limit: Optional[int] = None,
        image_id: Optional[str] = None,
        receipt_id: Optional[int] = None,
    ) -> BackfillStats:
        """
        Backfill ReceiptPlace for all receipts with ReceiptMetadata.

        Args:
            dry_run: If True, don't write to DynamoDB
            limit: Maximum number of receipts to backfill
            image_id: If provided, backfill only this image's receipts
            receipt_id: If provided with image_id, backfill only this receipt

        Returns:
            BackfillStats with operation results
        """
        self.stats = BackfillStats()

        logger.info("Starting ReceiptPlace backfill...")
        if dry_run:
            logger.info("[DRY RUN MODE] - No writes will be made to DynamoDB")
        if limit:
            logger.info(f"Processing max {limit} receipts")

        try:
            # If specific receipt requested, backfill just that one
            if image_id and receipt_id is not None:
                await self._backfill_single_receipt(
                    image_id, receipt_id, dry_run
                )
                return self.stats

            # Otherwise, scan and backfill all receipts
            await self._backfill_all_receipts(
                dry_run, limit, image_id
            )

        except Exception as e:
            logger.exception("Fatal error during backfill")
            self.stats.errors.append(f"Fatal error: {e!s}")

        return self.stats

    async def _backfill_single_receipt(
        self,
        image_id: str,
        receipt_id: int,
        dry_run: bool,
    ) -> None:
        """Backfill a single receipt."""
        logger.info(
            f"Backfilling single receipt: {image_id}#{receipt_id}"
        )

        try:
            # Get existing ReceiptMetadata
            metadata = self.dynamo.get_receipt_metadata(
                image_id, receipt_id
            )

            if not metadata or not metadata.place_id:
                logger.warning(
                    f"No metadata or place_id found for "
                    f"{image_id}#{receipt_id}"
                )
                self.stats.total_skipped += 1
                return

            # Create ReceiptPlace
            await self._create_receipt_place_from_metadata(
                metadata, dry_run
            )

        except Exception as e:
            logger.exception(
                f"Failed to backfill {image_id}#{receipt_id}"
            )
            self.stats.total_failed += 1
            self.stats.errors.append(
                f"{image_id}#{receipt_id}: {e!s}"
            )

    async def _backfill_all_receipts(
        self,
        dry_run: bool,
        limit: Optional[int],
        image_id: Optional[str],
    ) -> None:
        """Backfill all receipts with ReceiptMetadata."""
        last_key = None
        processed = 0

        while True:
            # Fetch batch of metadata
            batch, last_key = self.dynamo.list_receipt_metadatas(
                limit=self.batch_size,
                last_evaluated_key=last_key,
            )

            if not batch:
                break

            for metadata in batch:
                # Stop if limit reached
                if limit and processed >= limit:
                    logger.info(f"Reached limit of {limit} receipts")
                    break

                # Skip if no place_id (can't enrich without it)
                if not metadata.place_id or metadata.place_id in (
                    "", "null", "NO_RESULTS", "INVALID"
                ):
                    self.stats.total_skipped += 1
                    continue

                # Skip if image_id filter doesn't match
                if image_id and metadata.image_id != image_id:
                    self.stats.total_skipped += 1
                    continue

                # Create ReceiptPlace from metadata
                try:
                    await self._create_receipt_place_from_metadata(
                        metadata, dry_run
                    )
                except Exception as e:
                    logger.exception(
                        f"Failed to create ReceiptPlace for "
                        f"{metadata.image_id}#{metadata.receipt_id}"
                    )
                    self.stats.total_failed += 1
                    self.stats.errors.append(
                        f"{metadata.image_id}#{metadata.receipt_id}: "
                        f"{e!s}"
                    )

                processed += 1

                # Log progress periodically
                if processed % self.batch_size == 0:
                    logger.info(
                        f"Processed {processed} receipts: "
                        f"{self.stats.total_created} created, "
                        f"{self.stats.total_failed} failed, "
                        f"{self.stats.total_skipped} skipped"
                    )

            # Continue with next batch if there is one
            if not last_key or (limit and processed >= limit):
                break

        logger.info(
            f"Backfill complete: {self.stats.total_created} created, "
            f"{self.stats.total_failed} failed, "
            f"{self.stats.total_skipped} skipped "
            f"(total: {self.stats.total_processed})"
        )

    async def _create_receipt_place_from_metadata(
        self,
        metadata: Any,
        dry_run: bool,
    ) -> None:
        """
        Create ReceiptPlace from ReceiptMetadata.

        Fetches rich data from v1 API and creates ReceiptPlace entity.
        """
        from receipt_dynamo.constants import (
            MerchantValidationStatus,
            ValidationMethod,
        )
        from receipt_dynamo.entities import ReceiptPlace

        self.stats.total_processed += 1

        if not self.places or not metadata.place_id:
            self.stats.total_skipped += 1
            return

        try:
            # Get rich place data from v1 API
            place_v1 = await self.places.get_place_details(
                metadata.place_id
            )

            if not place_v1:
                logger.warning(
                    f"v1 API returned no data for place_id "
                    f"{metadata.place_id}"
                )
                self.stats.total_skipped += 1
                return

            # Extract data from v1 response
            latitude = (
                place_v1.location.latitude
                if place_v1.location
                else None
            )
            longitude = (
                place_v1.location.longitude
                if place_v1.location
                else None
            )
            viewport_ne_lat = (
                place_v1.viewport.high.latitude
                if place_v1.viewport and place_v1.viewport.high
                else None
            )
            viewport_ne_lng = (
                place_v1.viewport.high.longitude
                if place_v1.viewport and place_v1.viewport.high
                else None
            )
            viewport_sw_lat = (
                place_v1.viewport.low.latitude
                if place_v1.viewport and place_v1.viewport.low
                else None
            )
            viewport_sw_lng = (
                place_v1.viewport.low.longitude
                if place_v1.viewport and place_v1.viewport.low
                else None
            )

            # Extract hours data
            hours_summary = []
            hours_data = {}
            if place_v1.opening_hours:
                if place_v1.opening_hours.weekday_descriptions:
                    hours_summary = (
                        place_v1.opening_hours.weekday_descriptions
                    )
                if place_v1.opening_hours.periods:
                    hours_data = {
                        "periods": [
                            {"open": p.open, "close": p.close}
                            for p in place_v1.opening_hours.periods
                        ]
                    }

            # Extract photos
            photo_references = []
            if place_v1.photos:
                photo_references = [p.name for p in place_v1.photos]

            # Extract plus code
            plus_code = ""
            if (
                place_v1.plus_code
                and place_v1.plus_code.global_code
            ):
                plus_code = place_v1.plus_code.global_code

            # Determine matched_fields from metadata
            matched_fields = []
            if metadata.merchant_name:
                matched_fields.append("name")
            if metadata.address:
                matched_fields.append("address")
            if metadata.phone_number:
                matched_fields.append("phone")
            matched_fields.append("place_id")

            # Create ReceiptPlace
            receipt_place = ReceiptPlace(
                image_id=metadata.image_id,
                receipt_id=metadata.receipt_id,
                place_id=metadata.place_id,
                merchant_name=metadata.merchant_name or "",
                merchant_category=place_v1.primary_type or "",
                merchant_types=place_v1.types or [],
                formatted_address=place_v1.formatted_address or "",
                short_address=place_v1.short_formatted_address or "",
                address_components={},
                latitude=latitude,
                longitude=longitude,
                geohash="",  # Auto-calculated
                viewport_ne_lat=viewport_ne_lat,
                viewport_ne_lng=viewport_ne_lng,
                viewport_sw_lat=viewport_sw_lat,
                viewport_sw_lng=viewport_sw_lng,
                plus_code=plus_code,
                phone_number=metadata.phone_number or "",
                phone_intl=place_v1.international_phone_number or "",
                website=place_v1.website_uri or "",
                maps_url=place_v1.google_maps_uri or "",
                business_status=place_v1.business_status or "",
                open_now=(
                    place_v1.opening_hours.open_now
                    if place_v1.opening_hours
                    else None
                ),
                hours_summary=hours_summary,
                hours_data=hours_data,
                photo_references=photo_references,
                matched_fields=matched_fields,
                validated_by=ValidationMethod.INFERENCE.value,
                validation_status=(
                    MerchantValidationStatus.MATCHED.value
                    if metadata.place_id
                    else MerchantValidationStatus.UNSURE.value
                ),
                confidence=0.95,  # High confidence for backfilled data
                reasoning=(
                    f"Backfilled from ReceiptMetadata "
                    f"({metadata.reasoning or 'no reasoning'})"
                ),
                timestamp=datetime.now(timezone.utc),
                places_api_version="v1",
            )

            # Write to DynamoDB (unless dry-run)
            if not dry_run:
                self.dynamo.add_receipt_place(receipt_place)

            self.stats.total_created += 1

            logger.debug(
                f"Backfilled ReceiptPlace for "
                f"{metadata.image_id[:8]}...#{metadata.receipt_id} "
                f"(place_id={metadata.place_id}, "
                f"lat={latitude}, lng={longitude})"
            )

        except Exception as e:
            logger.warning(
                f"Failed to backfill ReceiptPlace for "
                f"{metadata.image_id}#{metadata.receipt_id}: {e!s}"
            )
            self.stats.total_failed += 1
            self.stats.errors.append(
                f"{metadata.image_id}#{metadata.receipt_id}: {e!s}"
            )
            raise

    def print_summary(self) -> None:
        """Print backfill operation summary."""
        print("\n" + "=" * 70)
        print("RECEIPT PLACE BACKFILL SUMMARY")
        print("=" * 70)
        print(
            f"Total processed: {self.stats.total_processed}\n"
            f"  ✅ Created: {self.stats.total_created}\n"
            f"  ⏭️  Skipped: {self.stats.total_skipped}\n"
            f"  ❌ Failed:  {self.stats.total_failed}"
        )

        if self.stats.errors:
            print(f"\nErrors ({len(self.stats.errors)}):")
            for error in self.stats.errors[:10]:
                print(f"  - {error}")
            if len(self.stats.errors) > 10:
                print(f"  ... and {len(self.stats.errors) - 10} more")

        print("\nSuccess rate: {:.1f}%".format(
            (self.stats.total_created / max(self.stats.total_processed, 1))
            * 100
        ))
        print("=" * 70 + "\n")


async def main():
    """Main entry point for backfill script."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill ReceiptPlace entities from ReceiptMetadata"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: don't write to DynamoDB",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for processing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of receipts to backfill",
    )
    parser.add_argument(
        "--image-id",
        help="Backfill only receipts from this image",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        help="Backfill only this receipt (requires --image-id)",
    )

    args = parser.parse_args()

    # TODO: Initialize clients here
    # For now, this is a template that shows the structure
    logger.error("This is a template script. Need to initialize clients.")
    logger.info("To use this script:")
    logger.info("1. Initialize DynamoDB client")
    logger.info("2. Initialize Places API client")
    logger.info("3. Call backfiller.backfill_all()")

    return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
