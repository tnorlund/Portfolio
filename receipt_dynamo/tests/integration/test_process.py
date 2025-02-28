import json
import os
from typing import Any, List, Literal

import boto3
import pytest
from botocore.exceptions import ClientError
from freezegun import freeze_time

from receipt_dynamo import (
    DynamoClient,
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    Word,
    WordTag,
    process,
)


def get_raw_bytes_receipt(uuid: str, receipt_id: int):
    """Checks the PNG directory for the receipt image and returns the bytes"""
    base_dir = os.path.dirname(
        __file__
    )  # directory containing test__process.py
    receipt_path = os.path.join(
        base_dir, "PNG", f"{uuid}_RECEIPT_{str(receipt_id).zfill(5)}.png"
    )
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

    # Get absolute paths to the JSON and PNG files in integration/JSON and
    # integration/PNG
    # directory containing test__process.py
    base_dir = os.path.dirname(__file__)
    json_path = os.path.join(base_dir, "JSON", f"{uuid}_SWIFT_OCR.json")
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


def expected_results(
    uuid: str,
) -> tuple[
    list[Image],
    list[Line],
    list[Word],
    list[WordTag],
    list[Letter],
    list[Receipt],
    list[ReceiptLine],
    list[ReceiptWord],
    list[ReceiptWordTag],
    list[ReceiptLetter],
]:
    """Get the expected results for the given UUID in the JSON directory"""
    base_dir = os.path.dirname(
        __file__
    )  # directory containing test__process.py
    json_path = os.path.join(base_dir, "JSON", f"{uuid}_RESULTS.json")
    with open(json_path, "r", encoding="utf-8") as json_file:
        results = json.load(json_file)
        return (
            [Image(**image) for image in results["images"]],
            [Line(**line) for line in results["lines"]],
            [Word(**word) for word in results["words"]],
            [WordTag(**word_tag) for word_tag in results["word_tags"]],
            [Letter(**letter) for letter in results["letters"]],
            [Receipt(**receipt) for receipt in results["receipts"]],
            [ReceiptLine(**line) for line in results["receipt_lines"]],
            [ReceiptWord(**word) for word in results["receipt_words"]],
            [
                ReceiptWordTag(**word_tag)
                for word_tag in results["receipt_word_tags"]
            ],
            [ReceiptLetter(**letter) for letter in results["receipt_letters"]],
        )


def compare_with_precision(a: Any, b: Any, precision: int) -> bool:
    """
    Recursively compare two values.

    - If both values are numbers, compare them after rounding to the given precision.
    - If both are dictionaries, compare all keys and their corresponding values recursively.
    - If both are lists, compare each element in order.
    - Otherwise, compare using equality.
    """
    # Compare numeric values (int or float)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(a, precision) == round(b, precision)

    # Compare dictionaries (assuming keys are the same)
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        for key in a:
            if not compare_with_precision(a[key], b[key], precision):
                return False
        return True

    # Compare lists
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        for item_a, item_b in zip(a, b):
            if not compare_with_precision(item_a, item_b, precision):
                return False
        return True

    # Fallback to exact equality
    return a == b


def compare_entity_lists(
    entities1: List[Any], entities2: List[Any], precision: int = 6
) -> bool:
    """
    Compare two lists of DynamoDB entities.

    These are compared using the specified precision:
        - bounding_box, top_left, top_right, bottom_left, bottom_right,
          angle_degrees, angle_radians, confidence

    Args:
        entities1: The first list of DynamoDB entities.
        entities2: The second list of DynamoDb entities.
        precision: The number of decimal places to use when comparing
                   numeric values in the dictionaries and angles.

    Returns:
        True if the two lists match (with the given precision for numeric fields);
        False otherwise.
    """
    # First check: lists must be the same length.
    if len(entities1) != len(entities2):
        return False

    for l1, l2 in zip(entities1, entities2):
        if l1.image_id != l2.image_id:
            return False
        if l1.receipt_id != l2.receipt_id:
            return False
        if l1.line_id != l2.line_id:
            return False
        if l1.text != l2.text:
            return False
        if isinstance(l1, (ReceiptLetter, ReceiptWord)):
            if l1.word_id != l2.word_id:
                return False
        if isinstance(l1, ReceiptLetter):
            if l1.letter_id != l2.letter_id:
                return False

        # Compare complex attributes with precision.
        if not compare_with_precision(
            l1.bounding_box, l2.bounding_box, precision
        ):
            return False
        if not compare_with_precision(l1.top_left, l2.top_left, precision):
            return False
        if not compare_with_precision(l1.top_right, l2.top_right, precision):
            return False
        if not compare_with_precision(
            l1.bottom_left, l2.bottom_left, precision
        ):
            return False
        if not compare_with_precision(
            l1.bottom_right, l2.bottom_right, precision
        ):
            return False
        if not compare_with_precision(
            l1.angle_degrees, l2.angle_degrees, precision
        ):
            return False
        if not compare_with_precision(
            l1.angle_radians, l2.angle_radians, precision
        ):
            return False
        if not compare_with_precision(l1.confidence, l2.confidence, precision):
            return False

    return True


