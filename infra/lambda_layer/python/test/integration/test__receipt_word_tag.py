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
        tag="SampleTag",
        timestamp_added="2021-01-01T00:00:00"
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
        ReceiptWordTag(image_id=1, receipt_id=10, line_id=1, word_id=i, tag=f"Tag{i}", timestamp_added="2021-01-01T00:00:00")
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
        ReceiptWordTag(image_id=1, receipt_id=10, line_id=2, word_id=i, tag=f"ImageTag{i}", timestamp_added="2021-01-01T00:00:00")
        for i in range(1, 3)
    ]
    # Another ReceiptWordTag with a different image_id
    different_image_tag = ReceiptWordTag(
        image_id=2, receipt_id=99, line_id=2, word_id=999, tag="OtherImage", timestamp_added="2021-01-01T00:00:00"
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

@pytest.fixture
def sample_receipt_word_tags():
    """
    Returns multiple ReceiptWordTag objects, with two distinct tags.
    """
    return [
        ReceiptWordTag(image_id=1, receipt_id=100, line_id=10, word_id=10, tag="ALPHA", timestamp_added="2021-01-01T00:00:00"),
        ReceiptWordTag(image_id=2, receipt_id=200, line_id=20, word_id=20, tag="BETA", timestamp_added="2021-01-01T00:00:00"),
        ReceiptWordTag(image_id=3, receipt_id=300, line_id=30, word_id=30, tag="ALPHA", timestamp_added="2021-01-01T00:00:00"),
        ReceiptWordTag(image_id=4, receipt_id=400, line_id=40, word_id=40, tag="BETA", timestamp_added="2021-01-01T00:00:00"),
    ]

def test_get_receipt_word_tags(
    dynamodb_table: Literal["MyMockedTable"], 
    sample_receipt_word_tags: list[ReceiptWordTag]
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptWordTags(sample_receipt_word_tags)

    # Act: Retrieve all with tag="ALPHA"
    alpha = client.getReceiptWordTags("ALPHA")

    # Assert
    # The two we expect with ALPHA
    alpha_expected = {
        (1, 100, 10, 10, "ALPHA"),
        (3, 300, 30, 30, "ALPHA"),
    }
    alpha_returned = {
        (x.image_id, x.receipt_id, x.line_id, x.word_id, x.tag) for x in alpha
    }
    assert alpha_returned == alpha_expected

    # Check BETA
    beta = client.getReceiptWordTags("BETA")
    beta_expected = {
        (2, 200, 20, 20, "BETA"),
        (4, 400, 40, 40, "BETA"),
    }
    beta_returned = {
        (x.image_id, x.receipt_id, x.line_id, x.word_id, x.tag) for x in beta
    }
    assert beta_returned == beta_expected


def test_get_receipt_word_tags_no_results(dynamodb_table: Literal["MyMockedTable"]):
    """
    If tag doesn't exist, we expect an empty list.
    """
    client = DynamoClient(dynamodb_table)
    results = client.getReceiptWordTags("NOTHING")
    assert results == []


def test_get_receipt_word_tags_pagination(dynamodb_table: Literal["MyMockedTable"]):
    """
    Test pagination for receipt word tags by inserting ~30 items
    all with the same tag.
    """
    client = DynamoClient(dynamodb_table)

    big_list = []
    for i in range(30):
        big_list.append(ReceiptWordTag(
            image_id=1, receipt_id=1, line_id=i, word_id=i, tag="PAGE", timestamp_added="2021-01-01T00:00:00"
        ))

    client.addReceiptWordTags(big_list)

    results = client.getReceiptWordTags("PAGE")
    assert len(results) == 30

    returned_tuples = {
        (r.image_id, r.receipt_id, r.line_id, r.word_id) for r in results
    }
    expected_tuples = {(1, 1, i, i) for i in range(30)}
    assert returned_tuples == expected_tuples