from typing import Literal
from datetime import datetime
import pytest
from dynamo import (
    Receipt,
    ReceiptLine,
    Image,
    DynamoClient,
    ReceiptWordTag,
    ReceiptWord,
    ReceiptLetter,
)


@pytest.fixture
def sample_receipt_word_tag():
    """
    Provides a sample ReceiptWordTag for testing.
    Adjust the IDs or tag text to fit your schema if needed.
    """
    return ReceiptWordTag(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=100,
        line_id=5,
        word_id=42,
        tag="SampleTag",
    )


@pytest.fixture
def sample_receipt():
    """
    Provides a sample Receipt for testing.
    Adjust the IDs or tag text to fit your schema if needed.
    """
    return Receipt(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        width=10,
        height=20,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        sha256="sha256",
    )


@pytest.fixture
def sample_receipt_word():
    """
    Provides a sample ReceiptWord for testing.
    Adjust the IDs or tag text to fit your schema if needed.
    """
    return ReceiptWord(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=1,
        word_id=1,
        text="text",
        bounding_box={
            "x": 0,
            "y": 0,
            "width": 10,
            "height": 20,
        },
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


correct_receipt_params = {
    "receipt_id": 1,
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
    "top_left": {"x": 0, "y": 0},
    "top_right": {"x": 10, "y": 0},
    "bottom_left": {"x": 0, "y": 20},
    "bottom_right": {"x": 10, "y": 20},
    "sha256": "sha256",
}

correct_image_params = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
}


@pytest.mark.integration
def test_addReceipt(dynamodb_table: Literal["MyMockedTable"], sample_receipt: Receipt):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)

    # Act
    dynamo_client.addReceipt(sample_receipt)

    # Assert
    response_receipt = dynamo_client.getReceipt(
        sample_receipt.image_id, sample_receipt.receipt_id
    )
    assert response_receipt == sample_receipt


@pytest.mark.integration
def test_addReceipt_error_receipt_that_exists(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt: Receipt
):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)

    # Act
    dynamo_client.addReceipt(sample_receipt)
    with pytest.raises(ValueError):
        dynamo_client.addReceipt(sample_receipt)


@pytest.mark.integration
def test_addReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "receipt_id": 2}),
    ]

    # Act
    dynamo_client.addReceipts(receipts)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert receipts == response_receipts


@pytest.mark.integration
def test_addReceipts_error_receipt_that_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    dynamo_client.addReceipt(receipts[0])
    with pytest.raises(ValueError):
        dynamo_client.addReceipts(receipts)


@pytest.mark.integration
def test_updateReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)
    receipt.s3_key = "new/path"

    # Act
    dynamo_client.updateReceipt(receipt)

    # Assert
    response_receipt = dynamo_client.getReceipt(receipt.image_id, receipt.receipt_id)
    assert response_receipt == receipt


@pytest.mark.integration
def test_updateReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.updateReceipt(receipt)


@pytest.mark.integration
def test_updateReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "receipt_id": 2}),
    ]
    dynamo_client.addReceipts(receipts)
    for receipt in receipts:
        receipt.s3_key = "new/path"

    # Act
    dynamo_client.updateReceipts(receipts)

    # Assert
    assert dynamo_client.listReceipts() == receipts


@pytest.mark.integration
def test_updateReceipts_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    with pytest.raises(ValueError):
        dynamo_client.updateReceipts(receipts)


@pytest.mark.integration
def test_deleteReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)

    # Act
    dynamo_client.deleteReceipt(receipt)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert receipt not in response_receipts


@pytest.mark.integration
def test_deleteReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceipt(receipt)


@pytest.mark.integration
def test_deleteReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "receipt_id": 2}),
    ]
    dynamo_client.addReceipts(receipts)

    # Act
    dynamo_client.deleteReceipts(receipts)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert not response_receipts


@pytest.mark.integration
def test_deleteReceipts_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceipts(receipts)


