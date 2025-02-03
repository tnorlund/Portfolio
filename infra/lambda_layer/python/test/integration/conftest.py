from os.path import dirname, join
from json import load
import functools
import boto3
import pytest
from moto import mock_aws

from dynamo import (
    Image,
    Line,
    Word,
    WordTag,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
)


@pytest.fixture
def dynamodb_table():
    """
    Spins up a mock DynamoDB instance, creates a table (with 2 GSIs: GSI1 and GSITYPE),
    waits until both the table and the GSIs are active, then yields
    the table name for tests.

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
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
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
    Spins up a mock S3 instance, creates a bucket with a name from `request.param`,
    then yields the bucket name for tests.

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
    Spins up a mock S3 instance, creates two buckets (taken from `request.param`),
    then yields (bucket_name1, bucket_name2) for tests.

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
    
    The JSON file is expected to be in a directory called "JSON" (relative to this file)
    and have the name "<uuid>_RESULTS.json".
    
    The fixture returns a tuple containing:
        (list[Image], list[Line], list[Word], list[WordTag], list[Letter],
         list[Receipt], list[ReceiptLine], list[ReceiptWord], list[ReceiptWordTag], list[ReceiptLetter])
    """
    uuid = request.param  # The UUID is passed as the parameter
    base_dir = dirname(__file__)
    json_path = join(base_dir, "JSON", f"{uuid}_RESULTS.json")
    with open(json_path, "r", encoding="utf-8") as f:
        results = load(f)

    images = [Image(**img) for img in results.get("images", [])]
    lines = [Line(**line) for line in results.get("lines", [])]
    words = [Word(**word) for word in results.get("words", [])]
    word_tags = [WordTag(**wt) for wt in results.get("word_tags", [])]
    letters = [Letter(**letter) for letter in results.get("letters", [])]
    receipts = [Receipt(**rcpt) for rcpt in results.get("receipts", [])]
    receipt_lines = [ReceiptLine(**rl) for rl in results.get("receipt_lines", [])]
    receipt_words = [ReceiptWord(**rw) for rw in results.get("receipt_words", [])]
    receipt_word_tags = [ReceiptWordTag(**rwt) for rwt in results.get("receipt_word_tags", [])]
    receipt_letters = [ReceiptLetter(**rltr) for rltr in results.get("receipt_letters", [])]

    return (
        images,
        lines,
        words,
        word_tags,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        receipt_letters,
    )