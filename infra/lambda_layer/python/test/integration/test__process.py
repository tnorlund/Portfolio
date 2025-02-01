import datetime
import os
import json
from typing import Literal
from botocore.exceptions import ClientError
import boto3
import pytest
from freezegun import freeze_time
from dynamo import process, DynamoClient, Image
from dynamo.data.process import process_ocr_dict


def upload_json_and_png_files_for_uuid(
    s3_client: "boto3.client",
    bucket_name: str,
    uuid: str,
    raw_prefix: str = "raw_prefix",
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


@pytest.mark.integration
@freeze_time("2021-01-01T00:00:00+00:00")
@pytest.mark.parametrize(
    "s3_buckets",
    [
        ("raw-bucket", "cdn-bucket"),  # You can specify any 2 bucket names here
    ],
    indirect=True,
)
def test_process(
    s3_buckets: Literal["raw-bucket", "cdn-bucket"],
    dynamodb_table: Literal["MyMockedTable"],
    # freeze_time,
):
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table
    # 1. Arrange: Put a ".json" and ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "e510f3c0-4e94-4bb9-a82e-e111f2d7e245"
    raw_prefix = "raw_prefix"
    upload_json_and_png_files_for_uuid(s3, raw_bucket, uuid, raw_prefix)

    # Act
    process(table_name, "raw-bucket", raw_prefix, uuid, "cdn-bucket")

    # Assert
    cdn_key = f"assets/{uuid}.png"
    cdn_response = s3.get_object(Bucket=cdn_bucket, Key=cdn_key)
    cdn_png_bytes = cdn_response["Body"].read()
    raw_response = s3.get_object(Bucket=raw_bucket, Key=f"{raw_prefix}/{uuid}.png")
    raw_png_bytes = raw_response["Body"].read()
    expected_lines, expected_words, expected_letters = process_ocr_dict(
        json.loads(
            s3.get_object(Bucket=raw_bucket, Key=f"{raw_prefix}/{uuid}.json")["Body"]
            .read()
            .decode("utf-8")
        ),
        uuid,
    )

    assert cdn_png_bytes == raw_png_bytes, "CDN copy of PNG does not match original!"
    assert cdn_response["ContentType"] == "image/png"
    assert Image(
        id=uuid,
        width=2480,
        height=3508,
        timestamp_added="2021-01-01T00:00:00+00:00",
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"{raw_prefix}/{uuid}.png",
        cdn_s3_bucket=cdn_bucket,
        cdn_s3_key=cdn_key,
        sha256="e0cf0ccf76e613858c445733a4bb3292342c22484f237b1b2213415a70b6b246",
    ) == DynamoClient(table_name).getImage(uuid)
    assert expected_lines == DynamoClient(table_name).listLines()
    assert expected_words == DynamoClient(table_name).listWords()
    assert dict(expected_letters[133]) == dict(DynamoClient(table_name).listLetters()[133])


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["bad-bucket-name"], indirect=True)
def test_process_no_bucket(s3_bucket):
    with pytest.raises(ValueError, match="Bucket raw_bucket_name not found"):
        process(
            "table_name", "raw_bucket_name", "raw_prefix", "uuid", "cdn_bucket_name"
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_no_files(s3_bucket):
    with pytest.raises(
        ValueError, match="UUID uuid not found in raw bucket raw-image-bucket"
    ):
        process(
            "table_name", "raw-image-bucket", "raw_prefix", "uuid", "cdn_bucket_name"
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_access_denied_raw_bucket(s3_bucket, monkeypatch):
    # 1. Arrange: upload objects in the raw bucket so we don't trigger 'NoSuchKey'
    s3_for_test = boto3.client("s3", region_name="us-east-1")
    s3_for_test.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.json", Body='{"valid": "json"}'
    )
    s3_for_test.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data"
    )

    # 2. Capture the real boto3.client (so we can still create real Moto-based clients)
    real_boto3_client = boto3.client

    # 3. Define a mock that returns an S3 client whose head_object is patched to raise AccessDenied
    def mock_boto3_client(service_name, *args, **kwargs):
        client = real_boto3_client(service_name, *args, **kwargs)
        if service_name == "s3":
            original_head_object = client.head_object

            def mock_head_object(*h_args, **h_kwargs):
                key = h_kwargs.get("Key", "")
                # Raise AccessDenied specifically for the ".png" key
                if key.endswith(".png"):
                    raise ClientError(
                        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
                        "HeadObject",
                    )
                return original_head_object(*h_args, **h_kwargs)

            # Patch the 'head_object' on this new S3 client
            monkeypatch.setattr(client, "head_object", mock_head_object)
        return client

    # 4. Patch the global "boto3.client" so that process(...) gets our mocked client
    monkeypatch.setattr("boto3.client", mock_boto3_client)

    # 5. Act & Assert: PNG file should trigger AccessDenied, causing a ValueError
    with pytest.raises(
        ValueError, match="Access denied to s3://raw-image-bucket/raw_prefix/*"
    ):
        process(
            "table_name",
            s3_bucket,
            "raw_prefix",
            "uuid",
            "cdn_bucket_name",
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_bad_json(s3_bucket):
    # 1. Arrange: Put a malformed JSON file and a ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.json", Body="Not valid JSON content"
    )
    s3.put_object(Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data")

    # 2. Act & Assert: The invalid JSON should raise a ValueError
    with pytest.raises(ValueError, match="Error decoding OCR results: "):
        process("table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name")


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_bad_png(s3_bucket):
    # 1. Arrange: Put a valid JSON file and a corrupted PNG in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.json", Body='{"valid": "json"}'
    )
    s3.put_object(Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data")

    # 2. Act & Assert: The invalid PNG should raise a ValueError
    with pytest.raises(ValueError, match="Corrupted or invalid PNG file"):
        process("table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name")


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_no_cdn_bucket(s3_bucket):
    # 1. Arrange: Put a ".json" and ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "e510f3c0-4e94-4bb9-a82e-e111f2d7e245"
    raw_prefix = "raw_prefix"
    upload_json_and_png_files_for_uuid(s3, s3_bucket, uuid, raw_prefix)

    with pytest.raises(ValueError, match="Bucket bad-cdn-bucket not found"):
        process("table_name", s3_bucket, raw_prefix, uuid, "bad-cdn-bucket")


@pytest.mark.integration
@pytest.mark.parametrize(
    "s3_buckets",
    [
        ("raw-bucket", "cdn-bucket"),  # You can specify any 2 bucket names here
    ],
    indirect=True,
)
def test_process_access_denied_cdn_bucket(s3_buckets, dynamodb_table, monkeypatch):
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table
    uuid = "e510f3c0-4e94-4bb9-a82e-e111f2d7e245"
    raw_prefix = "raw_prefix"
    s3 = boto3.client("s3", region_name="us-east-1")
    upload_json_and_png_files_for_uuid(s3, raw_bucket, uuid, raw_prefix)

    # 2. Capture the real boto3.client (so we can still create real Moto-based clients)
    real_boto3_client = boto3.client

    # 3. Define a mock that returns an S3 client whose put_object is patched to raise AccessDenied
    def mock_boto3_client(service_name, *args, **kwargs):
        client = real_boto3_client(service_name, *args, **kwargs)
        if service_name == "s3":
            original_put_object = client.put_object

            def mock_put_object(*p_args, **p_kwargs):
                key = p_kwargs.get("Key", "")
                # Raise AccessDenied specifically for the ".png" key
                if key.endswith(".png"):
                    raise ClientError(
                        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
                        "PutObject",
                    )
                return original_put_object(*p_args, **p_kwargs)

            # Patch the 'put_object' on this new S3 client
            monkeypatch.setattr(client, "put_object", mock_put_object)
        return client

    # 4. Patch the global "boto3.client" so that process(...) gets our mocked client
    monkeypatch.setattr("boto3.client", mock_boto3_client)

    # 5. Act & Assert: PNG file should trigger AccessDenied, causing a ValueError
    with pytest.raises(ValueError, match="Access denied to s3://cdn-bucket/assets/"):
        process(
            table_name,
            raw_bucket,
            raw_prefix,
            uuid,
            cdn_bucket,
        )
