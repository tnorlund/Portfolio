"""
Merge Receipt Lambda Handler (Container Lambda)

Merges multiple receipt fragments into a single new receipt with proper
warping, writes the merged receipt's native DynamoDB embeddings, then
deletes the originals (and their embedding items).

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
        "deleted_receipts": [3, 2]
    }

Environment Variables:
    DYNAMODB_TABLE_NAME: DynamoDB table name
    RAW_BUCKET: S3 bucket for raw receipt images
    SITE_BUCKET: S3 bucket for CDN images
    OPENAI_API_KEY: OpenAI API key (for embeddings)
    SUMMARY_QUEUE_URL: SQS queue for summary recomputation
    LINE_ITEM_QUEUE_URL: SQS queue for row/line-item recomputation
"""

import io
import json
import logging
import os
from dataclasses import replace
from typing import Any
from uuid import uuid4

import boto3
import PIL.Image as PIL_Image
import receipt_dynamo
from receipt_agent.lifecycle import receipt_manager
from receipt_dynamo.entities.receipt_merge import ReceiptMerge
from receipt_embeddings import report_incomplete
from receipt_upload import combine, utils
from receipt_upload.merchant_resolution import dynamo_embedding_write

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _collect_receipt_assets(receipt: Any) -> set[tuple[str, str]]:
    """Snapshot explicit raw/CDN references, never infer keys from a prefix."""
    assets = set()
    if getattr(receipt, "raw_s3_bucket", None) and receipt.raw_s3_key:
        assets.add((receipt.raw_s3_bucket, receipt.raw_s3_key))
    if getattr(receipt, "cdn_s3_bucket", None):
        for name in vars(receipt):
            if name.startswith("cdn_") and name.endswith("s3_key"):
                key = getattr(receipt, name)
                if key:
                    assets.add((receipt.cdn_s3_bucket, key))
    return assets


def _purge_receipt_children(
    client: Any, image_id: str, receipt_id: int
) -> int:
    """The data layer owns the consistent, bounded canonical/legacy sweep."""
    return client.purge_receipt_children(image_id, receipt_id)


def _delete_receipt_assets(
    s3_client: Any, assets: set[tuple[str, str]]
) -> None:
    """Attempt every captured object, then fail so the journal can resume."""
    failures = []
    for bucket, key in sorted(assets):
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(f"s3://{bucket}/{key}: {error}")
    if failures:
        raise RuntimeError(
            "Failed to delete receipt assets: " + "; ".join(failures)
        )