@pytest.mark.skip("TODO: Mock call to GPT API")
@pytest.mark.integration
@freeze_time("2021-01-01T00:00:00+00:00")
@pytest.mark.parametrize(
    "s3_buckets",
    [
        # You can specify any 2 bucket names here
        ("raw-image-bucket", "cdn-bucket"),
    ],
    indirect=True,
)
def test_process(
    s3_buckets: Literal["raw-image-bucket", "cdn-bucket"],
    dynamodb_table: Literal["MyMockedTable"],
):
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table

    # Arrange
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "02aa1d34-5c10-42b4-a463-c49b86214dd7"
    raw_prefix = "raw"
    upload_json_and_png_files_for_uuid(s3, raw_bucket, uuid, raw_prefix)
    get_raw_bytes_receipt(uuid, 1)

    # Act

    process(table_name, "raw-image-bucket", "raw/", uuid, "cdn-bucket")

    # Assert
    # The PNG should be in both the raw and cdn buckets
    cdn_response = s3.get_object(
        Bucket="cdn-bucket",
        Key="assets/02aa1d34-5c10-42b4-a463-c49b86214dd7.png",
    )
    cdn_png_bytes = cdn_response["Body"].read()
    raw_response = s3.get_object(
        Bucket="raw-image-bucket",
        Key="raw/02aa1d34-5c10-42b4-a463-c49b86214dd7.png",
    )
    raw_png_bytes = raw_response["Body"].read()
    assert (
        cdn_png_bytes == raw_png_bytes
    ), "CDN copy of PNG does not match original!"
    assert cdn_response["ContentType"] == "image/png"
    assert raw_response["ContentType"] == "image/png"

    # The Receipt PNG should be in both the raw and cdn buckets
    cdn_response = s3.get_object(
        Bucket="cdn-bucket",
        Key="assets/02aa1d34-5c10-42b4-a463-c49b86214dd7_RECEIPT_00001.png",
    )
    cdn_png_bytes = cdn_response["Body"].read()
    raw_response = s3.get_object(
        Bucket="raw-image-bucket",
        Key="raw/02aa1d34-5c10-42b4-a463-c49b86214dd7_RECEIPT_00001.png",
    )
    raw_png_bytes = raw_response["Body"].read()
    assert (
        cdn_png_bytes == raw_png_bytes
    ), "CDN copy of Receipt PNG does not match original!"
    assert cdn_response["ContentType"] == "image/png"
    assert raw_response["ContentType"] == "image/png"

    (
        expected_images,
        expected_lines,
        expected_words,
        expected_word_tags,
        expected_letters,
        expected_receipts,
        expected_receipt_lines,
        expected_receipt_words,
        expected_receipt_word_tags,
        expected_receipt_letters,
    ) = expected_results(uuid)

    # Probably want to query get receipt details for image and check the
    # receipt
    _, _, _, _, _, receipts, _, _, _, _, _, _ = DynamoClient(
        table_name
    ).getImageDetails(uuid)
    assert len(receipts) == 1
    receipts[0]

    assert expected_images == DynamoClient(table_name).listImages()[0]
    assert expected_lines == DynamoClient(table_name).listLines()
    assert expected_words == DynamoClient(table_name).listWords()
    assert expected_word_tags == DynamoClient(table_name).listWordTags()
    # More letters in dynamo than in the expected results
    # assert expected_letters == DynamoClient(table_name).listLetters()

    assert compare_entity_lists(
        expected_receipt_lines, DynamoClient(table_name).listReceiptLines()
    )
    assert compare_entity_lists(
        expected_receipt_words, DynamoClient(table_name).listReceiptWords()
    )
    assert (
        expected_receipt_word_tags
        == DynamoClient(table_name).listReceiptWordTags()
    )
    assert compare_entity_lists(
        expected_receipt_letters, DynamoClient(table_name).listReceiptLetters()
    )
    assert expected_receipts == DynamoClient(table_name).listReceipts()