@pytest.mark.integration
def test_deleteReceiptsFromImage(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    image = Image(**correct_image_params)
    receipt_different_image = Receipt(
        **{**correct_receipt_params, "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed4"}
    )
    dynamo_client.addReceipts([receipt, receipt_different_image])
    dynamo_client.addImage(image)

    # Act
    dynamo_client.deleteReceiptsFromImage(receipt.image_id)

    # Assert
    assert dynamo_client.getImage(image.image_id) == image
    assert (
        dynamo_client.getReceipt(
            receipt_different_image.image_id, receipt_different_image.receipt_id
        )
        == receipt_different_image
    )
    with pytest.raises(ValueError):
        dynamo_client.getReceipt(receipt.image_id, receipt.receipt_id)


@pytest.mark.integration
def test_deleteReceiptsFromImage_error_no_receipts_in_image(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    image_id = 1

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceiptsFromImage(image_id)


@pytest.mark.integration
def test_getReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)

    # Act
    retrieved_receipt = dynamo_client.getReceipt(receipt.image_id, receipt.receipt_id)

    # Assert
    assert retrieved_receipt == receipt


@pytest.mark.integration
def test_getReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.getReceipt(receipt.image_id, receipt.receipt_id)


@pytest.mark.integration
def test_getReceiptsFromImage(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    receipt_same_image = Receipt(**{**correct_receipt_params, "receipt_id": 2})
    receipt_different_image = Receipt(
        **{**correct_receipt_params, "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed4"}
    )
    dynamo_client.addReceipts([receipt, receipt_same_image, receipt_different_image])

    # Act
    retrieved_receipts = dynamo_client.getReceiptsFromImage(receipt.image_id)

    # Assert
    assert receipt in retrieved_receipts
    assert receipt_same_image in retrieved_receipts
    assert receipt_different_image not in retrieved_receipts


@pytest.fixture
def sample_receipt_lines():
    """
    Provides a sample list of ReceiptWord for testing.
    """
    return [
        ReceiptLine(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            text="line1",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptLine(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            text="line2",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptLine(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=3,
            text="line3",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptLine(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=4,
            text="line4",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
    ]


@pytest.fixture
def sample_receipt_words():
    return [
        ReceiptWord(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            id=1,
            text="word1",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptWord(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            id=2,
            text="word2",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
    ]


@pytest.fixture
def sample_receipt_word_tags():
    return [
        ReceiptWordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=1,
            tag="tag1",
            timestamp_added="2021-01-01T00:00:00",
        ),
        ReceiptWordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=2,
            tag="tag2",
            timestamp_added="2021-01-01T00:00:00",
        ),
    ]


@pytest.fixture
def sample_receipt_letters():
    return [
        ReceiptLetter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=1,
            id=1,
            text="a",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptLetter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=1,
            id=2,
            text="b",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_right={"x": 10, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 10, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
    ]


@pytest.mark.integration
def test_getReceiptDetails(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    sample_receipt_lines: list[ReceiptLine],
    sample_receipt_words: list[ReceiptWord],
    sample_receipt_word_tags: list[ReceiptWordTag],
    sample_receipt_letters: list[ReceiptLetter],
):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    dynamo_client.addReceipt(sample_receipt)
    dynamo_client.addReceiptLines(sample_receipt_lines)
    dynamo_client.addReceiptWords(sample_receipt_words)
    dynamo_client.addReceiptWordTags(sample_receipt_word_tags)
    dynamo_client.addReceiptLetters(sample_receipt_letters)

    # Act

    payload = dynamo_client.getReceiptDetails(
        sample_receipt.image_id, sample_receipt.receipt_id
    )

    # Assert
    len(payload) == 5
    (
        retrieved_receipt,
        retrieved_lines,
        retrieved_words,
        retrieved_letters,
        retrieved_tags,
    ) = payload
    assert retrieved_receipt == sample_receipt
    assert retrieved_lines == sample_receipt_lines
    assert retrieved_words == sample_receipt_words
    assert retrieved_letters == sample_receipt_letters
    assert retrieved_tags == sample_receipt_word_tags
