"""
Merge Receipt Lambda Handler (Container Lambda)

Merges multiple receipt fragments into a single new receipt with proper
warping, re-runs embeddings, waits for compaction to complete, then deletes
the originals.

Input:
    {
        "image_id": "d5a15b22-d73e-4cec-b3bd-18ebb79a19b3",
        "receipt_ids": [2, 3],
        "dry_run": false
    }

Output:
    {
        "image_id": "...",
        "receipt_ids": [2, 3],
        "new_receipt_id": 4,
        "status": "success",
        "words_merged": 110,
        "labels_merged": 17,
        "compaction_run_id": "uuid",
        "deleted_receipts": [3, 2]
    }

Environment Variables:
    DYNAMODB_TABLE_NAME: DynamoDB table name
    RAW_BUCKET: S3 bucket for raw receipt images
    SITE_BUCKET: S3 bucket for CDN images
    CHROMADB_BUCKET: S3 bucket for ChromaDB snapshots
    OPENAI_API_KEY: OpenAI API key (for embeddings)
"""

import io
import logging
import os
from typing import Any

import boto3
from PIL import Image as PIL_Image

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler to merge receipt fragments into a single receipt.

    Steps:
    1. Validate input and read receipt details via GSI4
    2. Transform words to image space and calculate new bounding rect
    3. Download original image and create warped receipt image
    4. Upload warped image to S3 (raw + CDN variants)
    5. Create new Receipt/Line/Word/Letter entities in warped space
    6. Migrate labels and place data
    7. Write to DynamoDB
    8. Create embeddings and wait for compaction
    9. Delete original receipts
    """
    try:
        # Validate input
        image_id = event.get("image_id")
        receipt_ids = event.get("receipt_ids")
        dry_run = event.get("dry_run", False)

        if not image_id:
            return {"status": "error", "error": "Missing required field: image_id"}
        if not receipt_ids or not isinstance(receipt_ids, list) or len(receipt_ids) != 2:
            return {
                "status": "error",
                "error": "receipt_ids must be a list of exactly 2 receipt IDs",
            }

        logger.info(
            "Merging receipts: image_id=%s, receipt_ids=%s, dry_run=%s",
            image_id,
            receipt_ids,
            dry_run,
        )

        # Import here to avoid cold start overhead if validation fails
        from geometry_utils import calculate_min_area_rect, create_warped_receipt_image
        from metadata_utils import get_best_receipt_place, migrate_receipt_word_labels
        from receipt_agent.lifecycle.receipt_manager import delete_receipt
        from receipt_chroma.embedding.orchestration import (
            EmbeddingConfig,
            create_embeddings_and_compaction_run,
        )
        from receipt_dynamo import DynamoClient
        from receipt_upload.utils import upload_all_cdn_formats, upload_png_to_s3
        from records_builder import (
            combine_receipt_letters_to_image_coords,
            combine_receipt_words_to_image_coords,
            create_combined_receipt_records,
            create_receipt_letters_from_combined,
        )

        # Initialize clients
        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        raw_bucket = os.environ.get("RAW_BUCKET")
        site_bucket = os.environ.get("SITE_BUCKET")
        chromadb_bucket = os.environ.get("CHROMADB_BUCKET")

        if not table_name:
            return {"status": "error", "error": "DYNAMODB_TABLE_NAME not set"}
        if not raw_bucket:
            return {"status": "error", "error": "RAW_BUCKET not set"}
        if not site_bucket:
            return {"status": "error", "error": "SITE_BUCKET not set"}
        if not chromadb_bucket:
            return {"status": "error", "error": "CHROMADB_BUCKET not set"}

        client = DynamoClient(table_name=table_name)
        s3_client = boto3.client("s3")

        # ============================================================
        # Step 1: Read receipt details via GSI4 (2 queries total)
        # ============================================================
        logger.info("Reading receipt details via GSI4...")
        receipt_details = {}
        for rid in receipt_ids:
            details = client.get_receipt_details(image_id, rid)
            receipt_details[rid] = details
            logger.info(
                "Receipt %d: %d lines, %d words, %d labels, place=%s",
                rid,
                len(details.lines),
                len(details.words),
                len(details.labels),
                details.place.merchant_name if details.place else None,
            )

        # Get image dimensions
        image_entity = client.get_image(image_id)
        image_width = image_entity.width
        image_height = image_entity.height

        # Determine new receipt ID (max existing + 1)
        all_receipts = client.get_receipts_from_image(image_id)
        existing_ids = {r.receipt_id for r in all_receipts}
        new_receipt_id = max(existing_ids) + 1 if existing_ids else 1
        logger.info("New receipt ID: %d", new_receipt_id)

        # ============================================================
        # Step 2: Transform words to image space
        # ============================================================
        logger.info("Transforming words to image coordinates...")
        combined_words = combine_receipt_words_to_image_coords(
            client, image_id, receipt_ids, image_width, image_height
        )
        logger.info("Combined %d words (deduplicated)", len(combined_words))

        if not combined_words:
            return {
                "status": "error",
                "error": "No words found after combining receipts",
                "image_id": image_id,
                "receipt_ids": receipt_ids,
            }

        # ============================================================
        # Step 3: Calculate new bounding rectangle
        # ============================================================
        logger.info("Calculating min-area bounding rectangle...")
        rect_info = calculate_min_area_rect(
            combined_words, image_width, image_height
        )
        bounds = rect_info["bounds"]
        src_corners = rect_info["src_corners"]
        warped_width = rect_info["warped_width"]
        warped_height = rect_info["warped_height"]
        logger.info(
            "Warped dimensions: %dx%d", warped_width, warped_height
        )

        # ============================================================
        # Step 4: Download original image and create warped image
        # ============================================================
        logger.info(
            "Downloading original image from s3://%s/%s",
            image_entity.raw_s3_bucket,
            image_entity.raw_s3_key,
        )
        response = s3_client.get_object(
            Bucket=image_entity.raw_s3_bucket, Key=image_entity.raw_s3_key
        )
        image_bytes = response["Body"].read()
        original_image = PIL_Image.open(io.BytesIO(image_bytes))

        warped_image = create_warped_receipt_image(
            original_image, src_corners, warped_width, warped_height
        )
        if warped_image is None:
            return {
                "status": "error",
                "error": "Failed to create warped receipt image",
                "image_id": image_id,
                "receipt_ids": receipt_ids,
            }
        logger.info("Created warped image: %dx%d", warped_image.width, warped_image.height)

        # ============================================================
        # Step 5: Create DynamoDB entities in warped space
        # ============================================================
        logger.info("Creating receipt records in warped space...")
        records = create_combined_receipt_records(
            image_id=image_id,
            new_receipt_id=new_receipt_id,
            combined_words=combined_words,
            bounds=bounds,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            image_width=image_width,
            image_height=image_height,
            warped_width=warped_width,
            warped_height=warped_height,
            src_corners=src_corners,
        )

        receipt = records["receipt"]
        receipt_lines = records["receipt_lines"]
        receipt_words = records["receipt_words"]
        line_id_map = records["line_id_map"]
        word_id_map = records["word_id_map"]

        logger.info(
            "Created: 1 receipt, %d lines, %d words",
            len(receipt_lines),
            len(receipt_words),
        )

        # ============================================================
        # Step 6: Transform and create letters
        # ============================================================
        logger.info("Transforming letters to image coordinates...")
        combined_letters = combine_receipt_letters_to_image_coords(
            client, image_id, receipt_ids, image_width, image_height,
            word_id_map, line_id_map,
        )
        logger.info("Combined %d letters", len(combined_letters))

        receipt_letters = []
        if combined_letters:
            receipt_letters = create_receipt_letters_from_combined(
                combined_letters=combined_letters,
                new_receipt_id=new_receipt_id,
                image_id=image_id,
                receipt_width=warped_width,
                receipt_height=warped_height,
                image_height=image_height,
                warped_height=warped_height,
                src_corners=src_corners,
                warped_width=warped_width,
            )
            logger.info("Created %d letter entities", len(receipt_letters))

        # ============================================================
        # Step 7: Migrate labels
        # ============================================================
        logger.info("Migrating receipt word labels...")
        new_labels = migrate_receipt_word_labels(
            client, image_id, receipt_ids, word_id_map, line_id_map,
            new_receipt_id,
        )
        logger.info("Migrated %d labels", len(new_labels))

        # ============================================================
        # Step 8: Select best place
        # ============================================================
        logger.info("Selecting best receipt place...")
        best_place = get_best_receipt_place(client, image_id, receipt_ids)
        if best_place:
            # Update place to point to new receipt
            best_place.receipt_id = new_receipt_id
            logger.info("Best place: %s", best_place.merchant_name)
        else:
            logger.info("No place data found")

        # ============================================================
        # Build response for dry run
        # ============================================================
        result = {
            "image_id": image_id,
            "receipt_ids": receipt_ids,
            "new_receipt_id": new_receipt_id,
            "words_merged": len(receipt_words),
            "letters_merged": len(receipt_letters),
            "labels_merged": len(new_labels),
            "lines_created": len(receipt_lines),
            "warped_dimensions": f"{warped_width}x{warped_height}",
            "place": best_place.merchant_name if best_place else None,
        }

        if dry_run:
            result["status"] = "dry_run"
            logger.info("Dry run complete: %s", result)
            return result

        # ============================================================
        # Step 9: Upload warped image to S3
        # ============================================================
        raw_s3_key = f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png"
        logger.info("Uploading raw image to s3://%s/%s", raw_bucket, raw_s3_key)
        upload_png_to_s3(warped_image, raw_bucket, raw_s3_key)

        # Upload CDN variants
        cdn_base_key = f"assets/{image_id}_RECEIPT_{new_receipt_id:05d}"
        logger.info("Uploading CDN formats to s3://%s/%s.*", site_bucket, cdn_base_key)
        upload_all_cdn_formats(warped_image, site_bucket, cdn_base_key)

        # ============================================================
        # Step 10: Write to DynamoDB
        # ============================================================
        logger.info("Writing entities to DynamoDB...")
        client.add_receipt(receipt)
        logger.info("  Added receipt %d", new_receipt_id)

        if receipt_lines:
            client.add_receipt_lines(receipt_lines)
            logger.info("  Added %d lines", len(receipt_lines))

        if receipt_words:
            client.add_receipt_words(receipt_words)
            logger.info("  Added %d words", len(receipt_words))

        if receipt_letters:
            client.add_receipt_letters(receipt_letters)
            logger.info("  Added %d letters", len(receipt_letters))

        if new_labels:
            client.add_receipt_word_labels(new_labels)
            logger.info("  Added %d labels", len(new_labels))

        if best_place:
            client.add_receipt_place(best_place)
            logger.info("  Added place: %s", best_place.merchant_name)

        # ============================================================
        # Step 11: Create embeddings and compaction run
        # ============================================================
        logger.info("Creating embeddings and compaction run...")
        embedding_config = EmbeddingConfig(
            image_id=image_id,
            receipt_id=new_receipt_id,
            chromadb_bucket=chromadb_bucket,
            dynamo_client=client,
            receipt_place=best_place,
            receipt_word_labels=new_labels if new_labels else None,
        )

        # Filter out noise words for embedding (non-noise words only)
        non_noise_words = [w for w in receipt_words if not getattr(w, "is_noise", False)]
        if not non_noise_words:
            non_noise_words = receipt_words  # Fallback: use all words

        embedding_result = create_embeddings_and_compaction_run(
            receipt_lines=receipt_lines,
            receipt_words=non_noise_words,
            config=embedding_config,
        )
        compaction_run_id = embedding_result.compaction_run.run_id
        logger.info("CompactionRun created: %s", compaction_run_id)

        # ============================================================
        # Step 12: Wait for compaction to complete
        # ============================================================
        logger.info("Waiting for compaction to complete (max 300s)...")
        compaction_success = embedding_result.wait_for_compaction_to_finish(
            dynamo_client=client, max_wait_seconds=300, poll_interval_seconds=5
        )
        embedding_result.close()

        if not compaction_success:
            logger.warning("Compaction did not complete successfully")
            result["status"] = "partial"
            result["compaction_run_id"] = compaction_run_id
            result["compaction_status"] = "timeout_or_failed"
            result["deleted_receipts"] = []
            return result

        logger.info("Compaction completed successfully")

        # ============================================================
        # Step 13: Delete original receipts (highest ID first)
        # ============================================================
        deleted_receipts = []
        for rid in sorted(receipt_ids, reverse=True):
            logger.info("Deleting original receipt %d...", rid)
            deletion_result = delete_receipt(client, image_id, rid)
            if deletion_result.success:
                deleted_receipts.append(rid)
                logger.info("  Deleted receipt %d", rid)
            else:
                logger.error(
                    "  Failed to delete receipt %d: %s",
                    rid,
                    deletion_result.error,
                )

        result["status"] = "success"
        result["compaction_run_id"] = compaction_run_id
        result["deleted_receipts"] = deleted_receipts
        logger.info("Merge complete: %s", result)
        return result

    except Exception as e:
        logger.exception("Error merging receipts")
        return {
            "status": "error",
            "error": str(e),
            "image_id": event.get("image_id"),
            "receipt_ids": event.get("receipt_ids"),
        }
