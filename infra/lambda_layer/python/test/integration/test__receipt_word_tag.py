# test__receipt_word_tag.py

import pytest
from typing import Literal

# Import your ReceiptWordTag entity and DynamoClient (or similar class).
# Adjust paths as necessary for your project.
from dynamo.entities.receipt_word_tag import ReceiptWordTag
from dynamo import DynamoClient  # or whatever your client is called


@pytest.fixture
def sample_receipt_word_tag():
    """
    Provides a sample ReceiptWordTag for testing.
    Adjust the IDs or tag text to fit your schema if needed.
    """
    return ReceiptWordTag(
        image_id=1,
        receipt_id=100,
        line_id=5,
        word_id=42,
        tag="SampleTag"
    )


def test_add_receipt_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word_tag: ReceiptWordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addReceiptWordTag(sample_receipt_word_tag)

    # Assert
    retrieved = client.getReceiptWordTag(
        sample_receipt_word_tag.image_id,
        sample_receipt_word_tag.receipt_id,
        sample_receipt_word_tag.line_id,
        sample_receipt_word_tag.word_id,
        sample_receipt_word_tag.tag
    )
    assert retrieved == sample_receipt_word_tag


def test_add_receipt_word_tag_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word_tag: ReceiptWordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptWordTag(sample_receipt_word_tag)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptWordTag(sample_receipt_word_tag)


def test_update_receipt_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word_tag: ReceiptWordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptWordTag(sample_receipt_word_tag)

    # Act: Update the tag from "SampleTag" to "UpdatedTag"
    sample_receipt_word_tag.tag = "UpdatedTag"
    client.updateReceiptWordTag(sample_receipt_word_tag)

    # Assert
    retrieved = client.getReceiptWordTag(
        sample_receipt_word_tag.image_id,
        sample_receipt_word_tag.receipt_id,
        sample_receipt_word_tag.line_id,
        sample_receipt_word_tag.word_id,
        sample_receipt_word_tag.tag
    )
    assert retrieved.tag == "UpdatedTag"


def test_delete_receipt_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word_tag: ReceiptWordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptWordTag(sample_receipt_word_tag)

    # Act
    client.deleteReceiptWordTag(
        image_id=sample_receipt_word_tag.image_id,
        receipt_id=sample_receipt_word_tag.receipt_id,
        line_id=sample_receipt_word_tag.line_id,
        word_id=sample_receipt_word_tag.word_id,
        tag=sample_receipt_word_tag.tag
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getReceiptWordTag(
            sample_receipt_word_tag.image_id,
            sample_receipt_word_tag.receipt_id,
            sample_receipt_word_tag.line_id,
            sample_receipt_word_tag.word_id,
            sample_receipt_word_tag.tag
        )


def test_list_receipt_word_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    receipt_word_tags = [
        ReceiptWordTag(image_id=1, receipt_id=10, line_id=1, word_id=i, tag=f"Tag{i}")
        for i in range(1, 4)
    ]
    for rwt in receipt_word_tags:
        client.addReceiptWordTag(rwt)

    # Act
    returned_tags = client.listReceiptWordTags()

    # Assert
    for rwt in receipt_word_tags:
        assert rwt in returned_tags


def test_list_receipt_word_tags_from_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # ReceiptWordTags for image_id=1
    same_image_tags = [
        ReceiptWordTag(image_id=1, receipt_id=10, line_id=2, word_id=i, tag=f"ImageTag{i}")
        for i in range(1, 3)
    ]
    # Another ReceiptWordTag with a different image_id
    different_image_tag = ReceiptWordTag(
        image_id=2, receipt_id=99, line_id=2, word_id=999, tag="OtherImage"
    )

    for rwt in same_image_tags + [different_image_tag]:
        client.addReceiptWordTag(rwt)

    # Act
    found_tags = client.listReceiptWordTagsFromImage(1)

    # Assert
    assert len(found_tags) == len(same_image_tags)
    for rwt in same_image_tags:
        assert rwt in found_tags
    assert different_image_tag not in found_tags