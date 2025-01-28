# test__gpt.py

import boto3
import pytest
import os
from typing import Literal, Tuple
from dynamo import (
    DynamoClient,
    Image,
    Line,
    Word,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)
from ._fixtures import sample_gpt_receipt


class MockGPTResponse:
    """A simple mock response class with .json() and .status_code."""

    def __init__(self, json_data, status_code=200, text=""):
        self._json_data = json_data
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


def test_gpt_receipt(
    sample_gpt_receipt: Tuple[
        list[Image],
        list[Line],
        list[Word],
        list[Letter],
        list[Receipt],
        list[ReceiptLine],
        list[ReceiptWord],
        list[ReceiptLetter],
    ],
    dynamodb_table: Literal["MyMockedTable"],
    monkeypatch,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        gpt_result,
    ) = sample_gpt_receipt
    client.addImages(images)
    client.addLines(lines)
    client.addWords(words)
    client.addLetters(letters)
    client.addReceipts(receipts)
    client.addReceiptLines(receipt_lines)
    client.addReceiptWords(receipt_words)
    client.addReceiptLetters(receipt_letters)
    os.environ["OPENAI_API_KEY"] = "my-openai-api-key"

    def mock_gpt_request(self, receipt, receipt_words):
        return MockGPTResponse({"choices":[{"message":{"content":gpt_result}}]}, status_code=200)

    monkeypatch.setattr(DynamoClient, "_gpt_request", mock_gpt_request)

    # Act
    client.gpt_receipt(1)

    # Assert
    print()
    tags = client.listWordTags()
    for tag in tags:
        print(tag)


def test_gpt_receipt_bad_response(
    sample_gpt_receipt,  # same fixture you used before
    dynamodb_table: Literal["MyMockedTable"],
    s3_bucket: Literal["raw-image-bucket"],
    monkeypatch,
):
    (
        images,
        _,
        _,
        _,
        receipts,
        _,
        _,
        _,
        _,
    ) = sample_gpt_receipt
    client = DynamoClient(dynamodb_table)
    _ = s3_bucket

    client.addImages(images)
    client.addReceipts(receipts)

    os.environ["OPENAI_API_KEY"] = "my-openai-api-key"

    def mock_gpt_request(self, receipt, receipt_words):
        _ = receipt, receipt_words
        return MockGPTResponse({}, status_code=500, text="Internal Server Error")

    monkeypatch.setattr(DynamoClient, "_gpt_request", mock_gpt_request)

    # 1) Call gpt_receipt(1), expecting a ValueError (non-200 code)
    with pytest.raises(
        ValueError,
        match=f"An error occurred while making the request: Internal Server Error",
    ) as exc:
        client.gpt_receipt(1)

    # 2) Verify the error message was what we expect
    assert "Internal Server Error" in str(exc.value)

    # 3) Check that the file was written to S3
    s3_client = boto3.client(
        "s3", region_name="us-east-1"
    )  # or same region as your mock
    # We know there's exactly one Image with id=1 in the fixture, or whichever you’re testing
    test_image = images[0]  # or find the one with .id == 1
    # We also assume there's at least one Receipt with .id == 1, etc.
    # By default, let's just pick receipts[0] if we know there's only one for image_id=1
    test_receipt = receipts[0]

    # Reproduce the failure_key logic in your code:
    failure_key = test_image.raw_s3_key.replace(
        ".png",
        f"_failure_image_{test_image.id:05d}_receipt_{test_receipt.id:05d}.txt",
    )

    # Now fetch from S3 and confirm the Body is what we expected
    obj = s3_client.get_object(Bucket=test_image.raw_s3_bucket, Key=failure_key)
    body_text = obj["Body"].read().decode("utf-8")
    assert body_text == "Internal Server Error"


def test_gpt_no_api_key(
    sample_gpt_receipt,
    dynamodb_table: Literal["MyMockedTable"],
):
    (_, _, _, _, receipts, _, _, _, _) = sample_gpt_receipt
    client = DynamoClient(dynamodb_table)

    client.addReceipts(receipts)

    os.environ["OPENAI_API_KEY"] = ""  # Set to empty string

    with pytest.raises(
        ValueError, match="The OPENAI_API_KEY environment variable is not set."
    ) as exc:
        client.gpt_receipt(1)
