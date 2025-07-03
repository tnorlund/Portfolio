# infra/lambda_layer/python/test/integration/test__export_and_import.py
import datetime
import os
from shutil import rmtree

import pytest

from receipt_dynamo import (DynamoClient, Image, Letter, Line, Receipt,
                            ReceiptLetter, ReceiptLine, ReceiptWord, Word,
                            export_image, import_image)


@pytest.fixture
def export_dir():
    """
    Fixture to provide a temporary directory for exports.
    Ensures it is removed after the test completes.
    """
    path = "export_dir_for_test"
    # Make sure it's clean before we start (optional)
    if os.path.exists(path):
        rmtree(path)

    os.makedirs(path, exist_ok=True)

    yield path  # The test will use this directory

    # Cleanup after test
    if os.path.exists(path):
        rmtree(path)


@pytest.mark.integration
def test_export_and_import_image(dynamodb_table, export_dir):
    # Arrange
    client = DynamoClient(dynamodb_table)

    image = Image(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        width=100,
        height=100,
        timestamp_added=datetime.datetime.now(datetime.timezone.utc),
        raw_s3_bucket="test_bucket",
        raw_s3_key="test_key",
        sha256="test_sha256",
        cdn_s3_bucket="test_cdn_bucket",
        cdn_s3_key="test_cdn_key",
    )
    lines = [
        Line(
            image_id=image.image_id,
            line_id=1,
            text="Hello, world!",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]
    words = [
        Word(
            image_id=image.image_id,
            line_id=lines[0].line_id,
            word_id=1,
            text="Hello",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]
    letters = [
        Letter(
            image_id=image.image_id,
            line_id=lines[0].line_id,
            word_id=words[0].word_id,
            letter_id=1,
            text="H",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]
    receipts = [
        Receipt(
            image_id=image.image_id,
            receipt_id=1,
            width=100,
            height=100,
            timestamp_added=datetime.datetime.now(datetime.timezone.utc),
            raw_s3_bucket="test_bucket",
            raw_s3_key="test_key",
            top_left={
                "x": 0,
                "y": 0,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
        )
    ]
    receipt_lines = [
        ReceiptLine(
            receipt_id=receipts[0].receipt_id,
            image_id=image.image_id,
            line_id=1,
            text="Hello, world!",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]
    receipt_words = [
        ReceiptWord(
            image_id=image.image_id,
            receipt_id=receipts[0].receipt_id,
            line_id=receipt_lines[0].line_id,
            word_id=words[0].word_id,
            text="Hello",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]
    receipt_letters = [
        ReceiptLetter(
            image_id=image.image_id,
            receipt_id=receipts[0].receipt_id,
            line_id=receipt_lines[0].line_id,
            word_id=receipt_words[0].word_id,
            letter_id=letters[0].letter_id,
            text="H",
            bounding_box={
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            top_right={
                "x": 100,
                "y": 0,
            },
            top_left={
                "x": 0,
                "y": 0,
            },
            bottom_right={
                "x": 100,
                "y": 100,
            },
            bottom_left={
                "x": 0,
                "y": 100,
            },
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        )
    ]

    client.add_image(image)
    client.add_lines(lines)
    client.add_words(words)
    client.add_letters(letters)
    client.add_receipts(receipts)
    client.add_receipt_lines(receipt_lines)
    client.add_receipt_words(receipt_words)
    client.add_receipt_letters(receipt_letters)
    # Act
    export_image(dynamodb_table, image.image_id, output_dir=export_dir)

    # Assert
    assert os.path.exists(f"{export_dir}/{image.image_id}.json")

    # Act
    client.delete_images([image])
    client.delete_lines(lines)
    client.delete_words(words)
    client.delete_letters(letters)
    client.delete_receipts(receipts)
    client.delete_receipt_lines(receipt_lines)
    client.delete_receipt_words(receipt_words)
    client.delete_receipt_letters(receipt_letters)

    # Assert
    assert len(client.list_images()[0]) == 0
    assert len(client.list_lines()[0]) == 0
    assert len(client.list_words()[0]) == 0
    assert len(client.list_letters()[0]) == 0
    assert len(client.list_receipts()[0]) == 0
    assert len(client.list_receipt_lines()[0]) == 0
    assert len(client.list_receipt_words()[0]) == 0
    assert len(client.list_receipt_letters()[0]) == 0

    # Act
    import_image(dynamodb_table, f"{export_dir}/{image.image_id}.json")

    # Assert
    assert len(client.list_images()[0]) == 1
    assert client.list_images()[0][0].image_id == image.image_id
    assert len(client.list_lines()[0]) == 1
    assert client.list_lines()[0][0].line_id == 1
    assert client.list_lines()[0][0].text == "Hello, world!"
    assert len(client.list_words()[0]) == 1
    assert client.list_words()[0][0].word_id == 1
    assert client.list_words()[0][0].text == "Hello"
    assert len(client.list_letters()[0]) == 1
    assert client.list_letters()[0][0].letter_id == 1
    assert client.list_letters()[0][0].text == "H"
    assert len(client.list_receipts()[0]) == 1
    assert client.list_receipts()[0][0].receipt_id == 1
    assert len(client.list_receipt_lines()[0]) == 1
    assert client.list_receipt_lines()[0][0].receipt_id == 1
    assert len(client.list_receipt_words()[0]) == 1
    assert client.list_receipt_words()[0][0].text == "Hello"
    assert len(client.list_receipt_letters()[0]) == 1
    assert client.list_receipt_letters()[0][0].text == "H"


@pytest.mark.integration
def test_export_image_does_not_exist(dynamodb_table, export_dir):
    with pytest.raises(ValueError, match="No image found for image_id"):
        export_image(dynamodb_table, "does_not_exist", output_dir=export_dir)


@pytest.mark.integration
def test_import_image_does_not_exist(dynamodb_table, export_dir):
    with pytest.raises(FileNotFoundError, match="JSON file not found"):
        import_image(dynamodb_table, f"{export_dir}/does_not_exist.json")