def _enqueue_recompute(image_id: str, receipt_id: int) -> None:
    """Require both sends; the updaters accept duplicate requests."""
    failures = []
    for env_name in ("SUMMARY_QUEUE_URL", "LINE_ITEM_QUEUE_URL"):
        try:
            boto3.client("sqs").send_message(
                QueueUrl=os.environ[env_name],
                MessageBody=json.dumps(
                    {
                        "entity_data": {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    }
                ),
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(f"{env_name}: {error}")
    if failures:
        raise RuntimeError(
            "Failed to enqueue recompute: " + "; ".join(failures)
        )


def _finish_merge(
    client: receipt_dynamo.DynamoClient,
    s3_client: Any,
    operation: ReceiptMerge,
) -> dict[str, Any]:
    """Resume cleanup once the complete output and manifest are durable."""
    image_id = operation.image_id
    client.assert_receipt_merge_owner(operation)
    client.assert_receipt_merge_output(operation)
    for rid in sorted(operation.source_ids, reverse=True):
        client.assert_receipt_merge_owner(operation)
        client.assert_receipt_merge_output(operation)
        if client.receipt_exists_consistent(image_id, rid):
            deletion = receipt_manager.delete_receipt(client, image_id, rid)
            if not deletion.success:
                raise RuntimeError(
                    f"Failed to delete receipt {rid}: {deletion.error}"
                )
        # This consistent sweep includes native embedding items, canonical
        # children and legacy keys, even after a previous parent delete.
        _purge_receipt_children(client, image_id, rid)
        owners = [
            client.get_image(image_id),
            *client.get_receipts_from_image_consistent(image_id),
        ]
        protected = set().union(
            *(_collect_receipt_assets(owner) for owner in owners)
        )
        assets: set[tuple[str, str]] = set()
        for bucket, key in operation.source_assets[str(rid)]:
            assets.add((bucket, key))
        _delete_receipt_assets(s3_client, assets - protected)

    client.assert_receipt_merge_owner(operation)
    _enqueue_recompute(image_id, operation.output_id)
    image_entity = client.get_image(image_id)
    image_entity.receipt_count = len(
        client.get_receipts_from_image_consistent(image_id)
    )
    client.update_receipt_merge_image(operation, image_entity)
    result = {
        **operation.result,
        "status": "success",
        "deleted_receipts": sorted(operation.source_ids, reverse=True),
    }
    client.checkpoint_receipt_merge(
        operation, replace(operation, status="COMPLETED", result=result)
    )
    return result


# Validation-heavy Lambda entrypoint; `context` is the AWS-provided arg.
# pylint: disable-next=too-many-return-statements,unused-argument
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
    8. Write native DynamoDB embeddings (abort before deletion if incomplete)
    9. Delete original receipts
    """
    operation: ReceiptMerge | None = None
    client = None
    try:
        # Validate input
        image_id = event.get("image_id")
        receipt_ids = event.get("receipt_ids")
        dry_run = event.get("dry_run", False)

        if not image_id:
            return {
                "status": "error",
                "error": "Missing required field: image_id",
            }
        if (
            not receipt_ids
            or not isinstance(receipt_ids, list)
            or len(receipt_ids) != 2
        ):
            return {
                "status": "error",
                "error": "receipt_ids must be a list of exactly 2 receipt IDs",
            }
        if not all(
            isinstance(rid, int) and not isinstance(rid, bool) and rid > 0
            for rid in receipt_ids
        ):
            return {
                "status": "error",
                "error": "receipt_ids must contain positive integers",
            }
        if receipt_ids[0] == receipt_ids[1]:
            return {"status": "error", "error": "receipt_ids must be distinct"}

        receipt_ids = sorted(receipt_ids)
        logger.info(
            "Merging receipts: image_id=%s, receipt_ids=%s, dry_run=%s",
            image_id,
            receipt_ids,
            dry_run,
        )

        # Initialize clients
        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        raw_bucket = os.environ.get("RAW_BUCKET")
        site_bucket = os.environ.get("SITE_BUCKET")

        if not table_name:
            return {"status": "error", "error": "DYNAMODB_TABLE_NAME not set"}
        if not raw_bucket:
            return {"status": "error", "error": "RAW_BUCKET not set"}
        if not site_bucket:
            return {"status": "error", "error": "SITE_BUCKET not set"}

        client = receipt_dynamo.DynamoClient(table_name=table_name)
        s3_client = boto3.client("s3")
        if not dry_run:
            operation = client.claim_receipt_merge(
                image_id, receipt_ids, str(uuid4())
            )
            if operation.status == "COMPLETED":
                return operation.result
            if operation.status == "READY":
                return _finish_merge(client, s3_client, operation)
        source_receipts = {
            receipt.receipt_id: receipt
            for receipt in client.get_receipts_from_image_consistent(image_id)
        }

        # ============================================================
        # Step 1: Read committed source details from the primary table
        # ============================================================
        logger.info("Reading committed receipt details...")
        receipt_details = {}
        for rid in receipt_ids:
            details = client.get_receipt_details(
                image_id, rid, consistent_read=True
            )
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
        new_receipt_id = (
            operation.output_id
            if operation
            else max(source_receipts, default=0) + 1
        )
        logger.info("New receipt ID: %d", new_receipt_id)

        # ============================================================
        # Step 2: Transform words to image space
        # ============================================================
        logger.info("Transforming words to image coordinates...")
        combined_words = combine.combine_receipt_words_to_image_coords(
            client,
            image_id,
            receipt_ids,
            image_width,
            image_height,
            strict=True,
            source_details=receipt_details,
        )
        logger.info("Combined %d words (deduplicated)", len(combined_words))

        if not combined_words:
            raise RuntimeError("No words found after combining receipts")

        # ============================================================
        # Step 3: Calculate new bounding rectangle
        # ============================================================
        logger.info("Calculating min-area bounding rectangle...")
        image_barcodes = combine.receipt_barcodes_in_image_space(
            client,
            image_id,
            receipt_ids,
            image_width,
            image_height,
            source_details=receipt_details,
        )
        # Barcodes may lie outside every text box. Include them in the crop
        # so migrating their coordinates also preserves the visible symbol.
        barcode_bounds = [
            {
                name: {
                    "x": getattr(barcode, name)["x"] * image_width,
                    "y": getattr(barcode, name)["y"] * image_height,
                }
                for name in (
                    "top_left",
                    "top_right",
                    "bottom_left",
                    "bottom_right",
                )
            }
            for barcode in image_barcodes
        ]
        rect_info = combine.calculate_min_area_rect(
            combined_words + barcode_bounds, image_width, image_height
        )
        bounds = rect_info["bounds"]
        src_corners = rect_info["src_corners"]
        warped_width = rect_info["warped_width"]
        warped_height = rect_info["warped_height"]
        logger.info("Warped dimensions: %dx%d", warped_width, warped_height)

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

        warped_image = combine.create_warped_receipt_image(
            original_image, src_corners, warped_width, warped_height
        )
        if warped_image is None:
            raise RuntimeError("Failed to create warped receipt image")
        logger.info(
            "Created warped image: %dx%d",
            warped_image.width,
            warped_image.height,
        )

        # ============================================================
        # Step 5: Create DynamoDB entities in warped space
        # ============================================================
        logger.info("Creating receipt records in warped space...")
        records = combine.create_combined_receipt_records(
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
        sections = combine.migrate_receipt_sections(
            client,
            image_id,
            receipt_ids,
            new_receipt_id,
            records["section_line_id_map"],
            source_details=receipt_details,
        )
        barcodes = combine.migrate_receipt_barcodes(
            image_barcodes,
            new_receipt_id,
            image_width,
            image_height,
            src_corners,
            warped_width,
            warped_height,
        )

        logger.info(
            "Created: 1 receipt, %d lines, %d words",
            len(receipt_lines),
            len(receipt_words),
        )

        # ============================================================
        # Step 6: Transform and create letters
        # ============================================================
        logger.info("Transforming letters to image coordinates...")
        combined_letters = combine.combine_receipt_letters_to_image_coords(
            client,
            image_id,
            receipt_ids,
            image_width,
            image_height,
            word_id_map,
            line_id_map,
            strict=True,
            source_details=receipt_details,
        )
        logger.info("Combined %d letters", len(combined_letters))

        receipt_letters = []
        if combined_letters:
            receipt_letters = combine.create_receipt_letters_from_combined(
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
        new_labels = combine.migrate_receipt_word_labels(
            client,
            image_id,
            receipt_ids,
            word_id_map,
            line_id_map,
            new_receipt_id,
            strict=True,
            source_details=receipt_details,
        )
        logger.info("Migrated %d labels", len(new_labels))

        # ============================================================
        # Step 8: Select best place (clone onto new receipt_id)
        # ============================================================
        logger.info("Selecting best receipt place...")
        source_place = combine.get_best_receipt_place(
            client,
            image_id,
            receipt_ids,
            strict=True,
            source_details=receipt_details,
        )
        receipt_place = None
        if source_place:
            # Clone so we never mutate the source entity or share mutable
            # containers; full field copy preserves geo/hours/confidence.
            receipt_place = combine.clone_receipt_place_for_receipt(
                source_place,
                new_receipt_id=new_receipt_id,
                reasoning_prefix=(
                    f"Merged from receipts {receipt_ids}. Original: "
                ),
            )
            logger.info("Best place: %s", receipt_place.merchant_name)
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
            "sections_migrated": len(sections),
            "barcodes_migrated": len(barcodes),
            "warped_dimensions": f"{warped_width}x{warped_height}",
            "place": receipt_place.merchant_name if receipt_place else None,
        }

        if dry_run:
            result["status"] = "dry_run"
            logger.info("Dry run complete: %s", result)
            return result

        # ============================================================
        # Step 9: Upload warped image to S3 and set CDN metadata
        # ============================================================
        assert operation is not None
        # Claim the actual parent before using its deterministic S3 paths.
        # An unrelated producer may have allocated the same ID since the
        # journal reservation; it must retain both its row and its objects.
        client.put_receipt_merge_output(operation, receipt)
        raw_s3_key = f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png"
        logger.info(
            "Uploading raw image to s3://%s/%s", raw_bucket, raw_s3_key
        )
        utils.upload_png_to_s3(warped_image, raw_bucket, raw_s3_key)
        receipt.raw_s3_key = raw_s3_key

        # Upload CDN variants and persist keys on the Receipt entity
        cdn_base_key = f"assets/{image_id}_RECEIPT_{new_receipt_id:05d}"
        logger.info(
            "Uploading CDN formats to s3://%s/%s.*", site_bucket, cdn_base_key
        )
        cdn_keys = utils.upload_all_cdn_formats(
            warped_image, site_bucket, cdn_base_key, generate_thumbnails=True
        )
        receipt.cdn_s3_key = cdn_keys.get("jpeg")
        receipt.cdn_webp_s3_key = cdn_keys.get("webp")
        receipt.cdn_avif_s3_key = cdn_keys.get("avif")
        receipt.cdn_thumbnail_s3_key = cdn_keys.get("jpeg_thumbnail")
        receipt.cdn_thumbnail_webp_s3_key = cdn_keys.get("webp_thumbnail")
        receipt.cdn_thumbnail_avif_s3_key = cdn_keys.get("avif_thumbnail")
        receipt.cdn_small_s3_key = cdn_keys.get("jpeg_small")
        receipt.cdn_small_webp_s3_key = cdn_keys.get("webp_small")
        receipt.cdn_small_avif_s3_key = cdn_keys.get("avif_small")
        receipt.cdn_medium_s3_key = cdn_keys.get("jpeg_medium")
        receipt.cdn_medium_webp_s3_key = cdn_keys.get("webp_medium")
        receipt.cdn_medium_avif_s3_key = cdn_keys.get("avif_medium")

        # Compute SHA256 for deduplication
        receipt.sha256 = utils.calculate_sha256_from_bytes(
            warped_image.tobytes()
        )

        # ============================================================
        # Step 10: Write to DynamoDB
        # ============================================================
        logger.info("Writing entities to DynamoDB...")
        client.put_receipt_merge_output(operation, receipt)
        # Rebuild incomplete staged children, including stale vectors, on the
        # same reserved output. Sources still exist until the READY checkpoint.
        client.purge_receipt_children(image_id, new_receipt_id)
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

        if sections:
            client.add_receipt_sections(sections)
        if barcodes:
            client.add_receipt_barcodes(barcodes)

        if receipt_place:
            # Idempotent: retries after partial write must not abort before
            # embeddings (step 11) and source deletion (step 13).
            place_action = combine.upsert_receipt_place(client, receipt_place)
            logger.info(
                "  %s place: %s",
                "Added" if place_action == "added" else "Updated",
                receipt_place.merchant_name,
            )

        # ============================================================
        # Step 11: Write native embeddings for the merged receipt
        # ============================================================
        logger.info("Writing native embeddings for merged receipt...")

        # Filter out noise words for embedding (non-noise words only)
        non_noise_words = [
            w for w in receipt_words if not getattr(w, "is_noise", False)
        ]
        if not non_noise_words:
            non_noise_words = receipt_words  # Fallback: use all words

        # The merged receipt's vectors are written
        # directly as native DynamoDB embedding items in one batched
        # OpenAI call. Destructive-step ordering (codex review P1):
        # source receipts are deleted below, so an incomplete native
        # write must abort the merge as retryable BEFORE deletion —
        # never report success with the merged receipt missing vectors.
        client.assert_receipt_merge_owner(operation)
        native_report = dynamo_embedding_write.write_native_embeddings(
            client,
            image_id=image_id,
            receipt_id=new_receipt_id,
            lines=receipt_lines,
            words=non_noise_words,
            word_labels=new_labels or [],
            receipt_place=receipt_place,
        )
        logger.info("Native embeddings report: %s", native_report)
        if report_incomplete(native_report):
            raise RuntimeError(
                "native embeddings write incomplete; "
                "source receipts NOT deleted "
                f"(safe to retry): {native_report}"
            )

        # Persist references while sources still exist. After READY a retry
        # never reloads source geometry or mints another output.
        manifest = {
            str(rid): [
                list(asset)
                for asset in sorted(
                    _collect_receipt_assets(receipt_details[rid].receipt)
                )
            ]
            for rid in receipt_ids
        }
        operation = client.checkpoint_receipt_merge(
            operation,
            replace(
                operation,
                status="READY",
                result=result,
                source_assets=manifest,
            ),
        )
        return _finish_merge(client, s3_client, operation)

    except Exception as e:  # pylint: disable=broad-exception-caught
        # CONTRACTUAL Lambda error contract: callers get a structured
        # error return, never an unhandled exception.
        logger.exception("Error merging receipts")
        if operation is not None and client is not None:
            try:
                client.release_receipt_merge(operation)
            except Exception:  # pylint: disable=broad-exception-caught
                # The original error is retained; a crashed release recovers
                # after the lease rather than permitting overlapping owners.
                logger.exception("Failed to release merge claim")
        return {
            "status": "error",
            "error": str(e),
            "image_id": event.get("image_id"),
            "receipt_ids": event.get("receipt_ids"),
        }