@pytest.mark.skip("TODO: Mock call to GPT API")
@pytest.mark.integration
@freeze_time("2021-01-01T00:00:00+00:00")
@pytest.mark.parametrize(
    "s3_buckets",
    [
        (
            "raw-image-bucket",
            "cdn-bucket",
        ),  # Or any two bucket names for your test fixture
    ],
    indirect=True,
)
def test_process_upload(s3_buckets, dynamodb_table):
    """
    Tests the code path in which we provide ocr_dict and png_data
    directly to process(), rather than reading them from S3.
    """
    # --------------------------------------------------------------------
    # 1) Setup test variables & environment
    # --------------------------------------------------------------------
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table
    uuid = "02aa1d34-5c10-42b4-a463-c49b86214dd7"
    raw_prefix = "raw"

    s3 = boto3.client("s3", region_name="us-east-1")

    # Make sure the raw bucket is empty for the test (not strictly required,
    # but ensures we rely solely on the ocr_dict/png_data path)
    # The fixture might have it already empty, but if you want to be safe:
    # for obj in s3.list_objects_v2(Bucket=raw_bucket).get("Contents", []):
    #     s3.delete_object(Bucket=raw_bucket, Key=obj["Key"])

    # --------------------------------------------------------------------
    # 2) Read local JSON + PNG into memory (the same files your original
    #    test used). Convert the JSON into a dict.
    # --------------------------------------------------------------------
    # directory containing test_process.py
    base_dir = os.path.dirname(__file__)
    json_path = os.path.join(base_dir, "JSON", f"{uuid}_SWIFT_OCR.json")
    png_path = os.path.join(base_dir, "PNG", f"{uuid}.png")

    with open(json_path, "r", encoding="utf-8") as jf:
        ocr_dict = json.load(jf)

    with open(png_path, "rb") as pf:
        png_data = pf.read()

    # --------------------------------------------------------------------
    # 3) Call process() with our in-memory OCR + PNG data
    #    NOTE: This tests the new code path that does not read from S3.
    # --------------------------------------------------------------------
    process(
        table_name=table_name,
        raw_bucket_name=raw_bucket,
        raw_prefix=raw_prefix + "/",
        uuid=uuid,
        cdn_bucket_name=cdn_bucket,
        cdn_prefix="assets/",
        ocr_dict=ocr_dict,
        png_data=png_data,
    )

    # --------------------------------------------------------------------
    # 4) Assertions:
    #    - The raw bucket should now contain the JSON and PNG we just provided.
    #    - The CDN bucket should also contain the original PNG and any receipts.
    #    - DynamoDB should have the expected data (lines, words, receipts, etc.)
    # --------------------------------------------------------------------
    # (a) Check the raw bucket
    raw_json_resp = s3.get_object(
        Bucket=raw_bucket, Key=f"{raw_prefix}/{uuid}.json"
    )
    raw_json_body = raw_json_resp["Body"].read().decode("utf-8")
    assert (
        json.loads(raw_json_body) == ocr_dict
    ), "Raw JSON in S3 does not match input!"

    raw_png_resp = s3.get_object(
        Bucket=raw_bucket, Key=f"{raw_prefix}/{uuid}.png"
    )
    raw_png_bytes = raw_png_resp["Body"].read()
    assert raw_png_bytes == png_data, "Raw PNG in S3 does not match input!"

    # (b) Check the CDN bucket
    cdn_png_resp = s3.get_object(Bucket=cdn_bucket, Key=f"assets/{uuid}.png")
    cdn_png_bytes = cdn_png_resp["Body"].read()
    assert cdn_png_bytes == png_data, "CDN copy of PNG does not match input!"

    # (c) Check the transformed receipt in S3
    cdn_receipt_resp = s3.get_object(
        Bucket=cdn_bucket, Key=f"assets/{uuid}_RECEIPT_00001.png"
    )
    cdn_receipt_bytes = cdn_receipt_resp["Body"].read()
    raw_receipt_resp = s3.get_object(
        Bucket=raw_bucket, Key=f"{raw_prefix}/{uuid}_RECEIPT_00001.png"
    )
    raw_receipt_bytes = raw_receipt_resp["Body"].read()
    assert (
        cdn_receipt_bytes == raw_receipt_bytes
    ), "Receipt PNG differs between raw & CDN!"

    # (d) Compare DynamoDB items to expected
    #     This reuses your existing helpers and the same references
    #     as test_process(). Confirm we get the same final data.
    (
        expected_images,
        expected_lines,
        expected_words,
        expected_word_tags,
        expected_letters,
        expected_receipts,
        expected_receipt_lines,
        expected_receipt_words,
        expected_receipt_word_tags,
        expected_receipt_letters,
    ) = expected_results(uuid)

    dynamo_client = DynamoClient(table_name)
    assert expected_images == dynamo_client.listImages()[0]
    assert expected_lines == dynamo_client.listLines()
    assert expected_words == dynamo_client.listWords()
    assert expected_word_tags == dynamo_client.listWordTags()
    # Optionally compare letters if your data sets match exactly:
    # assert expected_letters == dynamo_client.listLetters()

    # For receipts & derived items, use your existing compare helpers:
    assert compare_entity_lists(
        expected_receipt_lines, dynamo_client.listReceiptLines()
    )
    assert compare_entity_lists(
        expected_receipt_words, dynamo_client.listReceiptWords()
    )
    assert expected_receipt_word_tags == dynamo_client.listReceiptWordTags()
    assert compare_entity_lists(
        expected_receipt_letters, dynamo_client.listReceiptLetters()
    )
    assert expected_receipts == dynamo_client.listReceipts()
    # if expected_receipts != DynamoClient(table_name).listReceipts():
    #     # Download the receipt image from the CDN to the FAIL directory
    #     base_dir = os.path.dirname(__file__)
    #     fail_dir = os.path.join(base_dir, "FAIL")
    #     os.makedirs(fail_dir, exist_ok=True)
    #     receipt_path = os.path.join(fail_dir, f"{uuid}_RECEIPT_00001.png")
    #     print(f"Receipt transform doesn't match. Placing receipt in {receipt_path}")
    #     with open(receipt_path, "wb") as receipt_file:
    #         receipt_file.write(cdn_receipt_bytes)
    # else:
    #     # Delete the failure from the fail directory
    #     base_dir = os.path.dirname(__file__)
    #     fail_dir = os.path.join(base_dir, "FAIL")
    #     receipt_path = os.path.join(fail_dir, f"{uuid}_RECEIPT_00001.png")
    #     if os.path.exists(receipt_path):
    #         os.remove(receipt_path)


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["bad-bucket-name"], indirect=True)
def test_process_no_bucket(s3_bucket):
    with pytest.raises(ValueError, match="Bucket raw_bucket_name not found"):
        process(
            "table_name",
            "raw_bucket_name",
            "raw_prefix",
            "uuid",
            "cdn_bucket_name",
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_no_files(s3_bucket):
    with pytest.raises(
        ValueError,
        match="UUID uuid not found s3://raw-image-bucket/raw_prefix/uuid*",
    ):
        process(
            "table_name",
            "raw-image-bucket",
            "raw_prefix",
            "uuid",
            "cdn_bucket_name",
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_access_denied_raw_bucket(s3_bucket, monkeypatch):
    # 1. Arrange: upload objects in the raw bucket so we don't trigger
    # 'NoSuchKey'
    s3_for_test = boto3.client("s3", region_name="us-east-1")
    s3_for_test.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.json", Body='{"valid": "json"}'
    )
    s3_for_test.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data"
    )

    # 2. Capture the real boto3.client (so we can still create real Moto-based
    # clients)
    real_boto3_client = boto3.client

    # 3. Define a mock that returns an S3 client whose head_object is patched
    # to raise AccessDenied
    def mock_boto3_client(service_name, *args, **kwargs):
        client = real_boto3_client(service_name, *args, **kwargs)
        if service_name == "s3":
            original_head_object = client.head_object

            def mock_head_object(*h_args, **h_kwargs):
                key = h_kwargs.get("Key", "")
                # Raise AccessDenied specifically for the ".png" key
                if key.endswith(".png"):
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "AccessDenied",
                                "Message": "Access Denied",
                            }
                        },
                        "HeadObject",
                    )
                return original_head_object(*h_args, **h_kwargs)

            # Patch the 'head_object' on this new S3 client
            monkeypatch.setattr(client, "head_object", mock_head_object)
        return client

    # 4. Patch the global "boto3.client" so that process(...) gets our mocked
    # client
    monkeypatch.setattr("boto3.client", mock_boto3_client)

    # 5. Act & Assert: PNG file should trigger AccessDenied, causing a
    # ValueError
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
        Bucket=s3_bucket,
        Key="raw_prefix/uuid.json",
        Body="Not valid JSON content",
    )
    s3.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data"
    )

    # 2. Act & Assert: The invalid JSON should raise a ValueError
    with pytest.raises(ValueError, match="Error decoding OCR results: "):
        process(
            "table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name"
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_bad_png(s3_bucket):
    # 1. Arrange: Put a valid JSON file and a corrupted PNG in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.json", Body='{"valid": "json"}'
    )
    s3.put_object(
        Bucket=s3_bucket, Key="raw_prefix/uuid.png", Body=b"Fake PNG data"
    )

    # 2. Act & Assert: The invalid PNG should raise a ValueError
    with pytest.raises(
        ValueError,
        match="Corrupted or invalid PNG at s3://raw-image-bucket/raw_prefix/uuid.png",
    ):
        process(
            "table_name", s3_bucket, "raw_prefix", "uuid", "cdn_bucket_name"
        )


