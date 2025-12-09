"""
Shared logic for combining receipts.

This module contains the core logic for combining multiple receipts into a single receipt.
It can be used by both the dev script and the Lambda handler.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities import ReceiptMetadata

# Import image processing (optional)
try:
    from PIL import Image as PIL_Image

    from receipt_upload.utils import (
        calculate_sha256_from_bytes,
        upload_all_cdn_formats,
        upload_png_to_s3,
    )

    IMAGE_PROCESSING_AVAILABLE = True
except ImportError as e:
    logging.getLogger().error(
        "Image processing imports failed: %s", e, exc_info=True
    )
    IMAGE_PROCESSING_AVAILABLE = False

# Import helper modules
# Use local embedding_utils instead of package version to ensure we use upload_bundled_delta_to_s3
from embedding_utils import create_embeddings_and_compaction_run
from geometry_utils import calculate_min_area_rect, create_warped_receipt_image
from metadata_utils import (
    get_best_receipt_metadata,
    migrate_receipt_word_labels,
)
from records_builder import (
    combine_receipt_letters_to_image_coords,
    combine_receipt_words_to_image_coords,
    create_combined_receipt_records,
    create_receipt_letters_from_combined,
)
from s3_io import export_receipt_ndjson_and_queue, save_records_json_to_s3

logger = logging.getLogger()


def combine_receipts(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
    raw_bucket: str,
    site_bucket: str,
    chromadb_bucket: str,
    artifacts_bucket: Optional[str] = None,
    embed_ndjson_queue_url: Optional[str] = None,
    batch_bucket: Optional[str] = None,
    execution_id: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Combine multiple receipts into a single new receipt.

    This is the main function that orchestrates the entire combination process.

    Args:
        client: DynamoDB client
        image_id: Image ID containing the receipts
        receipt_ids: List of receipt IDs to combine
        raw_bucket: S3 bucket for raw images
        site_bucket: S3 bucket for CDN images
        chromadb_bucket: S3 bucket for ChromaDB deltas
        dry_run: If True, don't save to DynamoDB (and don't delete original receipts)

    Returns:
        Dict with:
            - new_receipt_id: ID of the new combined receipt
            - receipt: Receipt entity
            - receipt_lines: List of ReceiptLine entities
            - receipt_words: List of ReceiptWord entities
            - receipt_letters: List of ReceiptLetter entities
            - receipt_metadata: ReceiptMetadata entity (if available)
            - migrated_labels: List of ReceiptWordLabel entities
            - compaction_run: CompactionRun entity (if embeddings created)
            - status: "success" or "failed"
            - error: Error message if failed
            - deleted_receipts: List of deleted receipt IDs (if dry_run=False and compaction succeeded)
    """
    try:
        # Get image
        image_entity = client.get_image(image_id)
        image_width = image_entity.width
        image_height = image_entity.height

        # Get next available receipt ID
        all_receipts = client.get_receipts_from_image(image_id)
        existing_ids = {r.receipt_id for r in all_receipts}
        new_receipt_id = max(existing_ids) + 1 if existing_ids else 1

        # Download image if available
        image = None
        if IMAGE_PROCESSING_AVAILABLE:
            try:
                from io import (
                    BytesIO,  # pylint: disable=import-outside-toplevel
                )

                import boto3  # pylint: disable=import-outside-toplevel,reimported

                s3 = boto3.client("s3")
                response = s3.get_object(
                    Bucket=image_entity.raw_s3_bucket,
                    Key=image_entity.raw_s3_key,
                )
                image_data = response["Body"].read()
                image = PIL_Image.open(BytesIO(image_data))
            except Exception:
                pass  # Continue without image

        # Combine words from receipts
        if not IMAGE_PROCESSING_AVAILABLE:
            raise ValueError(
                "Image processing not available - transforms required"
            )

        combined_words = combine_receipt_words_to_image_coords(
            client, image_id, receipt_ids, image_width, image_height
        )

        # Calculate min-area rect and bounds
        min_rect_result = calculate_min_area_rect(
            combined_words, image_width, image_height
        )
        bounds = min_rect_result["bounds"]
        src_corners = min_rect_result[
            "src_corners"
        ]  # Source corners in image space
        warped_width = min_rect_result["warped_width"]
        warped_height = min_rect_result["warped_height"]

        # Create receipt records (before warping, so we can transform coordinates)
        records = create_combined_receipt_records(
            image_id,
            new_receipt_id,
            combined_words,
            bounds,
            raw_bucket,
            site_bucket,
            image_width,
            image_height,
            warped_width,
            warped_height,
            src_corners,
        )

        # Get receipt letters
        combined_letters = combine_receipt_letters_to_image_coords(
            client,
            image_id,
            receipt_ids,
            image_width,
            image_height,
            records["word_id_map"],
            records["line_id_map"],
        )

        # Create ReceiptLetter entities with coordinates transformed to warped space
        receipt_width = records["receipt"].width
        receipt_height = records["receipt"].height
        receipt_letters = create_receipt_letters_from_combined(
            combined_letters,
            new_receipt_id,
            image_id,
            receipt_width,
            receipt_height,
            image_height,
            warped_height,
            src_corners,
            warped_width,
        )
        records["receipt_letters"] = receipt_letters

        # Create and upload combined receipt image using min-area rect warping
        combined_image = None
        if image:
            combined_image = create_warped_receipt_image(
                image, src_corners, warped_width, warped_height
            )
            if combined_image:
                records["receipt"].width = combined_image.width
                records["receipt"].height = combined_image.height

                # Upload raw image to raw bucket
                raw_s3_key = f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png"
                upload_png_to_s3(combined_image, raw_bucket, raw_s3_key)
                records["receipt"].raw_s3_key = raw_s3_key

                # Upload CDN formats to site bucket (same pattern as upload workflow)
                receipt_cdn_keys = upload_all_cdn_formats(
                    combined_image,
                    site_bucket,
                    f"assets/{image_id}_RECEIPT_{new_receipt_id:05d}",
                    generate_thumbnails=True,
                )

                # Set all CDN keys on Receipt entity (matching upload workflow pattern)
                records["receipt"].cdn_s3_key = receipt_cdn_keys.get("jpeg")
                records["receipt"].cdn_webp_s3_key = receipt_cdn_keys.get(
                    "webp"
                )
                records["receipt"].cdn_avif_s3_key = receipt_cdn_keys.get(
                    "avif"
                )
                records["receipt"].cdn_thumbnail_s3_key = receipt_cdn_keys.get(
                    "jpeg_thumbnail"
                )
                records["receipt"].cdn_thumbnail_webp_s3_key = (
                    receipt_cdn_keys.get("webp_thumbnail")
                )
                records["receipt"].cdn_thumbnail_avif_s3_key = (
                    receipt_cdn_keys.get("avif_thumbnail")
                )
                records["receipt"].cdn_small_s3_key = receipt_cdn_keys.get(
                    "jpeg_small"
                )
                records["receipt"].cdn_small_webp_s3_key = (
                    receipt_cdn_keys.get("webp_small")
                )
                records["receipt"].cdn_small_avif_s3_key = (
                    receipt_cdn_keys.get("avif_small")
                )
                records["receipt"].cdn_medium_s3_key = receipt_cdn_keys.get(
                    "jpeg_medium"
                )
                records["receipt"].cdn_medium_webp_s3_key = (
                    receipt_cdn_keys.get("webp_medium")
                )
                records["receipt"].cdn_medium_avif_s3_key = (
                    receipt_cdn_keys.get("avif_medium")
                )

                # Calculate SHA256 from the combined image (same method as upload workflow)
                records["receipt"].sha256 = calculate_sha256_from_bytes(
                    combined_image.tobytes()
                )

        # Get best ReceiptMetadata
        best_metadata = get_best_receipt_metadata(
            client, image_id, receipt_ids
        )
        receipt_metadata = None
        if best_metadata:
            receipt_metadata = ReceiptMetadata(
                image_id=image_id,
                receipt_id=new_receipt_id,
                place_id=best_metadata.place_id or "",
                merchant_name=best_metadata.merchant_name or "",
                merchant_category=best_metadata.merchant_category or "",
                address=best_metadata.address or "",
                phone_number=best_metadata.phone_number or "",
                matched_fields=(
                    best_metadata.matched_fields.copy()
                    if best_metadata.matched_fields
                    else []
                ),
                validated_by=best_metadata.validated_by
                or ValidationMethod.TEXT_SEARCH.value,
                timestamp=datetime.now(timezone.utc),
                reasoning=(
                    f"Combined from receipts {receipt_ids}. "
                    f"Original: {best_metadata.reasoning or 'N/A'}"
                ),
                validation_status=best_metadata.validation_status
                or MerchantValidationStatus.MATCHED.value,
                canonical_place_id=best_metadata.canonical_place_id or "",
                canonical_merchant_name=best_metadata.canonical_merchant_name
                or "",
                canonical_address=best_metadata.canonical_address or "",
                canonical_phone_number=best_metadata.canonical_phone_number
                or "",
            )

        # Migrate labels
        migrated_labels = migrate_receipt_word_labels(
            client,
            image_id,
            receipt_ids,
            records["word_id_map"],
            records["line_id_map"],
            new_receipt_id,
        )

        # Create embeddings and ChromaDB deltas
        compaction_run = None
        if not dry_run:
            # Use local embedding_utils which uses upload_bundled_delta_to_s3
            # (correct format for compaction handler)
            compaction_run = create_embeddings_and_compaction_run(
                receipt_lines=records["receipt_lines"],
                receipt_words=records["receipt_words"],
                receipt_metadata=receipt_metadata,
                image_id=image_id,
                new_receipt_id=new_receipt_id,
                chromadb_bucket=chromadb_bucket,
            )

        # Save receipt records as JSON to S3 for validation
        # (even in dry_run mode)
        # This allows validation without committing to DynamoDB
        records_json = {
            "image_id": image_id,
            "new_receipt_id": new_receipt_id,
            "original_receipt_ids": receipt_ids,
            "receipt": dict(records["receipt"]),
            "receipt_lines": [dict(line) for line in records["receipt_lines"]],
            "receipt_words": [dict(word) for word in records["receipt_words"]],
            "receipt_letters": [
                dict(letter) for letter in records["receipt_letters"]
            ],
            "receipt_metadata": (
                dict(receipt_metadata) if receipt_metadata else None
            ),
            "migrated_labels": [dict(label) for label in migrated_labels],
            "compaction_run": dict(compaction_run) if compaction_run else None,
            "bounds": bounds,
        }

        # Save to S3
        result = save_records_json_to_s3(
            records_json,
            image_id,
            new_receipt_id,
            batch_bucket,
            chromadb_bucket,
            execution_id,
        )

        # Save to DynamoDB if not dry_run
        if not dry_run:
            client.add_receipt(records["receipt"])
            client.add_receipt_lines(records["receipt_lines"])
            client.add_receipt_words(records["receipt_words"])

            # Try to add letters, but if they already exist (from a previous failed attempt),
            # just skip them and continue
            try:
                client.add_receipt_letters(records["receipt_letters"])
            except Exception as e:  # pylint: disable=broad-except
                error_str = str(e)
                if (
                    "ConditionalCheckFailed" in error_str
                    or "already exists" in error_str.lower()
                ):
                    logger.warning(
                        "Some letters already exist for receipt %s/%s (likely from previous failed attempt), skipping letter addition",
                        image_id,
                        new_receipt_id,
                    )
                    # Letters already exist, which is fine - just skip adding them
                    # and continue with the rest of the process
                else:
                    # Re-raise if it's a different error
                    raise

            # Try to add metadata, but if it already exists (from a previous failed attempt),
            # delete it first and try again
            if receipt_metadata:
                try:
                    client.add_receipt_metadata(receipt_metadata)
                except Exception as e:  # pylint: disable=broad-except
                    error_str = str(e)
                    if (
                        "ConditionalCheckFailed" in error_str
                        or "already exists" in error_str.lower()
                    ):
                        logger.warning(
                            "Receipt metadata already exists for receipt %s/%s (likely from previous failed attempt), deleting and retrying",
                            image_id,
                            new_receipt_id,
                        )
                        # Delete the existing metadata (pass the ReceiptMetadata object)
                        try:
                            client.delete_receipt_metadata(receipt_metadata)
                        except (
                            Exception
                        ) as delete_err:  # pylint: disable=broad-except
                            logger.warning(
                                "Error deleting existing metadata (may not exist): %s",
                                delete_err,
                            )
                        # Try adding again
                        client.add_receipt_metadata(receipt_metadata)
                    else:
                        # Re-raise if it's a different error
                        raise
            for label in migrated_labels:
                client.add_receipt_word_label(label)
            if compaction_run:
                client.add_compaction_run(compaction_run)

            # Export NDJSON and queue to stream processor (same as upload workflow)
            if artifacts_bucket and embed_ndjson_queue_url:
                try:
                    export_receipt_ndjson_and_queue(
                        client=client,
                        artifacts_bucket=artifacts_bucket,
                        embed_ndjson_queue_url=embed_ndjson_queue_url,
                        image_id=image_id,
                        receipt_id=new_receipt_id,
                    )
                except Exception as e:  # pylint: disable=broad-except
                    # Best-effort: do not fail main processing if NDJSON export fails
                    # Log error but continue
                    logger.error(
                        "Failed to export NDJSON and queue for receipt %s/%s: %s",
                        image_id,
                        new_receipt_id,
                        e,
                        exc_info=True,
                    )

        # Step 3: Wait for compaction and delete original receipts (only if not dry_run)
        deleted_receipts = []
        if not dry_run and compaction_run:
            logger.info(
                "Waiting for compaction and deleting original receipts for %s/%s",
                image_id,
                new_receipt_id,
            )
            try:
                from receipt_agent.lifecycle.compaction_manager import (
                    wait_for_compaction,
                )
                from receipt_agent.lifecycle.receipt_manager import (
                    delete_receipt,
                )

                # Wait for compaction to complete
                logger.info(
                    "Waiting for compaction to complete for new receipt %s/%s",
                    image_id,
                    new_receipt_id,
                )
                wait_for_compaction(
                    client=client,
                    image_id=image_id,
                    receipt_id=new_receipt_id,
                    max_wait_seconds=300,  # 5 minutes - reduced for faster retries
                    poll_interval=5,  # Poll every 5 seconds for faster detection
                    initial_wait_seconds=15,  # Wait 15s for CompactionRun to appear
                )
                logger.info(
                    "Compaction completed for new receipt %s/%s",
                    image_id,
                    new_receipt_id,
                )

                # Delete original receipts in reverse order (highest ID first)
                # This follows the same pattern as dev.test_realtime_embeddings.py
                sorted_receipt_ids = sorted(receipt_ids, reverse=True)

                logger.info(
                    "About to delete %d original receipt(s) for %s/%s: %s (these were combined into the new receipt)",
                    len(sorted_receipt_ids),
                    image_id,
                    new_receipt_id,
                    sorted_receipt_ids,
                )

                for receipt_id in sorted_receipt_ids:
                    logger.info(
                        "Deleting original receipt %s/%s (was combined into %s/%s)",
                        image_id,
                        receipt_id,
                        image_id,
                        new_receipt_id,
                    )
                    try:
                        deletion_result = delete_receipt(
                            client=client,
                            image_id=image_id,
                            receipt_id=receipt_id,
                        )

                        if deletion_result.success:
                            logger.info(
                                "Successfully deleted receipt %s/%s",
                                image_id,
                                receipt_id,
                            )
                            deleted_receipts.append(receipt_id)
                        else:
                            logger.error(
                                "Failed to delete receipt %s/%s: %s",
                                image_id,
                                receipt_id,
                                deletion_result.error,
                            )
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error(
                            "Error deleting receipt %s/%s: %s",
                            image_id,
                            receipt_id,
                            e,
                            exc_info=True,
                        )

            except (TimeoutError, RuntimeError) as e:
                logger.error(
                    "Failed to wait for compaction for %s/%s: %s",
                    image_id,
                    new_receipt_id,
                    e,
                )
                # Continue anyway - don't fail the whole operation
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    "Error during deletion step for %s/%s: %s",
                    image_id,
                    new_receipt_id,
                    e,
                    exc_info=True,
                )
                # Continue anyway - don't fail the whole operation

        # Build return dictionary with all fields, including records_s3_key/bucket from result
        result_dict = {
            "new_receipt_id": new_receipt_id,
            "receipt": records["receipt"],
            "receipt_lines": records["receipt_lines"],
            "receipt_words": records["receipt_words"],
            "receipt_letters": records["receipt_letters"],
            "receipt_metadata": receipt_metadata,
            "migrated_labels": migrated_labels,
            "compaction_run": compaction_run,
            "status": "success",
            "records_s3_key": result.get("records_s3_key"),
            "records_s3_bucket": result.get("records_s3_bucket"),
        }

        if deleted_receipts:
            result_dict["deleted_receipts"] = deleted_receipts

        return result_dict

    except Exception as e:  # pylint: disable=broad-except
        logger.error("Error combining receipts: %s", e, exc_info=True)
        return {
            "status": "failed",
            "error": str(e),
        }
