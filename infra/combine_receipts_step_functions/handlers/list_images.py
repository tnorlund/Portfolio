"""
List Images Handler (Zip Lambda)

Lists images that have multiple receipts and need to be combined.
Can load from LLM analysis JSON file in S3 or query DynamoDB directly.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from receipt_agent.utils.combination_generator import (
    generate_receipt_combinations,
)

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def get_image_ids_from_llm_analysis(
    bucket: str, s3_key: str
) -> List[Dict[str, Any]]:
    """Extract image IDs from LLM analysis JSON file in S3."""
    images = []

    try:
        # Download from S3
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        data = json.loads(response["Body"].read())

        # Check for llm_analyses array
        if isinstance(data, dict) and "llm_analyses" in data:
            for analysis in data["llm_analyses"]:
                image_id = analysis.get("image_id")
                if not image_id:
                    continue

                # Check if LLM recommended combining (not "NONE")
                llm_response = analysis.get("llm_response", {})
                response_text = llm_response.get("response", "")

                if not response_text:
                    continue

                # Parse the response - look for "OPTION:" line
                response_upper = response_text.upper()
                option_line = None
                for line in response_text.split("\n"):
                    if line.strip().upper().startswith("OPTION:"):
                        option_line = line.strip()
                        break

                # Skip if LLM said "NONE"
                if option_line and "NONE" in option_line.upper():
                    logger.info("Skipping %s: LLM said NONE", image_id)
                    continue

                # Check if there's a recommended combination
                has_recommendation = False
                if option_line:
                    option_part = (
                        option_line.split(":", 1)[1].strip()
                        if ":" in option_line
                        else option_line
                    )
                    try:
                        option_num = int(option_part.split()[0])
                        if option_num > 0:
                            has_recommendation = True
                    except (ValueError, IndexError):
                        pass

                if not has_recommendation:
                    # Also check for patterns in the full response
                    for i in range(1, 10):
                        if (
                            f"OPTION {i}" in response_upper
                            or f"OPTION: {i}" in response_upper
                        ):
                            has_recommendation = True
                            break

                if has_recommendation:
                    receipt_ids = analysis.get("receipt_ids", [])
                    # Determine which receipts to combine based on option
                    if len(receipt_ids) >= 2:
                        # Generate combinations using shared utility
                        combinations = generate_receipt_combinations(
                            receipt_ids
                        )

                        # Extract option number from response
                        option_num = None
                        if option_line:
                            try:
                                option_part = option_line.split(":", 1)[
                                    1
                                ].strip()
                                option_num = int(option_part.split()[0])
                            except (ValueError, IndexError):
                                pass

                        if option_num and 1 <= option_num <= len(combinations):
                            combo = combinations[option_num - 1]
                            images.append(
                                {
                                    "image_id": image_id,
                                    "receipt_ids": list(combo),
                                }
                            )
                            logger.info(
                                f"Including {image_id}: Combine receipts {combo}"
                            )

    except Exception as e:
        logger.exception("Error loading LLM analysis from S3")
        raise

    return images


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    List images/receipts that need combination because they lack merchant place info.

    Logic:
    - Fetch all receipts (IMAGE#/RECEIPT# items).
    - Fetch all receipt place items.
    - Identify:
        * Receipts whose place exists but merchant_name is missing/blank.
        * Receipts that have no place at all.
    - Group by image_id and emit only images with 2+ target receipts.

    All other behaviors (LLM selection, combination) happen downstream.
    """
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    limit = event.get("limit")
    llm_analysis_s3_key = event.get("llm_analysis_s3_key")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    logger.info(
        "Listing images for combination, execution_id=%s", execution_id
    )

    images = []

    # If LLM analysis S3 key is provided, use pre-computed analysis
    if llm_analysis_s3_key:
        logger.info(
            "Loading images from LLM analysis: s3://%s/%s",
            batch_bucket,
            llm_analysis_s3_key,
        )
        images = get_image_ids_from_llm_analysis(
            batch_bucket, llm_analysis_s3_key
        )
    else:
        logger.info("Listing receipts and places to find missing merchants")
        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME required")

        dynamo = DynamoClient(table_name)

        # Collect all receipts
        receipts_by_image: Dict[str, set[int]] = {}
        last_key = None
        while True:
            recs, last_key = dynamo.list_receipts(
                limit=100, last_evaluated_key=last_key
            )
            for r in recs:
                receipts_by_image.setdefault(r.image_id, set()).add(
                    r.receipt_id
                )
            if not last_key:
                break

        # Collect place info and note which have missing merchant_name
        place_missing_merchant: Dict[str, set[int]] = {}
        place_seen: Dict[str, set[int]] = {}
        last_key = None
        while True:
            places, last_key = dynamo.list_receipt_places(
                limit=100, last_evaluated_key=last_key
            )
            for p in places:
                place_seen.setdefault(p.image_id, set()).add(p.receipt_id)
                if not p.merchant_name or not str(p.merchant_name).strip():
                    place_missing_merchant.setdefault(p.image_id, set()).add(
                        p.receipt_id
                    )
            if not last_key:
                break

        # Receipts with no place info
        no_place: Dict[str, set[int]] = {}
        for img, rids in receipts_by_image.items():
            missing = rids - place_seen.get(img, set())
            if missing:
                no_place.setdefault(img, set()).update(missing)

        # Union of "no place" and "place missing merchant"
        target_receipts_by_image: Dict[str, set[int]] = {}
        for img, rids in no_place.items():
            target_receipts_by_image.setdefault(img, set()).update(rids)
        for img, rids in place_missing_merchant.items():
            target_receipts_by_image.setdefault(img, set()).update(rids)

        # For any image with at least one target receipt, include ALL receipts on that image
        # (so the LLM can choose the best combination among all).
        for image_id, _target_rids in target_receipts_by_image.items():
            all_rids = receipts_by_image.get(image_id, set())
            if len(all_rids) >= 2:
                images.append(
                    {
                        "image_id": image_id,
                        "receipt_ids": sorted(list(all_rids)),
                    }
                )

    # Apply limit if specified
    if limit is not None and len(images) > limit:
        images = images[:limit]

    logger.info("Found %d images to process", len(images))

    # Save to S3 for reference
    manifest_key = f"manifests/{execution_id}/images.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=manifest_key,
        Body=json.dumps({"images": images}, indent=2),
        ContentType="application/json",
    )

    return {
        "images": images,
        "total_images": len(images),
        "manifest_s3_key": manifest_key,
    }
