import datetime
import os
import json
from typing import Literal
from botocore.exceptions import ClientError
import boto3
import pytest
from freezegun import freeze_time
from dynamo import process, DynamoClient, Image, Receipt
from dynamo.data.process import process_ocr_dict


def get_raw_bytes_receipt(uuid: str, receipt_id: int):
    """Checks the PNG directory for the receipt image and returns the bytes"""
    base_dir = os.path.dirname(__file__)  # directory containing test__process.py
    receipt_path = os.path.join(base_dir, "PNG", f"{uuid}_RECEIPT_{str(receipt_id).zfill(5)}.png")
    with open(receipt_path, "rb") as receipt_file:
        return receipt_file.read()

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

    This is necessary for the start of the test because the upload script lands 
    both these files in the raw bucket.

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
            ContentType="image/png",

        )

    # Upload .png
    with open(png_path, "rb") as png_file:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=f"{raw_prefix}/{uuid}.png",
            Body=png_file.read(),

            ContentType="image/png",

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
):
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table

    # Arrange
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "27e58027-edd6-45af-9038-3a778feda954"
    raw_prefix = "raw_prefix"
    upload_json_and_png_files_for_uuid(s3, raw_bucket, uuid, raw_prefix)
    receipt_raw_bytes = get_raw_bytes_receipt(uuid, 1)

    # Act

    process(table_name, "raw-bucket", "raw_prefix/", uuid, "cdn-bucket")

    # Assert
    # The PNG should be in both the raw and cdn buckets
    cdn_response = s3.get_object(Bucket="cdn-bucket", Key="assets/27e58027-edd6-45af-9038-3a778feda954.png")
    cdn_png_bytes = cdn_response["Body"].read()
    raw_response = s3.get_object(Bucket="raw-bucket", Key="raw_prefix/27e58027-edd6-45af-9038-3a778feda954.png")
    raw_png_bytes = raw_response["Body"].read()
    assert cdn_png_bytes == raw_png_bytes, "CDN copy of PNG does not match original!"
    assert cdn_response["ContentType"] == "image/png"
    assert raw_response["ContentType"] == "image/png"

    # The Receipt PNG should be in both the raw and cdn buckets
    cdn_response = s3.get_object(Bucket="cdn-bucket", Key="assets/27e58027-edd6-45af-9038-3a778feda954_RECEIPT_00001.png")
    cdn_png_bytes = cdn_response["Body"].read()
    raw_response = s3.get_object(Bucket="raw-bucket", Key="raw_prefix/27e58027-edd6-45af-9038-3a778feda954_RECEIPT_00001.png")
    raw_png_bytes = raw_response["Body"].read()
    assert cdn_png_bytes == raw_png_bytes, "CDN copy of Receipt PNG does not match original!"
    assert cdn_response["ContentType"] == "image/png"
    assert raw_response["ContentType"] == "image/png"

    expected_lines, expected_words, expected_letters = process_ocr_dict(
        json.loads(
            s3.get_object(Bucket=raw_bucket, Key="raw_prefix/27e58027-edd6-45af-9038-3a778feda954.json")["Body"]

            .read()
            .decode("utf-8")
        ),
        uuid,
    )

    # Probably want to query get receipt details for image and check the receipt
    _, _, _, _, _, receipts = DynamoClient(table_name).getImageDetails(uuid)
    assert len(receipts) == 1
    receipt = receipts[0]["receipt"]


    assert Image(
        id=uuid,
        width=2480,
        height=3508,
        timestamp_added="2021-01-01T00:00:00+00:00",

        raw_s3_bucket="raw-bucket",
        raw_s3_key="raw_prefix/27e58027-edd6-45af-9038-3a778feda954.png",
        cdn_s3_bucket="cdn-bucket",
        cdn_s3_key="assets/27e58027-edd6-45af-9038-3a778feda954.png",

        sha256="84418357a7248b72d9ed566ea52871f6cb14338144884be50790402f1ecb7984",
    ) == DynamoClient(table_name).getImage(uuid)
    assert expected_lines == DynamoClient(table_name).listLines()
    assert expected_words == DynamoClient(table_name).listWords()
    assert expected_letters == DynamoClient(table_name).listLetters()
    assert (
        Receipt(
            id=1,
            image_id=uuid,
            width=883,
            height=2162,
            timestamp_added="2021-01-01T00:00:00+00:00",
            raw_s3_bucket="raw-bucket",
            raw_s3_key="raw_prefix/27e58027-edd6-45af-9038-3a778feda954_RECEIPT_00001.png",
            top_left={"x": 0.3385116284949973, "y": 0.8528895959899104},
            top_right={"x": 0.6916728645345689, "y": 0.8201395064710936},
            bottom_left={"x": 0.2251505841755011, "y": 0.24193551921666712},
            bottom_right={"x": 0.5783118202150728, "y": 0.20918542969785037},
            cdn_s3_bucket="cdn-bucket",
            cdn_s3_key="assets/27e58027-edd6-45af-9038-3a778feda954_RECEIPT_00001.png",
            sha256="3e98bc86eff21a766b2cf722bc9ed2b8363b17f7531c9001df980ff2f9ca083e",
        )
        == receipt
    )



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
        ValueError, match="UUID uuid not found s3://raw-image-bucket/raw_prefix/uuid*"

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
    uuid = "27e58027-edd6-45af-9038-3a778feda954"
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
    uuid = "27e58027-edd6-45af-9038-3a778feda954"
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
    with pytest.raises(ValueError, match="Access denied to s3://cdn-bucket/assets"):

        process(
            table_name,
            raw_bucket,
            raw_prefix,
            uuid,
            cdn_bucket,
        )
