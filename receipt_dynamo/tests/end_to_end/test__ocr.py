import sys
import pytest
import boto3
import os

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._ocr import apple_vision_ocr
from receipt_dynamo.data._pulumi import load_env


@pytest.fixture(scope="session")
def dynamodb_table() -> str:
    """
    Fixture that retrieves the DynamoDB table name from Pulumi dev environment.

    Returns:
        str: The name of the DynamoDB table
    """
    env_vars = load_env("prod")
    return env_vars["dynamodb_table_name"]


@pytest.fixture
def test_image():
    """Fixture to manage test image file lifecycle."""
    test_file = "test_image.png"
    yield test_file
    # Cleanup after test completes
    if os.path.exists(test_file):
        os.remove(test_file)


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Apple Vision OCR only works on macOS"
)
@pytest.mark.end_to_end
def test_apple_vision_ocr(dynamodb_table: str, test_image: str):
    """Test that apple_vision_ocr returns a list of lines, words, and letters

    This test downloads the first receipt from the S3 bucket and passes the
    image to the apple_vision_ocr function.
    """
    test_image = "test_image.png"
    client = DynamoClient(dynamodb_table)
    receipts, _ = client.listReceipts(1)
    receipt = receipts[0]
    # Download the image from S3
    s3 = boto3.client("s3")
    bucket_name = receipt.raw_s3_bucket
    key = receipt.raw_s3_key
    s3.download_file(bucket_name, key, test_image)

    # Run the OCR
    ocr_dict = apple_vision_ocr([test_image])

    # Check that the OCR dictionary has the image ID as a key
    assert len(ocr_dict) == 1
    image_id = list(ocr_dict.keys())[0]
    # Check that the OCR dictionary has the lines, words, and letters
    assert len(ocr_dict[image_id]) == 3
    # Check that the lines are not empty
    assert len(ocr_dict[image_id][0]) > 0
    # Check that the words are not empty
    assert len(ocr_dict[image_id][1]) > 0
    # Check that the letters are not empty
    assert len(ocr_dict[image_id][2]) > 0
