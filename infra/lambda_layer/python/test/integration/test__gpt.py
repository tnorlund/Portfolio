# test__gpt.py

import json
import boto3
import pytest
import os
from typing import Dict, Literal, Tuple, Union
from dynamo import (
    DynamoClient,
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
from ._fixtures import sample_gpt_receipt_1


def assert_tags_on_word_and_receipt_word(
    line_id: int,
    word_id: int,
    expected_tags: list[str],
    words: list[Word],
    word_tags: list[WordTag],
    receipt_details,
):
    """
    Assert that the Word and ReceiptWord objects have the expected tags
    (order-insensitive) and that the WordTag and ReceiptWordTag objects exist.
    """

    # 1) Locate the single Word
    matching_words = [w for w in words if w.line_id == line_id and w.id == word_id]
    assert len(matching_words) == 1, (
        f"Expected exactly one Word with (line_id={line_id}, id={word_id}), "
        f"but found {len(matching_words)}."
    )
    matching_word = matching_words[0]

    # 2) Assert the Word's tags (ignore order by converting to a set)
    assert set(matching_word.tags) == set(expected_tags), (
        f"Word(line_id={line_id}, id={word_id}, text={matching_word.text}) has tags={matching_word.tags}, "
        f"but expected tags={expected_tags} (order-insensitive)."
    )

    # 3) Assert the correct number of WordTag entries and that each tag is present
    matching_word_tags = [
        wt for wt in word_tags if wt.line_id == line_id and wt.word_id == word_id
    ]
    assert len(matching_word_tags) == len(expected_tags), (
        f"Expected {len(expected_tags)} WordTag entries for Word with "
        f"(line_id={line_id}, id={word_id}), but found {len(matching_word_tags)}."
    )

    # Sort the WordTag objects and expected_tags by tag
    matching_word_tags_sorted = sorted(matching_word_tags, key=lambda wt: wt.tag)
    expected_tags_sorted = sorted(expected_tags)
    for i, expected_tag in enumerate(expected_tags_sorted):
        assert matching_word_tags_sorted[i].tag == expected_tag, (
            f"Expected WordTag entry {i} to have tag '{expected_tag}', "
            f"but found '{matching_word_tags_sorted[i].tag}'."
        )

    # 4) Locate the single ReceiptWord
    matching_receipt_words = [
        rw
        for rw in receipt_details["words"]
        if rw.line_id == line_id and rw.id == word_id
    ]
    assert len(matching_receipt_words) == 1, (
        f"Expected exactly one ReceiptWord with (line_id={line_id}, id={word_id}), "
        f"but found {len(matching_receipt_words)}."
    )
    matching_receipt_word = matching_receipt_words[0]

    # 5) Assert the ReceiptWord's tags (again, ignore order)
    assert set(matching_receipt_word.tags) == set(expected_tags), (
        f"ReceiptWord(line_id={line_id}, id={word_id}, text={matching_receipt_word.text}) "
        f"has tags={matching_receipt_word.tags}, but expected tags={expected_tags} (order-insensitive)."
    )

    # 6) Assert the correct number of ReceiptWordTag entries and that each tag is present
    matching_receipt_word_tags = [
        rwt
        for rwt in receipt_details["word_tags"]
        if rwt.line_id == line_id and rwt.word_id == word_id
    ]
    assert len(matching_receipt_word_tags) == len(expected_tags), (
        f"Expected {len(expected_tags)} ReceiptWordTag entries for ReceiptWord with "
        f"(line_id={line_id}, id={word_id}), but found {len(matching_receipt_word_tags)}."
    )

    matching_receipt_word_tags_sorted = sorted(
        matching_receipt_word_tags, key=lambda rwt: rwt.tag
    )
    for i, expected_tag in enumerate(expected_tags_sorted):
        assert matching_receipt_word_tags_sorted[i].tag == expected_tag, (
            f"Expected ReceiptWordTag entry {i} to have tag '{expected_tag}', "
            f"but found '{matching_receipt_word_tags_sorted[i].tag}'."
        )


class MockGPTResponse:
    """A simple mock response class with .json() and .status_code."""

    def __init__(self, json_data, status_code=200, text=""):
        self._json_data = json_data
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data

@pytest.mark.integration
def test_gpt_receipt_1(
    sample_gpt_receipt_1: Tuple[
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
    ],
    dynamodb_table: Literal["MyMockedTable"],
    s3_bucket: Literal["raw-image-bucket"],
    monkeypatch,
):
    (
        images,
        lines,
        words,
        _,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        _,
        receipt_letters,
        gpt_result,
    ) = sample_gpt_receipt_1
    _ = s3_bucket
    client = DynamoClient(dynamodb_table)
    image_id = images[0].id
    # Arrange
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
        return MockGPTResponse(
            {
                "choices": [
                    {"message": {"content": f"```json{json.dumps(gpt_result)}```"}}
                ]
            },
            status_code=200,
        )

    monkeypatch.setattr(
        "dynamo.data.dynamo_client.DynamoClient._gpt_request", mock_gpt_request
    )

    # Act
    client.gpt_receipt(image_id)

    # Assert
    # Check that the GPT response was stored in S3
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_json_key = images[0].raw_s3_key.replace(
        ".png",
        f"_GPT_image_{image_id}_receipt_{receipts[0].id:05d}.json",
    )
    obj = s3_client.get_object(Bucket=images[0].raw_s3_bucket, Key=s3_json_key)
    body_text = obj["Body"].read().decode("utf-8")
    assert body_text == json.dumps(gpt_result)

    # Check that the Tags were set correctly
    _, _, words, word_tags, _, receipt_details = client.getImageDetails(image_id)
    # fmt: off
    assert_tags_on_word_and_receipt_word(2, 1, ["store_name"], words, word_tags, receipt_details[0]) # VONS
    assert_tags_on_word_and_receipt_word(3, 3, ["address"], words, word_tags, receipt_details[0]) # 497-1921
    assert_tags_on_word_and_receipt_word(4, 2, ["address"], words, word_tags, receipt_details[0]) # Agoura
    assert_tags_on_word_and_receipt_word(4, 3, ["address"], words, word_tags, receipt_details[0]) # Road
    assert_tags_on_word_and_receipt_word(5, 1, ["address"], words, word_tags, receipt_details[0]) # WESTLAKE
    assert_tags_on_word_and_receipt_word(5, 2, ["address"], words, word_tags, receipt_details[0]) # CA
    assert_tags_on_word_and_receipt_word(5, 3, ["address"], words, word_tags, receipt_details[0]) # 91360
    assert_tags_on_word_and_receipt_word(8, 2, ["line_item_name", "line_item"], words, word_tags, receipt_details[0]) # PURE
    assert_tags_on_word_and_receipt_word(8, 3, ["line_item_name", "line_item"], words, word_tags, receipt_details[0]) # LIFE
    assert_tags_on_word_and_receipt_word(8, 4, ["line_item_name", "line_item"], words, word_tags, receipt_details[0]) # WATER
    assert_tags_on_word_and_receipt_word(20, 3, ["date"], words, word_tags, receipt_details[0]) # 03/19/24
    assert_tags_on_word_and_receipt_word(20, 4, ["time"], words, word_tags, receipt_details[0]) # 13:29
    assert_tags_on_word_and_receipt_word(24, 1, ["line_item_price", "line_item"], words, word_tags, receipt_details[0]) # 3.60
    assert_tags_on_word_and_receipt_word(33, 1, ["total_amount"], words, word_tags, receipt_details[0]) # 3.60
    assert_tags_on_word_and_receipt_word(40, 1, ["phone_number"], words, word_tags, receipt_details[0]) # 877-276-9637
    # fmt: on

@pytest.mark.integration
def test_gpt_receipt_bad_response(
    sample_gpt_receipt_1,
    dynamodb_table: Literal["MyMockedTable"],
    s3_bucket: Literal["raw-image-bucket"],
    monkeypatch,
):
    (
        images,
        _,
        _,
        _,
        _,
        receipts,
        _,
        _,
        _,
        _,
        _,
    ) = sample_gpt_receipt_1
    client = DynamoClient(dynamodb_table)
    _ = s3_bucket
    image_id = images[0].id

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
        client.gpt_receipt(image_id)

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
        f"_failure_image_{image_id}_receipt_{test_receipt.id:05d}.txt",
    )

    # Now fetch from S3 and confirm the Body is what we expected
    obj = s3_client.get_object(Bucket=test_image.raw_s3_bucket, Key=failure_key)
    body_text = obj["Body"].read().decode("utf-8")
    assert body_text == "Internal Server Error"

@pytest.mark.integration
def test_gpt_no_api_key(
    sample_gpt_receipt_1,
    dynamodb_table: Literal["MyMockedTable"],
):
    (images, _, _, _, _, receipts, _, _, _, _, _) = sample_gpt_receipt_1
    client = DynamoClient(dynamodb_table)
    image_id = images[0].id

    client.addReceipts(receipts)
    retrieved_receipts = client.listReceipts()

    os.environ["OPENAI_API_KEY"] = ""  # Set to empty string

    with pytest.raises(
        ValueError, match="The OPENAI_API_KEY environment variable is not set."
    ) as exc:
        client.gpt_receipt(image_id)
