import os
from typing import Literal
import boto3
import pytest
from dynamo import process

def upload_json_and_png_files_for_uuid(
    s3_client: "boto3.client", 
    bucket_name: str, 
    uuid: str, 
    raw_prefix: str = "raw_prefix"
) -> None:
    """
    Reads the .json and .png files for a given UUID from local directories 
    (integration/JSON, integration/PNG) and uploads them to the specified S3 bucket 
    under the given prefix.

    Args:
        s3_client (boto3.client): A boto3 S3 client.
        bucket_name (str): The name of the S3 bucket to upload into.
        uuid (str): The unique identifier for the files.
        raw_prefix (str, optional): Prefix path in the bucket for the files. Defaults to "raw_prefix".
    """

    # Get absolute paths to the JSON and PNG files in integration/JSON and integration/PNG
    base_dir = os.path.dirname(__file__)  # directory containing test__process.py
    json_path = os.path.join(base_dir, "JSON", f"{uuid}.json")
    png_path = os.path.join(base_dir, "PNG", f"{uuid}.png")

    # Upload .json
    with open(json_path, "r", encoding="utf-8") as json_file:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=f"{raw_prefix}/{uuid}.json",
            Body=json_file.read(),
        )

    # Upload .png
    with open(png_path, "rb") as png_file:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=f"{raw_prefix}/{uuid}.png",
            Body=png_file.read(),
        )

@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process(s3_bucket):
    # 1. Arrange: Put a ".json" and ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "e510f3c0-4e94-4bb9-a82e-e111f2d7e245"
    raw_prefix = "raw_prefix"
    upload_json_and_png_files_for_uuid(s3, s3_bucket, uuid, raw_prefix)
    process("table_name", "raw-image-bucket", raw_prefix, uuid, "cdn_bucket_name")

@pytest.mark.parametrize("s3_bucket", ["bad-bucket-name"], indirect=True)
def test_process_no_bucket(s3_bucket):
    with pytest.raises(ValueError, match="Bucket raw_bucket_name not found"):
        process("table_name", "raw_bucket_name", "raw_prefix", "uuid", "cdn_bucket_name")

@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_no_files(s3_bucket):
    with pytest.raises(ValueError, match="UUID uuid not found in raw bucket raw-image-bucket"):
        process("table_name", "raw-image-bucket", "raw_prefix", "uuid", "cdn_bucket_name")

@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_bad_json(s3_bucket):
    # 1. Arrange: Put a malformed JSON file and a ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=s3_bucket, 
        Key="raw_prefix/uuid.json", 
        Body="Not valid JSON content"
    )
    s3.put_object(
        Bucket=s3_bucket, 
        Key="raw_prefix/uuid.png", 
        Body=b"Fake PNG data"
    )

    # 2. Act & Assert: The invalid JSON should raise a ValueError
    with pytest.raises(ValueError, match="Error decoding OCR results: "):
        process("table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name")

@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_bad_png(s3_bucket):
    # 1. Arrange: Put a valid JSON file and a corrupted PNG in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=s3_bucket, 
        Key="raw_prefix/uuid.json", 
        Body='{"valid": "json"}'
    )
    s3.put_object(
        Bucket=s3_bucket, 
        Key="raw_prefix/uuid.png", 
        Body=b"Fake PNG data"
    )

    # 2. Act & Assert: The invalid PNG should raise a ValueError
    with pytest.raises(ValueError, match="Corrupted or invalid PNG file"):
        process("table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name")