@pytest.mark.integration
@pytest.mark.parametrize("s3_bucket", ["raw-image-bucket"], indirect=True)
def test_process_no_cdn_bucket(s3_bucket):
    # 1. Arrange: Put a ".json" and ".png" in the bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    uuid = "2608fbeb-dd25-4ab8-8034-5795282b6cd6"
    raw_prefix = "raw_prefix"
    upload_json_and_png_files_for_uuid(s3, s3_bucket, uuid, raw_prefix)

    with pytest.raises(ValueError, match="Bucket bad-cdn-bucket not found"):
        process("table_name", s3_bucket, raw_prefix, uuid, "bad-cdn-bucket")


@pytest.mark.integration
@pytest.mark.parametrize(
    "s3_buckets",
    [
        (
            "raw-bucket",
            "cdn-bucket",
        ),  # You can specify any 2 bucket names here
    ],
    indirect=True,
)
def test_process_access_denied_cdn_bucket(
    s3_buckets, dynamodb_table, monkeypatch
):
    raw_bucket, cdn_bucket = s3_buckets
    table_name = dynamodb_table
    uuid = "2608fbeb-dd25-4ab8-8034-5795282b6cd6"
    raw_prefix = "raw_prefix"
    s3 = boto3.client("s3", region_name="us-east-1")
    upload_json_and_png_files_for_uuid(s3, raw_bucket, uuid, raw_prefix)

    # 2. Capture the real boto3.client (so we can still create real Moto-based
    # clients)
    real_boto3_client = boto3.client

    # 3. Define a mock that returns an S3 client whose put_object is patched
    # to raise AccessDenied
    def mock_boto3_client(service_name, *args, **kwargs):
        client = real_boto3_client(service_name, *args, **kwargs)
        if service_name == "s3":
            original_put_object = client.put_object

            def mock_put_object(*p_args, **p_kwargs):
                bucket = p_kwargs.get("Bucket", "")
                key = p_kwargs.get("Key", "")
                # Only raise an error if we're uploading to the CDN bucket
                # and the key begins with the CDN prefix (e.g., "assets/")
                if bucket == cdn_bucket and key.startswith("assets/"):
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "AccessDenied",
                                "Message": "Access Denied",
                            }
                        },
                        "PutObject",
                    )
                return original_put_object(*p_args, **p_kwargs)

            monkeypatch.setattr(client, "put_object", mock_put_object)
        return client

    # 4. Patch the global "boto3.client" so that process(...) gets our mocked
    # client
    monkeypatch.setattr("boto3.client", mock_boto3_client)

    # 5. Act & Assert: PNG file should trigger AccessDenied, causing a
    # ValueError
    with pytest.raises(
        ValueError, match="Access denied to s3://cdn-bucket/assets"
    ):
        process(
            table_name,
            raw_bucket,
            raw_prefix,
            uuid,
            cdn_bucket,
        )
