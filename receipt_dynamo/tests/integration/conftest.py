from datetime import datetime
from json import load
from os.path import dirname, join

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo import (
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
)


@pytest.fixture
def dynamodb_table():
    """
    Spins up a mock DynamoDB instance, creates a table (with GSIs: GSI1, GSI2,
    and GSITYPE), waits until both the table and the GSIs are active, then
    yields the table name for tests.

    After the tests, everything is torn down automatically.
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        table_name = "MyMockedTable"
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )

        # Wait for the table to be created
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)

        # Yield the table name so your tests can reference it
        yield table_name


@pytest.fixture
def s3_bucket(request):
    """
    Spins up a mock S3 instance, creates a bucket with a name from
    `request.param`, then yields the bucket name for tests.

    After the tests, everything is torn down automatically.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")

        # Pull the bucket name to create from the test's parameter
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)

        yield bucket_name


@pytest.fixture
def s3_buckets(request):
    """
    Spins up a mock S3 instance, creates two buckets (taken from
    `request.param`), then yields a tuple of the two bucket names for tests.

    After the tests, everything is torn down automatically.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")

        # Expect request.param to be a tuple of two bucket names
        bucket_name1, bucket_name2 = request.param

        s3.create_bucket(Bucket=bucket_name1)
        s3.create_bucket(Bucket=bucket_name2)

        # Yield a tuple
        yield (bucket_name1, bucket_name2)


@pytest.fixture
def expected_results(request):
    """
    Fixture that loads expected results from a JSON file for a given UUID.
    The UUID is provided via parameterization (indirect=True).

    The JSON file is expected to be in a directory called "JSON" (relative to
    this file) and have the name "<uuid>_RESULTS.json".

    The fixture returns a tuple containing:
        (
            list[Image], list[Line], list[Word], list[Letter],
            list[Receipt], list[ReceiptLine], list[ReceiptWord],
            list[ReceiptLetter],
        )
    """
    uuid = request.param  # The UUID is passed as the parameter
    base_dir = dirname(__file__)
    json_path = join(base_dir, "JSON", f"{uuid}_RESULTS.json")
    with open(json_path, "r", encoding="utf-8") as f:
        results = load(f)

    images = [Image(**img) for img in results.get("images", [])]
    lines = [Line(**line) for line in results.get("lines", [])]
    words = [Word(**word) for word in results.get("words", [])]
    letters = [Letter(**letter) for letter in results.get("letters", [])]
    receipts = [Receipt(**rcpt) for rcpt in results.get("receipts", [])]
    receipt_lines = [ReceiptLine(**rl) for rl in results.get("receipt_lines", [])]
    receipt_words = [ReceiptWord(**rw) for rw in results.get("receipt_words", [])]
    receipt_letters = [
        ReceiptLetter(**rltr) for rltr in results.get("receipt_letters", [])
    ]

    return (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
    )


@pytest.fixture
def sample_receipt_details():
    """
    Provides a sample receipt with its associated words and word labels for testing.
    """
    receipt = Receipt(
        image_id="test_image",
        receipt_id=1,
        width=1000,
        height=2000,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="test-bucket",
        raw_s3_key="test-key",
        top_left={"x": 0.1, "y": 0.1},
        top_right={"x": 0.9, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.9},
        bottom_right={"x": 0.9, "y": 0.9},
        sha256="test-sha256",
        cdn_s3_bucket="test-cdn-bucket",
        cdn_s3_key="test-cdn-key",
    )

    receipt_words = [
        ReceiptWord(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="Test",
            confidence=0.95,
            x1=0.1,
            y1=0.1,
            x2=0.2,
            y2=0.2,
        ),
        ReceiptWord(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=2,
            text="Store",
            confidence=0.95,
            x1=0.3,
            y1=0.1,
            x2=0.4,
            y2=0.2,
        ),
    ]

    word_labels = [
        ReceiptWordLabel(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="store_name",
            confidence=0.95,
        ),
        ReceiptWordLabel(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=2,
            label="store_name",
            confidence=0.95,
        ),
    ]

    return {
        "receipt": receipt,
        "words": receipt_words,
        "word_labels": word_labels,
    }
