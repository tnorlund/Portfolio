"""
Shared logic for combining receipts.

This module contains the core logic for combining multiple receipts into a single receipt.
It can be used by both the dev script and the Lambda handler.
"""

import copy
import json
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities import (
    CompactionRun,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

# Note: json import is used for serializing records to S3


# Import image processing (optional)
try:
    from PIL import Image as PIL_Image
    from PIL import ImageTransform as Transform

    from receipt_upload.cluster import reorder_box_points
    from receipt_upload.geometry.hull_operations import (
        box_points,
        min_area_rect,
    )
    from receipt_upload.utils import (
        calculate_sha256_from_bytes,
        upload_all_cdn_formats,
        upload_png_to_s3,
    )

    IMAGE_PROCESSING_AVAILABLE = True
except ImportError as e:
    import logging

    logging.getLogger().error(
        "Image processing imports failed: %s", e, exc_info=True
    )
    IMAGE_PROCESSING_AVAILABLE = False

# Import embedding functions (optional)
try:
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_label.embedding.line.realtime import embed_lines_realtime
    from receipt_label.embedding.word.realtime import embed_words_realtime
    from receipt_label.merchant_resolution.embeddings import upsert_embeddings

    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False


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
        dry_run: If True, don't save to DynamoDB

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
                from io import BytesIO

                import boto3

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

        combined_words = _combine_receipt_words_to_image_coords(
            client, image_id, receipt_ids, image_width, image_height
        )

        # Calculate min-area rect and bounds
        min_rect_result = _calculate_min_area_rect(
            combined_words, image_width, image_height
        )
        bounds = min_rect_result["bounds"]
        src_corners = min_rect_result[
            "src_corners"
        ]  # Source corners in image space
        warped_width = min_rect_result["warped_width"]
        warped_height = min_rect_result["warped_height"]

        # Create receipt records (before warping, so we can transform coordinates)
        records = _create_combined_receipt_records(
            image_id,
            new_receipt_id,
            combined_words,
            image,
            bounds,
            raw_bucket,
            site_bucket,
            receipt_ids,
            image_width,
            image_height,
            warped_width,
            warped_height,
            src_corners,
        )

        # Get receipt letters
        combined_letters = _combine_receipt_letters_to_image_coords(
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
        new_letter_id = 1
        for letter_data in combined_letters:
            # Transform letter corners from original image space to warped space
            letter_corners_ocr_warped = {}
            for corner_name in [
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ]:
                corner = letter_data.get(corner_name, {})
                x_ocr = corner.get("x", 0)
                y_ocr = corner.get("y", 0)
                # Convert to PIL space
                y_pil = image_height - y_ocr
                # Transform to warped space
                x_warped, y_warped = _transform_point_to_warped_space(
                    x_ocr, y_pil, src_corners, warped_width, warped_height
                )
                # Convert back to OCR space in warped image
                y_ocr_warped = warped_height - y_warped
                letter_corners_ocr_warped[corner_name] = (
                    x_warped,
                    y_ocr_warped,
                )

            # Calculate bounding box in warped space
            letter_min_x = min(
                c[0] for c in letter_corners_ocr_warped.values()
            )
            letter_max_x = max(
                c[0] for c in letter_corners_ocr_warped.values()
            )
            letter_min_y_ocr = min(
                c[1] for c in letter_corners_ocr_warped.values()
            )
            letter_max_y_ocr = max(
                c[1] for c in letter_corners_ocr_warped.values()
            )

            # Create ReceiptLetter with coordinates in warped OCR space, normalized (0-1)
            receipt_letter = ReceiptLetter(
                receipt_id=new_receipt_id,
                image_id=image_id,
                line_id=letter_data["new_line_id"],
                word_id=letter_data["new_word_id"],
                letter_id=new_letter_id,
                text=letter_data["text"],
                bounding_box={
                    "x": letter_min_x,  # Already in warped space, absolute pixels
                    "y": letter_min_y_ocr,
                    "width": letter_max_x - letter_min_x,
                    "height": letter_max_y_ocr - letter_min_y_ocr,
                },
                top_left={
                    "x": (
                        letter_corners_ocr_warped["top_left"][0]
                        / receipt_width
                        if receipt_width > 0
                        else 0.0
                    ),
                    "y": (
                        letter_corners_ocr_warped["top_left"][1]
                        / receipt_height
                        if receipt_height > 0
                        else 0.0
                    ),  # Normalize relative to bottom (OCR space)
                },
                top_right={
                    "x": (
                        letter_corners_ocr_warped["top_right"][0]
                        / receipt_width
                        if receipt_width > 0
                        else 1.0
                    ),
                    "y": (
                        letter_corners_ocr_warped["top_right"][1]
                        / receipt_height
                        if receipt_height > 0
                        else 0.0
                    ),
                },
                bottom_left={
                    "x": (
                        letter_corners_ocr_warped["bottom_left"][0]
                        / receipt_width
                        if receipt_width > 0
                        else 0.0
                    ),
                    "y": (
                        letter_corners_ocr_warped["bottom_left"][1]
                        / receipt_height
                        if receipt_height > 0
                        else 1.0
                    ),
                },
                bottom_right={
                    "x": (
                        letter_corners_ocr_warped["bottom_right"][0]
                        / receipt_width
                        if receipt_width > 0
                        else 1.0
                    ),
                    "y": (
                        letter_corners_ocr_warped["bottom_right"][1]
                        / receipt_height
                        if receipt_height > 0
                        else 1.0
                    ),
                },
                angle_degrees=letter_data.get("angle_degrees", 0.0),
                angle_radians=letter_data.get("angle_degrees", 0.0)
                * 3.141592653589793
                / 180.0,
                confidence=letter_data.get("confidence", 1.0),
            )
            records["receipt_letters"].append(receipt_letter)
            new_letter_id += 1

        # Create and upload combined receipt image using min-area rect warping
        combined_image = None
        if image:
            combined_image = _create_warped_receipt_image(
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
        best_metadata = _get_best_receipt_metadata(
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
                reasoning=f"Combined from receipts {receipt_ids}. Original: {best_metadata.reasoning or 'N/A'}",
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
        migrated_labels = _migrate_receipt_word_labels(
            client,
            image_id,
            receipt_ids,
            records["word_id_map"],
            records["line_id_map"],
            new_receipt_id,
        )

        # Create embeddings and ChromaDB deltas
        compaction_run = None
        if EMBEDDING_AVAILABLE and not dry_run:
            run_id = str(uuid.uuid4())
            delta_lines_dir = os.path.join(
                tempfile.gettempdir(), f"lines_{run_id}"
            )
            delta_words_dir = os.path.join(
                tempfile.gettempdir(), f"words_{run_id}"
            )

            line_client = ChromaClient(
                persist_directory=delta_lines_dir,
                mode="delta",
                metadata_only=True,
            )
            word_client = ChromaClient(
                persist_directory=delta_words_dir,
                mode="delta",
                metadata_only=True,
            )

            merchant_name = (
                receipt_metadata.merchant_name if receipt_metadata else None
            )

            upsert_embeddings(
                line_client=line_client,
                word_client=word_client,
                line_embed_fn=embed_lines_realtime,
                word_embed_fn=embed_words_realtime,
                ctx={
                    "lines": records["receipt_lines"],
                    "words": records["receipt_words"],
                },
                merchant_name=merchant_name,
            )

            lines_prefix = f"lines/delta/{run_id}/"
            words_prefix = f"words/delta/{run_id}/"

            lines_delta_key = line_client.persist_and_upload_delta(
                bucket=chromadb_bucket,
                s3_prefix=lines_prefix,
            )
            words_delta_key = word_client.persist_and_upload_delta(
                bucket=chromadb_bucket,
                s3_prefix=words_prefix,
            )

            compaction_run = CompactionRun(
                run_id=run_id,
                image_id=image_id,
                receipt_id=new_receipt_id,
                lines_delta_prefix=lines_delta_key,
                words_delta_prefix=words_delta_key,
            )

        # Initialize result dictionary early so we can add records_s3_key/bucket
        result = {}

        # Save receipt records as JSON to S3 for validation (even in dry_run mode)
        # This allows validation without committing to DynamoDB
        records_json = {
            "image_id": image_id,
            "new_receipt_id": new_receipt_id,
            "original_receipt_ids": receipt_ids,
            "receipt": dict(records["receipt"]),
            "receipt_lines": [dict(l) for l in records["receipt_lines"]],
            "receipt_words": [dict(w) for w in records["receipt_words"]],
            "receipt_letters": [dict(l) for l in records["receipt_letters"]],
            "receipt_metadata": (
                dict(receipt_metadata) if receipt_metadata else None
            ),
            "migrated_labels": [dict(l) for l in migrated_labels],
            "compaction_run": dict(compaction_run) if compaction_run else None,
            "bounds": bounds,
        }

        # Save to S3 (use batch_bucket parameter, environment, or chromadb_bucket as fallback)
        records_key = None
        records_bucket = None
        try:
            import boto3

            s3_client = boto3.client("s3")
            # Get batch bucket from parameter, environment (set by step function), or chromadb_bucket as fallback
            save_bucket = (
                batch_bucket
                or os.environ.get("BATCH_BUCKET")
                or chromadb_bucket
            )
            save_execution_id = execution_id or os.environ.get(
                "EXECUTION_ID", "unknown"
            )

            import logging

            logger = logging.getLogger()
            logger.info(
                f"Saving records JSON to S3: bucket={save_bucket}, execution_id={save_execution_id}"
            )

            records_key = f"receipts/{save_execution_id}/{image_id}_receipt_{new_receipt_id:05d}_records.json"
            s3_client.put_object(
                Bucket=save_bucket,
                Key=records_key,
                Body=json.dumps(records_json, default=str, indent=2),
                ContentType="application/json",
            )
            records_bucket = save_bucket
            logger.info(
                f"Successfully saved records JSON to s3://{save_bucket}/{records_key}"
            )
        except Exception as e:
            import logging

            logger = logging.getLogger()
            logger.error(
                f"Failed to save records JSON to S3: {e}", exc_info=True
            )

        # Always set the fields (even if None) so the handler can return them
        result["records_s3_key"] = records_key
        result["records_s3_bucket"] = records_bucket

        # Save to DynamoDB if not dry_run
        if not dry_run:
            client.add_receipt(records["receipt"])
            client.add_receipt_lines(records["receipt_lines"])
            client.add_receipt_words(records["receipt_words"])
            client.add_receipt_letters(records["receipt_letters"])
            if receipt_metadata:
                client.add_receipt_metadata(receipt_metadata)
            for label in migrated_labels:
                client.add_receipt_word_label(label)
            if compaction_run:
                client.add_compaction_run(compaction_run)

            # Export NDJSON and queue to stream processor (same as upload workflow)
            if artifacts_bucket and embed_ndjson_queue_url:
                try:
                    _export_receipt_ndjson_and_queue(
                        client=client,
                        artifacts_bucket=artifacts_bucket,
                        embed_ndjson_queue_url=embed_ndjson_queue_url,
                        image_id=image_id,
                        receipt_id=new_receipt_id,
                    )
                except Exception as e:
                    # Best-effort: do not fail main processing if NDJSON export fails
                    # Log error but continue
                    import logging

                    logger = logging.getLogger()
                    logger.error(
                        f"Failed to export NDJSON and queue for receipt {image_id}/{new_receipt_id}: {e}"
                    )

        # Build return dictionary with all fields, including records_s3_key/bucket from result
        return {
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

    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
        }


# Helper functions (extracted from dev.combine_receipts.py)
# These are marked with _ prefix to indicate they're internal


def _combine_receipt_words_to_image_coords(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
    image_width: int,
    image_height: int,
) -> List[Dict[str, Any]]:
    """Combine words from multiple receipts and transform to image coordinates."""
    all_words = []
    for receipt_id in receipt_ids:
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            receipt_words = client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            for word in receipt_words:
                try:
                    # Use the new Receipt entity method
                    transform_coeffs, receipt_width, receipt_height = (
                        receipt.get_transform_to_image(
                            image_width, image_height
                        )
                    )
                    word_copy = copy.deepcopy(word)
                    from receipt_upload.geometry.transformations import (
                        invert_warp,
                    )

                    forward_coeffs = invert_warp(*transform_coeffs)
                    # ReceiptWord coordinates are in OCR space (y=0 at bottom), normalized 0-1
                    # The transform destination is in PIL space (y=0 at top)
                    # So we need flip_y=True to convert from OCR space to PIL space during transform
                    word_copy.warp_transform(
                        *forward_coeffs,
                        src_width=image_width,
                        src_height=image_height,
                        dst_width=receipt_width,
                        dst_height=receipt_height,
                        flip_y=True,  # Receipt coords are in OCR space (y=0 at bottom), need to flip to PIL space
                    )
                    centroid = word_copy.calculate_centroid()
                    # After warp_transform, word_copy coordinates are always normalized (0-1) in image space
                    # We always need to multiply by image_width/height to get pixel coordinates
                    # The centroid check tells us if the word is within bounds (centroid <= 1.0) or outside (centroid > 1.0)
                    # But regardless, we need to convert normalized coords to pixel coords
                    if centroid[0] <= 1.0 and centroid[1] <= 1.0:
                        # Word is within image bounds - convert normalized to pixel
                        centroid_x = centroid[0] * image_width
                        centroid_y = centroid[1] * image_height
                        bounding_box = {
                            "x": word_copy.bounding_box["x"] * image_width,
                            "y": word_copy.bounding_box["y"] * image_height,
                            "width": word_copy.bounding_box["width"]
                            * image_width,
                            "height": word_copy.bounding_box["height"]
                            * image_height,
                        }
                        top_left = {
                            "x": word_copy.top_left["x"] * image_width,
                            "y": word_copy.top_left["y"] * image_height,
                        }
                        top_right = {
                            "x": word_copy.top_right["x"] * image_width,
                            "y": word_copy.top_right["y"] * image_height,
                        }
                        bottom_left = {
                            "x": word_copy.bottom_left["x"] * image_width,
                            "y": word_copy.bottom_left["y"] * image_height,
                        }
                        bottom_right = {
                            "x": word_copy.bottom_right["x"] * image_width,
                            "y": word_copy.bottom_right["y"] * image_height,
                        }
                    else:
                        # Word is outside image bounds, but coordinates are still normalized
                        # Convert normalized to pixel coordinates anyway
                        centroid_x = centroid[0] * image_width
                        centroid_y = centroid[1] * image_height
                        bounding_box = {
                            "x": word_copy.bounding_box["x"] * image_width,
                            "y": word_copy.bounding_box["y"] * image_height,
                            "width": word_copy.bounding_box["width"]
                            * image_width,
                            "height": word_copy.bounding_box["height"]
                            * image_height,
                        }
                        top_left = {
                            "x": word_copy.top_left["x"] * image_width,
                            "y": word_copy.top_left["y"] * image_height,
                        }
                        top_right = {
                            "x": word_copy.top_right["x"] * image_width,
                            "y": word_copy.top_right["y"] * image_height,
                        }
                        bottom_left = {
                            "x": word_copy.bottom_left["x"] * image_width,
                            "y": word_copy.bottom_left["y"] * image_height,
                        }
                        bottom_right = {
                            "x": word_copy.bottom_right["x"] * image_width,
                            "y": word_copy.bottom_right["y"] * image_height,
                        }

                    all_words.append(
                        {
                            "receipt_id": receipt_id,
                            "word_id": word.word_id,
                            "line_id": word.line_id,
                            "text": word.text,
                            "centroid_x": centroid_x,
                            "centroid_y": centroid_y,
                            "bounding_box": bounding_box,
                            "top_left": top_left,
                            "top_right": top_right,
                            "bottom_left": bottom_left,
                            "bottom_right": bottom_right,
                            "angle_degrees": word_copy.angle_degrees,
                            "confidence": word_copy.confidence,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

    all_words.sort(key=lambda w: (-w["centroid_y"], w["centroid_x"]))

    # Deduplicate words with identical coordinates (OCR duplicates)
    # This handles cases where the same word was detected on multiple lines
    # with identical bounding box coordinates
    deduplicated_words = []
    seen_coords = set()
    for word in all_words:
        # Create a coordinate signature for deduplication
        # Use a small tolerance for floating point comparison (0.1 pixels)
        coord_key = (
            round(word["top_left"]["x"], 1),
            round(word["top_left"]["y"], 1),
            round(word["top_right"]["x"], 1),
            round(word["top_right"]["y"], 1),
            round(word["bottom_left"]["x"], 1),
            round(word["bottom_left"]["y"], 1),
            round(word["bottom_right"]["x"], 1),
            round(word["bottom_right"]["y"], 1),
            word[
                "text"
            ],  # Also include text to avoid deduplicating different words at same location
        )

        if coord_key not in seen_coords:
            seen_coords.add(coord_key)
            deduplicated_words.append(word)
        # Skip duplicate - log if needed for debugging
        # Note: We keep the first occurrence (already sorted by reading order)

    return deduplicated_words


def _combine_receipt_letters_to_image_coords(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
    image_width: int,
    image_height: int,
    word_id_map: Dict[Tuple[int, int, int], int],
    line_id_map: Dict[Tuple[int, int], int],
) -> List[Dict[str, Any]]:
    """Combine letters from multiple receipts and transform to image coordinates."""
    all_letters = []
    for receipt_id in receipt_ids:
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            receipt_words = client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            for word in receipt_words:
                try:
                    receipt_letters = client.list_receipt_letters_from_word(
                        receipt_id, image_id, word.line_id, word.word_id
                    )
                    for letter in receipt_letters:
                        try:
                            # Use the new Receipt entity method
                            transform_coeffs, receipt_width, receipt_height = (
                                receipt.get_transform_to_image(
                                    image_width, image_height
                                )
                            )
                            letter_copy = copy.deepcopy(letter)
                            from receipt_upload.geometry.transformations import (
                                invert_warp,
                            )

                            forward_coeffs = invert_warp(*transform_coeffs)
                            # ReceiptLetter coordinates are in OCR space (y=0 at bottom), normalized 0-1
                            # The transform destination is in PIL space (y=0 at top)
                            # So we need flip_y=True to convert from OCR space to PIL space during transform
                            letter_copy.warp_transform(
                                *forward_coeffs,
                                src_width=image_width,
                                src_height=image_height,
                                dst_width=receipt_width,
                                dst_height=receipt_height,
                                flip_y=True,  # Receipt coords are in OCR space (y=0 at bottom), need to flip to PIL space
                            )
                            original_key = (
                                word.word_id,
                                word.line_id,
                                receipt_id,
                            )
                            new_word_id = word_id_map.get(original_key)
                            new_line_id = line_id_map.get(
                                (word.line_id, receipt_id)
                            )
                            if new_word_id is None or new_line_id is None:
                                continue

                            centroid = letter_copy.calculate_centroid()
                            # After warp_transform, letter_copy coordinates are always normalized (0-1) in image space
                            # We always need to multiply by image_width/height to get pixel coordinates
                            # The centroid check tells us if the letter is within bounds (centroid <= 1.0) or outside (centroid > 1.0)
                            # But regardless, we need to convert normalized coords to pixel coords
                            if centroid[0] <= 1.0 and centroid[1] <= 1.0:
                                # Letter is within image bounds - convert normalized to pixel
                                centroid_x = centroid[0] * image_width
                                centroid_y = centroid[1] * image_height
                                bounding_box = {
                                    "x": letter_copy.bounding_box["x"]
                                    * image_width,
                                    "y": letter_copy.bounding_box["y"]
                                    * image_height,
                                    "width": letter_copy.bounding_box["width"]
                                    * image_width,
                                    "height": letter_copy.bounding_box[
                                        "height"
                                    ]
                                    * image_height,
                                }
                                top_left = {
                                    "x": letter_copy.top_left["x"]
                                    * image_width,
                                    "y": letter_copy.top_left["y"]
                                    * image_height,
                                }
                                top_right = {
                                    "x": letter_copy.top_right["x"]
                                    * image_width,
                                    "y": letter_copy.top_right["y"]
                                    * image_height,
                                }
                                bottom_left = {
                                    "x": letter_copy.bottom_left["x"]
                                    * image_width,
                                    "y": letter_copy.bottom_left["y"]
                                    * image_height,
                                }
                                bottom_right = {
                                    "x": letter_copy.bottom_right["x"]
                                    * image_width,
                                    "y": letter_copy.bottom_right["y"]
                                    * image_height,
                                }
                            else:
                                # Letter is outside image bounds, but coordinates are still normalized
                                # Convert normalized to pixel coordinates anyway
                                centroid_x = centroid[0] * image_width
                                centroid_y = centroid[1] * image_height
                                bounding_box = {
                                    "x": letter_copy.bounding_box["x"]
                                    * image_width,
                                    "y": letter_copy.bounding_box["y"]
                                    * image_height,
                                    "width": letter_copy.bounding_box["width"]
                                    * image_width,
                                    "height": letter_copy.bounding_box[
                                        "height"
                                    ]
                                    * image_height,
                                }
                                top_left = {
                                    "x": letter_copy.top_left["x"]
                                    * image_width,
                                    "y": letter_copy.top_left["y"]
                                    * image_height,
                                }
                                top_right = {
                                    "x": letter_copy.top_right["x"]
                                    * image_width,
                                    "y": letter_copy.top_right["y"]
                                    * image_height,
                                }
                                bottom_left = {
                                    "x": letter_copy.bottom_left["x"]
                                    * image_width,
                                    "y": letter_copy.bottom_left["y"]
                                    * image_height,
                                }
                                bottom_right = {
                                    "x": letter_copy.bottom_right["x"]
                                    * image_width,
                                    "y": letter_copy.bottom_right["y"]
                                    * image_height,
                                }

                            all_letters.append(
                                {
                                    "receipt_id": receipt_id,
                                    "letter_id": letter.letter_id,
                                    "word_id": word.word_id,
                                    "line_id": word.line_id,
                                    "new_word_id": new_word_id,
                                    "new_line_id": new_line_id,
                                    "text": letter.text,
                                    "centroid_x": centroid_x,
                                    "centroid_y": centroid_y,
                                    "bounding_box": bounding_box,
                                    "top_left": top_left,
                                    "top_right": top_right,
                                    "bottom_left": bottom_left,
                                    "bottom_right": bottom_right,
                                    "angle_degrees": letter_copy.angle_degrees,
                                    "confidence": letter_copy.confidence,
                                }
                            )
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            continue

    return all_letters


def _get_best_receipt_metadata(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
) -> Optional[ReceiptMetadata]:
    """Get the best ReceiptMetadata from the original receipts."""
    metadatas = []
    for receipt_id in receipt_ids:
        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if (
                metadata
                and metadata.merchant_name
                and metadata.merchant_name.strip()
            ):
                metadatas.append(metadata)
        except Exception:
            pass

    if not metadatas:
        return None

    def score_metadata(meta: ReceiptMetadata) -> int:
        score = 0
        if meta.place_id and meta.place_id.strip():
            score += 10
        if meta.merchant_name and meta.merchant_name.strip():
            score += 5
        if meta.address and meta.address.strip():
            score += 3
        if meta.phone_number and meta.phone_number.strip():
            score += 2
        if meta.validation_status == MerchantValidationStatus.MATCHED.value:
            score += 5
        elif meta.validation_status == MerchantValidationStatus.UNSURE.value:
            score += 2
        return score

    metadatas.sort(
        key=lambda m: (score_metadata(m), m.timestamp), reverse=True
    )
    return metadatas[0]


def _migrate_receipt_word_labels(
    client: DynamoClient,
    image_id: str,
    original_receipt_ids: List[int],
    word_id_map: Dict[Tuple[int, int, int], int],
    line_id_map: Dict[Tuple[int, int], int],
    new_receipt_id: int,
) -> List[ReceiptWordLabel]:
    """Migrate ReceiptWordLabel entities from original receipts to the new combined receipt."""
    new_labels = []
    for receipt_id in original_receipt_ids:
        try:
            labels, _ = client.list_receipt_word_labels_for_receipt(
                image_id, receipt_id
            )
            for label in labels:
                original_key = (label.word_id, label.line_id, receipt_id)
                new_word_id = word_id_map.get(original_key)
                new_line_id = line_id_map.get((label.line_id, receipt_id))
                if new_word_id is None or new_line_id is None:
                    continue
                new_label = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=new_receipt_id,
                    line_id=new_line_id,
                    word_id=new_word_id,
                    label=label.label,
                    reasoning=label.reasoning
                    or f"Migrated from receipt {receipt_id}, word {label.word_id}",
                    timestamp_added=datetime.now(timezone.utc),
                    validation_status=label.validation_status,
                    label_proposed_by=label.label_proposed_by
                    or "receipt_combination",
                    label_consolidated_from=f"receipt_{receipt_id}_word_{label.word_id}",
                )
                new_labels.append(new_label)
        except Exception:
            continue
    return new_labels


def _calculate_min_area_rect(
    words: List[Dict[str, Any]], image_width: int, image_height: int
) -> Dict[str, Any]:
    """
    Calculate the minimum-area rectangle covering all words and return bounds + warping info.

    Returns:
        Dict with:
            - bounds: OCR-space bounds dict (for Receipt entity)
            - src_corners: Source corners in PIL image space (y=0 at top) for warping
            - warped_width: Width of warped image
            - warped_height: Height of warped image
    """
    if not words:
        raise ValueError("No words provided to calculate bounds")

    # Collect all corner points in PIL image space (y=0 at top)
    # Words are currently in OCR space (y=0 at bottom), so we need to flip Y
    all_points = []
    for w in words:
        # Convert OCR space (y=0 at bottom) to PIL space (y=0 at top)
        for corner_name in [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]:
            corner = w.get(corner_name, {})
            x = corner.get("x", 0)
            y_ocr = corner.get("y", 0)
            y_pil = image_height - y_ocr  # Flip Y: PIL space has y=0 at top
            all_points.append((x, y_pil))

    # Compute minimum-area rectangle
    (cx, cy), (rw, rh), angle_deg = min_area_rect(all_points)

    # Ensure portrait orientation (width < height)
    if rw > rh:
        angle_deg -= 90.0
        rw, rh = rh, rw

    # Get the 4 corners of the min-area rect
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    src_corners_ordered = reorder_box_points(
        box_4
    )  # [top-left, top-right, bottom-right, bottom-left]

    # Calculate warped dimensions
    warped_width = int(round(rw))
    warped_height = int(round(rh))

    # Ensure minimum dimensions
    if warped_width < 1:
        warped_width = 1
    if warped_height < 1:
        warped_height = 1

    # Convert src_corners back to OCR space for bounds dict (y=0 at bottom)
    # Bounds are used for Receipt entity coordinates
    bounds = {
        "top_left": {
            "x": src_corners_ordered[0][0],
            "y": image_height
            - src_corners_ordered[0][1],  # Convert back to OCR space
        },
        "top_right": {
            "x": src_corners_ordered[1][0],
            "y": image_height - src_corners_ordered[1][1],
        },
        "bottom_right": {
            "x": src_corners_ordered[2][0],
            "y": image_height - src_corners_ordered[2][1],
        },
        "bottom_left": {
            "x": src_corners_ordered[3][0],
            "y": image_height - src_corners_ordered[3][1],
        },
    }

    return {
        "bounds": bounds,
        "src_corners": src_corners_ordered,  # In PIL space for warping
        "warped_width": warped_width,
        "warped_height": warped_height,
    }


def _create_warped_receipt_image(
    image: Any,
    src_corners: List[Tuple[float, float]],
    warped_width: int,
    warped_height: int,
) -> Optional[Any]:
    """
    Create a warped/rectified image using perspective transform.

    Args:
        image: PIL Image in original space
        src_corners: Source corners in PIL image space [top-left, top-right, bottom-right, bottom-left]
        warped_width: Width of destination rectified image
        warped_height: Height of destination rectified image

    Returns:
        Warped PIL Image or None if warping fails
    """
    if not IMAGE_PROCESSING_AVAILABLE:
        return None

    try:
        from receipt_upload.geometry.transformations import (
            find_perspective_coeffs,
        )

        # Destination corners for rectified rectangle (in order: top-left, top-right, bottom-right, bottom-left)
        dst_corners = [
            (0.0, 0.0),
            (float(warped_width - 1), 0.0),
            (float(warped_width - 1), float(warped_height - 1)),
            (0.0, float(warped_height - 1)),
        ]

        # Compute perspective transform coefficients
        transform_coeffs = find_perspective_coeffs(
            src_points=src_corners,
            dst_points=dst_corners,
        )

        # Warp the image
        warped_img = image.transform(
            (warped_width, warped_height),
            Transform.PERSPECTIVE,
            transform_coeffs,
            resample=PIL_Image.BICUBIC,
        )
        return warped_img
    except Exception as e:
        import logging

        logger = logging.getLogger()
        logger.error(f"Failed to warp receipt image: {e}", exc_info=True)
        return None


def _transform_point_to_warped_space(
    x: float,
    y: float,
    src_corners: List[Tuple[float, float]],
    warped_width: int,
    warped_height: int,
) -> Tuple[float, float]:
    """
    Transform a point from original image space to warped/rectified space.

    Uses the perspective transform defined by src_corners -> dst_corners.
    Since PIL uses backward mapping (dst -> src), we need to solve for the forward mapping.

    Args:
        x, y: Point in original image space (PIL space, y=0 at top)
        src_corners: Source corners [top-left, top-right, bottom-right, bottom-left]
        warped_width, warped_height: Dimensions of warped image

    Returns:
        (x_warped, y_warped) in rectified space
    """
    from receipt_upload.geometry.transformations import (
        find_perspective_coeffs,
        invert_warp,
    )

    # Destination corners for rectified rectangle
    dst_corners = [
        (0.0, 0.0),
        (float(warped_width - 1), 0.0),
        (float(warped_width - 1), float(warped_height - 1)),
        (0.0, float(warped_height - 1)),
    ]

    # Get backward transform coefficients (dst -> src)
    backward_coeffs = find_perspective_coeffs(
        src_points=dst_corners,  # Note: swapped for backward mapping
        dst_points=src_corners,
    )

    # Invert to get forward transform (src -> dst)
    forward_coeffs = invert_warp(*backward_coeffs)

    # Apply forward transform: x_dst = (a*x_src + b*y_src + c) / (1 + g*x_src + h*y_src)
    a, b, c, d, e, f, g, h = forward_coeffs
    denom = 1.0 + g * x + h * y
    if abs(denom) < 1e-10:
        # Degenerate case, return original coordinates
        return (x, y)

    x_warped = (a * x + b * y + c) / denom
    y_warped = (d * x + e * y + f) / denom

    return (x_warped, y_warped)


def _create_combined_receipt_records(
    image_id: str,
    new_receipt_id: int,
    combined_words: List[Dict[str, Any]],
    image: Optional[Any],  # pylint: disable=unused-argument
    bounds: Dict[str, Any],
    raw_bucket: str,
    site_bucket: str,
    original_receipt_ids: List[int],  # pylint: disable=unused-argument
    image_width: int,
    image_height: int,
    warped_width: int,
    warped_height: int,
    src_corners: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """Create all DynamoDB entities for the combined receipt with coordinates in warped space."""
    # Use warped dimensions for receipt
    receipt_width = warped_width
    receipt_height = warped_height

    # Transform receipt corners from original image space to warped space
    # Convert OCR space bounds to PIL space for transformation
    top_left_ocr = bounds["top_left"]
    top_right_ocr = bounds["top_right"]
    bottom_left_ocr = bounds["bottom_left"]
    bottom_right_ocr = bounds["bottom_right"]

    # Convert to PIL space (y=0 at top)
    top_left_pil = (top_left_ocr["x"], image_height - top_left_ocr["y"])
    top_right_pil = (top_right_ocr["x"], image_height - top_right_ocr["y"])
    bottom_left_pil = (
        bottom_left_ocr["x"],
        image_height - bottom_left_ocr["y"],
    )
    bottom_right_pil = (
        bottom_right_ocr["x"],
        image_height - bottom_right_ocr["y"],
    )

    # Transform to warped space
    top_left_warped = _transform_point_to_warped_space(
        top_left_pil[0],
        top_left_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    top_right_warped = _transform_point_to_warped_space(
        top_right_pil[0],
        top_right_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    bottom_left_warped = _transform_point_to_warped_space(
        bottom_left_pil[0],
        bottom_left_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    bottom_right_warped = _transform_point_to_warped_space(
        bottom_right_pil[0],
        bottom_right_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )

    # Create Receipt entity with corners in warped space, normalized (0-1)
    # Receipt coordinates use OCR space (y=0 at bottom), so convert back
    receipt = Receipt(
        image_id=image_id,
        receipt_id=new_receipt_id,
        width=receipt_width,
        height=receipt_height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png",  # Will be updated if image is uploaded
        # Receipt corners in normalized warped space (0-1), OCR coordinate system (y=0 at bottom)
        top_left={
            "x": (
                top_left_warped[0] / warped_width if warped_width > 0 else 0.0
            ),
            "y": (
                (warped_height - top_left_warped[1]) / warped_height
                if warped_height > 0
                else 0.0
            ),  # Convert to OCR space
        },
        top_right={
            "x": (
                top_right_warped[0] / warped_width if warped_width > 0 else 1.0
            ),
            "y": (
                (warped_height - top_right_warped[1]) / warped_height
                if warped_height > 0
                else 0.0
            ),
        },
        bottom_left={
            "x": (
                bottom_left_warped[0] / warped_width
                if warped_width > 0
                else 0.0
            ),
            "y": (
                (warped_height - bottom_left_warped[1]) / warped_height
                if warped_height > 0
                else 1.0
            ),
        },
        bottom_right={
            "x": (
                bottom_right_warped[0] / warped_width
                if warped_width > 0
                else 1.0
            ),
            "y": (
                (warped_height - bottom_right_warped[1]) / warped_height
                if warped_height > 0
                else 1.0
            ),
        },
        sha256=None,  # Will be calculated and set when image is uploaded
        cdn_s3_bucket=site_bucket,
    )

    # Group words by line
    words_by_line = defaultdict(list)
    for word in combined_words:
        line_key = (word["line_id"], word["receipt_id"])
        words_by_line[line_key].append(word)

    # Create ReceiptLine entities
    receipt_lines = []
    line_id_map = {}
    new_line_id = 1

    for (original_line_id, original_receipt_id), line_words in sorted(
        words_by_line.items()
    ):
        # Sort words in line by x coordinate
        line_words_sorted = sorted(line_words, key=lambda w: w["centroid_x"])

        # Collect all corners for this line and transform to warped space
        line_corners_ocr = []
        for w in line_words_sorted:
            # Convert OCR space to PIL space, transform, then back to OCR space
            for corner_name in [
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ]:
                corner = w.get(corner_name, {})
                x_ocr = corner.get("x", 0)
                y_ocr = corner.get("y", 0)
                # Convert to PIL space
                y_pil = image_height - y_ocr
                # Transform to warped space
                x_warped, y_warped = _transform_point_to_warped_space(
                    x_ocr, y_pil, src_corners, warped_width, warped_height
                )
                # Convert back to OCR space in warped image
                y_ocr_warped = warped_height - y_warped
                line_corners_ocr.append((x_warped, y_ocr_warped))

        # Calculate line bounding box in warped space
        line_min_x = min(c[0] for c in line_corners_ocr)
        line_max_x = max(c[0] for c in line_corners_ocr)
        line_min_y_ocr = min(
            c[1] for c in line_corners_ocr
        )  # Bottom of line in OCR space
        line_max_y_ocr = max(
            c[1] for c in line_corners_ocr
        )  # Top of line in OCR space

        # Create line text
        line_text = " ".join(w["text"] for w in line_words_sorted)

        # Create ReceiptLine with coordinates in warped OCR space, normalized (0-1)
        receipt_line = ReceiptLine(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=new_line_id,
            text=line_text,
            bounding_box={
                "x": line_min_x,  # Already in warped space, absolute pixels
                "y": line_min_y_ocr,
                "width": line_max_x - line_min_x,
                "height": line_max_y_ocr - line_min_y_ocr,
            },
            top_left={
                "x": line_min_x / receipt_width if receipt_width > 0 else 0.0,
                "y": (
                    line_max_y_ocr / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),  # Normalize relative to bottom (OCR space)
            },
            top_right={
                "x": line_max_x / receipt_width if receipt_width > 0 else 1.0,
                "y": (
                    line_max_y_ocr / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),
            },
            bottom_left={
                "x": line_min_x / receipt_width if receipt_width > 0 else 0.0,
                "y": (
                    line_min_y_ocr / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            bottom_right={
                "x": line_max_x / receipt_width if receipt_width > 0 else 1.0,
                "y": (
                    line_min_y_ocr / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
        )

        receipt_lines.append(receipt_line)
        line_id_map[(original_line_id, original_receipt_id)] = new_line_id
        new_line_id += 1

    # Create ReceiptWord entities
    receipt_words = []
    word_id_map = {}
    new_word_id = 1

    for word in combined_words:
        original_key = (word["word_id"], word["line_id"], word["receipt_id"])
        new_line_id = line_id_map.get((word["line_id"], word["receipt_id"]), 1)

        # Transform word corners from original image space to warped space
        word_corners_ocr_warped = {}
        for corner_name in [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]:
            corner = word.get(corner_name, {})
            x_ocr = corner.get("x", 0)
            y_ocr = corner.get("y", 0)
            # Convert to PIL space
            y_pil = image_height - y_ocr
            # Transform to warped space
            x_warped, y_warped = _transform_point_to_warped_space(
                x_ocr, y_pil, src_corners, warped_width, warped_height
            )
            # Convert back to OCR space in warped image
            y_ocr_warped = warped_height - y_warped
            word_corners_ocr_warped[corner_name] = (x_warped, y_ocr_warped)

        # Calculate bounding box in warped space
        word_min_x = min(c[0] for c in word_corners_ocr_warped.values())
        word_max_x = max(c[0] for c in word_corners_ocr_warped.values())
        word_min_y_ocr = min(c[1] for c in word_corners_ocr_warped.values())
        word_max_y_ocr = max(c[1] for c in word_corners_ocr_warped.values())

        # Create ReceiptWord with coordinates in warped OCR space, normalized (0-1)
        receipt_word = ReceiptWord(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=new_line_id,
            word_id=new_word_id,
            text=word["text"],
            bounding_box={
                "x": word_min_x,  # Already in warped space, absolute pixels
                "y": word_min_y_ocr,
                "width": word_max_x - word_min_x,
                "height": word_max_y_ocr - word_min_y_ocr,
            },
            top_left={
                "x": (
                    word_corners_ocr_warped["top_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    word_corners_ocr_warped["top_left"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),  # Normalize relative to bottom (OCR space)
            },
            top_right={
                "x": (
                    word_corners_ocr_warped["top_right"][0] / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    word_corners_ocr_warped["top_right"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),
            },
            bottom_left={
                "x": (
                    word_corners_ocr_warped["bottom_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    word_corners_ocr_warped["bottom_left"][1] / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            bottom_right={
                "x": (
                    word_corners_ocr_warped["bottom_right"][0] / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    word_corners_ocr_warped["bottom_right"][1] / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            angle_degrees=word.get("angle_degrees", 0.0),
            angle_radians=word.get("angle_degrees", 0.0)
            * 3.141592653589793
            / 180.0,
            confidence=word.get("confidence", 1.0),
        )

        receipt_words.append(receipt_word)
        word_id_map[original_key] = new_word_id
        new_word_id += 1

    return {
        "receipt": receipt,
        "receipt_lines": receipt_lines,
        "receipt_words": receipt_words,
        "receipt_letters": [],
        "line_id_map": line_id_map,
        "word_id_map": word_id_map,
        "letter_id_map": {},
    }


def _export_receipt_ndjson_and_queue(
    client: DynamoClient,
    artifacts_bucket: str,
    embed_ndjson_queue_url: Optional[str],
    image_id: str,
    receipt_id: int,
) -> None:
    """
    Export receipt lines and words to NDJSON files and queue for stream processor.

    This matches the upload workflow pattern from process_ocr_results.py.
    If embed_ndjson_queue_url is None or empty, NDJSON files are still uploaded but not queued.
    """
    if not embed_ndjson_queue_url:
        import logging

        logger = logging.getLogger()
        logger.info("EMBED_NDJSON_QUEUE_URL not set; skipping embedding queue")
        return

    # Fetch authoritative words/lines from DynamoDB (just saved)
    receipt_words = client.list_receipt_words_from_receipt(
        image_id, receipt_id
    )
    receipt_lines = client.list_receipt_lines_from_receipt(
        image_id, receipt_id
    )

    prefix = f"receipts/{image_id}/receipt-{receipt_id:05d}/"
    lines_key = prefix + "lines.ndjson"
    words_key = prefix + "words.ndjson"

    # Serialize full dataclass objects so the consumer can rehydrate with
    # ReceiptLine(**d)/ReceiptWord(**d) preserving geometry and methods
    line_rows = [dict(l) for l in (receipt_lines or [])]
    word_rows = [dict(w) for w in (receipt_words or [])]

    # Upload NDJSON files to S3
    s3_client = boto3.client("s3")

    # Upload lines NDJSON
    lines_ndjson_content = "\n".join(
        json.dumps(row, default=str) for row in line_rows
    )
    s3_client.put_object(
        Bucket=artifacts_bucket,
        Key=lines_key,
        Body=lines_ndjson_content.encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    # Upload words NDJSON
    words_ndjson_content = "\n".join(
        json.dumps(row, default=str) for row in word_rows
    )
    s3_client.put_object(
        Bucket=artifacts_bucket,
        Key=words_key,
        Body=words_ndjson_content.encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    # Enqueue for batched embedding from NDJSON via SQS
    sqs_client = boto3.client("sqs")
    payload = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "artifacts_bucket": artifacts_bucket,
        "lines_key": lines_key,
        "words_key": words_key,
    }
    sqs_client.send_message(
        QueueUrl=embed_ndjson_queue_url,
        MessageBody=json.dumps(payload),
    )
