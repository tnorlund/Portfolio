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

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def get_image_ids_from_llm_analysis(bucket: str, s3_key: str) -> List[Dict[str, Any]]:
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
                    logger.info(f"Skipping {image_id}: LLM said NONE")
                    continue

                # Check if there's a recommended combination
                has_recommendation = False
                if option_line:
                    option_part = option_line.split(":", 1)[1].strip() if ":" in option_line else option_line
                    try:
                        option_num = int(option_part.split()[0])
                        if option_num > 0:
                            has_recommendation = True
                    except (ValueError, IndexError):
                        pass

                if not has_recommendation:
                    # Also check for patterns in the full response
                    for i in range(1, 10):
                        if f"OPTION {i}" in response_upper or f"OPTION: {i}" in response_upper:
                            has_recommendation = True
                            break

                if has_recommendation:
                    receipt_ids = analysis.get("receipt_ids", [])
                    # Determine which receipts to combine based on option
                    if len(receipt_ids) >= 2:
                        # Generate combinations (same logic as in dev.list_receipts_without_merchants.py)
                        combinations = []
                        if len(receipt_ids) == 2:
                            combinations = [(receipt_ids[0], receipt_ids[1])]
                        elif len(receipt_ids) == 3:
                            combinations = [
                                (receipt_ids[0], receipt_ids[1]),  # Option 1: A+B
                                (receipt_ids[0], receipt_ids[2]),  # Option 2: A+C
                                (receipt_ids[1], receipt_ids[2]),  # Option 3: B+C
                            ]

                        # Extract option number from response
                        option_num = None
                        if option_line:
                            try:
                                option_part = option_line.split(":", 1)[1].strip()
                                option_num = int(option_part.split()[0])
                            except (ValueError, IndexError):
                                pass

                        if option_num and 1 <= option_num <= len(combinations):
                            combo = combinations[option_num - 1]
                            images.append({
                                "image_id": image_id,
                                "receipt_ids": list(combo),
                            })
                            logger.info(f"Including {image_id}: Combine receipts {combo}")

    except Exception as e:
        logger.error(f"Error loading LLM analysis from S3: {e}")
        raise

    return images


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    List images that need receipt combinations.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "llm_analysis_s3_key": "s3://bucket/path/to/analysis.json",  // Optional
        "limit": 10  // Optional limit
    }

    Output:
    {
        "images": [
            {
                "image_id": "image-uuid",
                "receipt_ids": [1, 2]
            },
            ...
        ],
        "total_images": 5
    }
    """
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    llm_analysis_s3_key = event.get("llm_analysis_s3_key")
    limit = event.get("limit")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    logger.info(f"Listing images for combination, execution_id={execution_id}")

    images = []

    if llm_analysis_s3_key:
        # Load from LLM analysis file in S3
        logger.info(f"Loading from LLM analysis: {llm_analysis_s3_key}")
        # Parse S3 key (remove s3://bucket/ prefix if present)
        if llm_analysis_s3_key.startswith("s3://"):
            parts = llm_analysis_s3_key.replace("s3://", "").split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            bucket = batch_bucket
            key = llm_analysis_s3_key

        images = get_image_ids_from_llm_analysis(bucket, key)
    else:
        # Query DynamoDB for receipts without merchant metadata
        # This is a fallback - ideally use LLM analysis
        logger.info("Querying DynamoDB for receipts without merchant metadata")
        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME required when not using LLM analysis")

        dynamo = DynamoClient(table_name)

        # Get all receipt metadatas and find ones without merchants
        last_key = None
        image_receipt_map = {}  # image_id -> set of receipt_ids

        while True:
            receipts, last_key = dynamo.list_receipt_metadatas(
                limit=100,
                last_evaluated_key=last_key
            )

            for receipt in receipts:
                if not receipt.merchant_name or not receipt.merchant_name.strip():
                    if receipt.image_id not in image_receipt_map:
                        image_receipt_map[receipt.image_id] = set()
                    image_receipt_map[receipt.image_id].add(receipt.receipt_id)

            if not last_key:
                break

        # Only include images with multiple receipts
        for image_id, receipt_ids_set in image_receipt_map.items():
            if len(receipt_ids_set) >= 2:
                # For now, combine all receipts (LLM analysis would be better)
                images.append({
                    "image_id": image_id,
                    "receipt_ids": sorted(list(receipt_ids_set)),
                })

    # Apply limit if specified
    if limit and len(images) > limit:
        images = images[:limit]

    logger.info(f"Found {len(images)} images to process")

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

