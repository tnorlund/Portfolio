"""Lambda handler for generating word similarity cache.

Searches ChromaDB lines collection for dairy milk products, fetches
receipt details with prices using parallel DynamoDB queries, and
generates a summary table for visualization with receipt images.
"""

import json
import logging
import os
import shutil
import statistics
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import boto3
import chromadb

from receipt_chroma.s3 import download_snapshot_atomic
from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
CHROMADB_BUCKET = os.environ["CHROMADB_BUCKET"]
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET", CHROMADB_BUCKET)
CACHE_KEY = "word-similarity-cache/milk.json"
TARGET_WORD = "MILK"

# Exclusion terms for dairy milk filtering
DAIRY_EXCLUDE_TERMS = ["CHOCOLATE", "CHOC", "COCONUT", "ALMOND"]

# Price ranges for inferring milk sizes
MILK_SIZE_RANGES = {
    "RAW WHOLE MILK": [
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "RAN WHOLE MILK": [  # OCR error for RAW
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "RAW MILK": [
        (0, 8.00, "Half Gallon"),
        (8.00, 25.00, "Gallon"),
    ],
    "ORG WHOLE MILK": [
        (0, 15.00, "Gallon"),
    ],
    "ORG FF GRASSFED MILK": [
        (0, 15.00, "Half Gallon"),
    ],
    "ORG FF GRASSED MILK": [
        (0, 15.00, "Half Gallon"),
    ],
    "WHOLE MILK": [
        (0, 6.00, "Half Gallon"),
        (6.00, 15.00, "Gallon"),
    ],
    "V CRNR WHOLE MILK": [
        (0, 10.00, "Gallon"),
    ],
    "VIT D WHOLE MILK": [
        (0, 10.00, "Gallon"),
    ],
}

# Initialize clients
s3_client = boto3.client("s3")


def infer_size(product: str, price: Optional[str]) -> str:
    """Infer product size based on product name and price."""
    if not price:
        return "Unknown"

    try:
        price_val = float(str(price).replace("$", "").replace(",", ""))
    except (ValueError, AttributeError):
        return "Unknown"

    product_upper = product.upper().strip()

    # Handle common variations
    if product_upper == "WHOLE MILK" or product == "Whole Milk":
        product_upper = "WHOLE MILK"

    ranges = MILK_SIZE_RANGES.get(product_upper)
    if not ranges:
        return "Unknown"

    for min_price, max_price, size in ranges:
        if min_price <= price_val < max_price:
            return size

    return "Unknown"


def assemble_visual_lines(words, labels):
    """Group words into visual lines by y-coordinate proximity."""
    if not words:
        return []

    # Build label lookup
    labels_by_word = defaultdict(list)
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    def get_valid_label(line_id, word_id):
        history = labels_by_word.get((line_id, word_id), [])
        valid = [lbl for lbl in history if lbl.validation_status == "VALID"]
        if valid:
            valid.sort(key=lambda lbl: str(lbl.timestamp_added), reverse=True)
            return valid[0]
        return None

    # Build word contexts with centroids
    word_contexts = []
    for word in words:
        centroid = word.calculate_centroid()
        label = get_valid_label(word.line_id, word.word_id)
        word_contexts.append({
            "word": word,
            "label": label,
            "y": centroid[1],
            "x": centroid[0],
        })

    # Sort by y descending
    sorted_contexts = sorted(word_contexts, key=lambda c: -c["y"])

    # Calculate tolerance
    heights = [
        w["word"].bounding_box.get("height", 0.02)
        for w in sorted_contexts
        if w["word"].bounding_box.get("height")
    ]
    if heights:
        y_tolerance = max(0.01, statistics.median(heights) * 0.75)
    else:
        y_tolerance = 0.015

    # Group by y-proximity
    visual_lines = []
    current_words = [sorted_contexts[0]]
    current_y = sorted_contexts[0]["y"]

    for ctx in sorted_contexts[1:]:
        if abs(ctx["y"] - current_y) <= y_tolerance:
            current_words.append(ctx)
            current_y = sum(c["y"] for c in current_words) / len(current_words)
        else:
            current_words.sort(key=lambda c: c["x"])
            visual_lines.append(current_words)
            current_words = [ctx]
            current_y = ctx["y"]

    current_words.sort(key=lambda c: c["x"])
    visual_lines.append(current_words)

    return visual_lines


def find_price_on_visual_line(target_line_id, words, labels):
    """Find LINE_TOTAL or UNIT_PRICE on the same visual line."""
    visual_lines = assemble_visual_lines(words, labels)

    # Find visual line containing target
    target_visual_line = None
    for vl in visual_lines:
        for ctx in vl:
            if ctx["word"].line_id == target_line_id:
                target_visual_line = vl
                break
        if target_visual_line:
            break

    if not target_visual_line:
        return None, None

    # Look for price labels
    line_total = None
    unit_price = None

    for ctx in target_visual_line:
        if ctx["label"]:
            if ctx["label"].label == "LINE_TOTAL":
                line_total = ctx["word"].text
            elif ctx["label"].label == "UNIT_PRICE":
                unit_price = ctx["word"].text

    return line_total, unit_price


def calculate_product_bbox(target_line_id, words, labels):
    """Calculate bounding box around product line for cropping.

    Returns bbox in normalized coordinates (0-1) with format:
    {tl: {x, y}, tr: {x, y}, bl: {x, y}, br: {x, y}}
    where y=1 is top and y=0 is bottom (receipt coordinate system).
    """
    visual_lines = assemble_visual_lines(words, labels)

    # Find visual line containing target
    target_visual_line = None
    target_visual_line_idx = None
    for idx, vl in enumerate(visual_lines):
        for ctx in vl:
            if ctx["word"].line_id == target_line_id:
                target_visual_line = vl
                target_visual_line_idx = idx
                break
        if target_visual_line:
            break

    if not target_visual_line:
        return None

    # Get lines to include (target + 1 above + 1 below for context)
    lines_to_include = []
    if target_visual_line_idx > 0:
        lines_to_include.extend(visual_lines[target_visual_line_idx - 1])
    lines_to_include.extend(target_visual_line)
    if target_visual_line_idx < len(visual_lines) - 1:
        lines_to_include.extend(visual_lines[target_visual_line_idx + 1])

    if not lines_to_include:
        return None

    # Calculate bounding box from all words
    min_x = min(ctx["word"].bounding_box.get("x", 0) for ctx in lines_to_include)
    max_x = max(
        ctx["word"].bounding_box.get("x", 0) + ctx["word"].bounding_box.get("width", 0)
        for ctx in lines_to_include
    )
    # Y coordinates: use top_left.y (top) and bottom_left.y (bottom)
    max_y = max(ctx["word"].top_left.get("y", 1) for ctx in lines_to_include)
    min_y = min(ctx["word"].bottom_left.get("y", 0) for ctx in lines_to_include)

    # Add padding (5% on x, variable on y)
    padding_x = (max_x - min_x) * 0.05
    padding_y = max((max_y - min_y) * 0.05, 0.02)

    left = max(0, min_x - padding_x)
    right = min(1, max_x + padding_x)
    bottom = max(0, min_y - padding_y)
    top = min(1, max_y + padding_y)

    return {
        "tl": {"x": left, "y": top},
        "tr": {"x": right, "y": top},
        "bl": {"x": left, "y": bottom},
        "br": {"x": right, "y": bottom},
    }


def receipt_to_dict(receipt):
    """Convert Receipt entity to dict for JSON serialization."""
    return {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "width": receipt.width,
        "height": receipt.height,
        "timestamp_added": str(receipt.timestamp_added),
        "raw_s3_bucket": receipt.raw_s3_bucket,
        "raw_s3_key": receipt.raw_s3_key,
        "top_left": receipt.top_left,
        "top_right": receipt.top_right,
        "bottom_left": receipt.bottom_left,
        "bottom_right": receipt.bottom_right,
        "sha256": receipt.sha256,
        "cdn_s3_bucket": getattr(receipt, "cdn_s3_bucket", None),
        "cdn_s3_key": getattr(receipt, "cdn_s3_key", None),
        "cdn_webp_s3_key": getattr(receipt, "cdn_webp_s3_key", None),
        "cdn_avif_s3_key": getattr(receipt, "cdn_avif_s3_key", None),
        "cdn_thumbnail_s3_key": getattr(receipt, "cdn_thumbnail_s3_key", None),
        "cdn_thumbnail_webp_s3_key": getattr(receipt, "cdn_thumbnail_webp_s3_key", None),
        "cdn_thumbnail_avif_s3_key": getattr(receipt, "cdn_thumbnail_avif_s3_key", None),
        "cdn_small_s3_key": getattr(receipt, "cdn_small_s3_key", None),
        "cdn_small_webp_s3_key": getattr(receipt, "cdn_small_webp_s3_key", None),
        "cdn_small_avif_s3_key": getattr(receipt, "cdn_small_avif_s3_key", None),
        "cdn_medium_s3_key": getattr(receipt, "cdn_medium_s3_key", None),
        "cdn_medium_webp_s3_key": getattr(receipt, "cdn_medium_webp_s3_key", None),
        "cdn_medium_avif_s3_key": getattr(receipt, "cdn_medium_avif_s3_key", None),
    }


def line_to_dict(line):
    """Convert ReceiptLine entity to dict for JSON serialization."""
    return {
        "image_id": line.image_id,
        "line_id": line.line_id,
        "text": line.text,
        "bounding_box": line.bounding_box,
        "top_left": line.top_left,
        "top_right": line.top_right,
        "bottom_left": line.bottom_left,
        "bottom_right": line.bottom_right,
        "angle_degrees": getattr(line, "angle_degrees", 0),
        "angle_radians": getattr(line, "angle_radians", 0),
        "confidence": getattr(line, "confidence", 1.0),
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate word similarity cache."""
    logger.info("Starting milk product cache generation")

    temp_dir = tempfile.mkdtemp()

    try:
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Step 1: Download ChromaDB lines snapshot
        logger.info("Downloading ChromaDB lines snapshot from %s", CHROMADB_BUCKET)
        download_result = download_snapshot_atomic(
            bucket=CHROMADB_BUCKET,
            collection="lines",
            local_path=temp_dir,
            verify_integrity=True,
        )

        if download_result.get("status") != "downloaded":
            logger.error("Failed to download snapshot: %s", download_result)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to download ChromaDB lines snapshot"}),
            }

        logger.info("Snapshot downloaded successfully")

        # Step 2: Search for MILK lines
        client = chromadb.PersistentClient(path=temp_dir)
        lines_collection = client.get_collection("lines")

        all_lines = lines_collection.get(include=["metadatas"])
        logger.info("Fetched %d lines from ChromaDB", len(all_lines["ids"]))

        # Filter for dairy milk
        matching_lines = []
        for id_, meta in zip(all_lines["ids"], all_lines["metadatas"]):
            text = meta.get("text", "")
            text_upper = text.upper()

            if TARGET_WORD in text_upper:
                is_excluded = any(term in text_upper for term in DAIRY_EXCLUDE_TERMS)
                if not is_excluded:
                    matching_lines.append({
                        "id": id_,
                        "text": text,
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "line_id": meta.get("line_id"),
                    })

        logger.info("Found %d dairy milk lines", len(matching_lines))

        # Step 3: Deduplicate by image_id
        by_image = defaultdict(list)
        for match in matching_lines:
            by_image[match["image_id"]].append(match)

        logger.info("Found %d unique receipts", len(by_image))

        # Step 4: Fetch receipt details in parallel
        work_items = []
        for image_id, matches in by_image.items():
            receipt_id = int(matches[0]["receipt_id"])
            line_id = int(matches[0]["line_id"])
            product_text = matches[0]["text"]
            work_items.append((image_id, receipt_id, line_id, product_text))

        def process_receipt(work_item):
            image_id, receipt_id, line_id, product_text = work_item
            try:
                details = dynamo_client.get_receipt_details(image_id, receipt_id)
                line_total, unit_price = find_price_on_visual_line(
                    line_id, details.words, details.labels
                )
                merchant = details.place.merchant_name if details.place else "Unknown"
                price = line_total or unit_price
                size = infer_size(product_text, price)

                # Calculate bounding box for visual cropping
                bbox = calculate_product_bbox(line_id, details.words, details.labels)

                # Convert lines to dict format
                lines_dict = [line_to_dict(line) for line in details.lines]

                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "product": product_text,
                    "merchant": merchant,
                    "price": price,
                    "size": size,
                    "line_id": line_id,
                    # Full receipt data for visual display
                    "receipt": receipt_to_dict(details.receipt),
                    "lines": lines_dict,
                    "bbox": bbox,
                }
            except Exception as e:
                logger.warning("Error processing %s: %s", image_id, e)
                return None

        results = []
        max_workers = 25

        logger.info("Fetching receipt details with %d parallel workers", max_workers)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_receipt, item): item for item in work_items}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        logger.info("Processed %d receipts successfully", len(results))

        # Step 5: Build summary table
        summary = defaultdict(lambda: {"count": 0, "prices": [], "receipts": []})
        for r in results:
            key = (r["merchant"], r["product"], r["size"])
            summary[key]["count"] += 1
            summary[key]["receipts"].append({
                "image_id": r["image_id"],
                "receipt_id": r["receipt_id"],
            })
            if r["price"]:
                try:
                    summary[key]["prices"].append(float(r["price"]))
                except ValueError:
                    pass

        # Convert to list format
        summary_table = []
        for (merchant, product, size), data in sorted(summary.items()):
            avg_price = None
            total = None
            if data["prices"]:
                avg_price = sum(data["prices"]) / len(data["prices"])
                total = sum(data["prices"])

            summary_table.append({
                "merchant": merchant,
                "product": product,
                "size": size,
                "count": data["count"],
                "avg_price": round(avg_price, 2) if avg_price else None,
                "total": round(total, 2) if total else None,
                "receipts": data["receipts"],
            })

        # Step 6: Build response
        response_data = {
            "query_word": TARGET_WORD,
            "total_receipts": len(results),
            "total_items": len(matching_lines),
            "summary_table": summary_table,
            "receipts": results,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

        # Step 7: Upload to S3
        logger.info("Uploading cache to S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY)
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=CACHE_KEY,
            Body=json.dumps(response_data, default=str),
            ContentType="application/json",
        )

        logger.info(
            "Cache generation complete: %d receipts, %d summary rows",
            len(results),
            len(summary_table),
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Cache generated successfully",
                "total_receipts": len(results),
                "summary_rows": len(summary_table),
            }),
        }

    except Exception as e:
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }

    finally:
        try:
            shutil.rmtree(temp_dir)
            logger.info("Cleaned up temp directory")
        except Exception as e:
            logger.warning("Failed to cleanup: %s", e)
