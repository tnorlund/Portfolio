import json
import os

import boto3

from receipt_dynamo import DynamoClient

CURRENT_DIR = os.path.dirname(__file__)

IMAGE_ID = "2f05267d-86df-42b3-8a14-e29c5ea567b3"

if __name__ == "__main__":
    # 1) Grab data from Dynamo or wherever
    table_name = os.getenv("DYNAMO_DB_TABLE")
    if not table_name:
        raise ValueError("DYNAMO_DB_TABLE environment variable must be set")
    dynamo_client = DynamoClient(table_name)

    # For example, get an image and lines from your DB:
    details = dynamo_client.get_image_details(IMAGE_ID)

    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
        _receipt_metadatas,
        _ocr_jobs,
        _ocr_routing_decisions,
    ) = details
    # There should be only one image
    if len(images) != 1:
        raise Exception(f"Expected one image, got {len(images)}")
    image = images[0]

    s3 = boto3.client("s3")

    # Example: Get the GPT response from S3 (existing code)
    response = s3.list_objects_v2(
        Bucket=image.raw_s3_bucket,
        Prefix=f"raw/{image.image_id}",
    )
    gpt_objects = [obj for obj in response.get("Contents", []) if "GPT" in obj["Key"]]
    for obj in gpt_objects:
        obj_response = s3.get_object(
            Bucket=image.raw_s3_bucket,
            Key=obj["Key"],
        )

    # -------------------------------------------------------------------------
    # NEW CODE: Download the raw image file from image.raw_s3_bucket/image.raw_s3_key
    # Save it in the PNG directory (next to the JSON directory).
    png_dir = os.path.join(CURRENT_DIR, "integration", "PNG")
    os.makedirs(png_dir, exist_ok=True)
    image_filename = os.path.basename(image.raw_s3_key)
    image_local_path = os.path.join(png_dir, image_filename)
    print(
        f"Downloading raw image file from {image.raw_s3_bucket}/{image.raw_s3_key} to {image_local_path}"
    )
    s3.download_file(image.raw_s3_bucket, image.raw_s3_key, image_local_path)
    # -------------------------------------------------------------------------

    # Save the JSON file (unchanged location)
    json_dir = os.path.join(CURRENT_DIR, "integration", "JSON")
    os.makedirs(json_dir, exist_ok=True)
    # -------------------------------------------------------------------------
    # NEW CODE: Download the Swift OCR results JSON from S3.
    # Assume that the Swift OCR JSON is stored in the same S3 prefix as the
    # raw image.
    swift_ocr_key = image.raw_s3_key.replace(".png", ".json")
    swift_ocr_local_path = os.path.join(json_dir, f"{IMAGE_ID}_SWIFT_OCR.json")
    print(
        f"Downloading swift OCR JSON from {image.raw_s3_bucket}/{swift_ocr_key} to {swift_ocr_local_path}"
    )
    s3.download_file(image.raw_s3_bucket, swift_ocr_key, swift_ocr_local_path)

    # -------------------------------------------------------------------------
    # NEW CODE: Download each receipt’s raw file from its raw_s3_bucket/raw_s3_key.
    # Files will also be saved in the same PNG directory.
    for receipt in receipts:
        receipt_filename = os.path.basename(receipt.raw_s3_key)
        receipt_local_path = os.path.join(png_dir, receipt_filename)
        print(
            f"Downloading raw receipt file from {receipt.raw_s3_bucket}/{receipt.raw_s3_key} to {receipt_local_path}"
        )
        s3.download_file(receipt.raw_s3_bucket, receipt.raw_s3_key, receipt_local_path)
    # -------------------------------------------------------------------------

    with open(os.path.join(json_dir, f"{IMAGE_ID}_RESULTS.json"), "w") as f:
        json.dump(
            {
                "images": [
                    {
                        **dict(image),
                        "timestamp_added": "2021-01-01T00:00:00+00:00",
                        "raw_s3_bucket": "raw-image-bucket",
                        "cdn_s3_bucket": "cdn-bucket",
                    }
                ],
                "lines": [
                    {
                        key: value
                        for key, value in dict(line).items()
                        if key not in ("num_chars", "histogram")
                    }
                    for line in lines
                ],
                "words": [
                    {
                        key: value
                        for key, value in dict(word).items()
                        if key not in ("num_chars", "histogram")
                    }
                    for word in words
                ],
                "letters": [dict(letter) for letter in letters],
                "receipts": [
                    {
                        **dict(receipt),
                        "timestamp_added": "2021-01-01T00:00:00+00:00",
                        "raw_s3_bucket": "raw-image-bucket",
                        "cdn_s3_bucket": "cdn-bucket",
                    }
                    for receipt in receipts
                ],
                "receipt_lines": [
                    {
                        key: value
                        for key, value in dict(line).items()
                        if key not in ("num_chars", "histogram")
                    }
                    for line in receipt_lines
                ],
                "receipt_words": [
                    {
                        key: value
                        for key, value in dict(word).items()
                        if key not in ("num_chars", "histogram")
                    }
                    for word in receipt_words
                ],
                "receipt_letters": [dict(letter) for letter in receipt_letters],
                "receipt_word_labels": [dict(label) for label in receipt_word_labels],
            },
            f,
            indent=4,
        )
