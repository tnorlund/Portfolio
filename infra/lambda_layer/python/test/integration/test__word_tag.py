# test__word_tag.py

import pytest
from typing import Literal
from dynamo import WordTag, DynamoClient


@pytest.fixture
def sample_word_tag():
    """
    Provides a sample WordTag for testing.
    Adjust the IDs or tag text as needed for your schema.
    """
    return WordTag(image_id=1, line_id=10, word_id=5, tag="SampleTag")


def test_add_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addWordTag(sample_word_tag)

    # Assert
    retrieved_tag = client.getWordTag(
        sample_word_tag.image_id, sample_word_tag.line_id, sample_word_tag.word_id, sample_word_tag.tag
    )
    assert retrieved_tag == sample_word_tag


def test_add_word_tag_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addWordTag(sample_word_tag)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addWordTag(sample_word_tag)


def test_update_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addWordTag(sample_word_tag)

    # Act: Update the tag from "SampleTag" to "UpdatedTag"
    sample_word_tag.tag = "UpdatedTag"
    client.updateWordTag(sample_word_tag)

    # Assert
    retrieved_tag = client.getWordTag(
        sample_word_tag.image_id, sample_word_tag.line_id, sample_word_tag.word_id, sample_word_tag.tag
    )
    assert retrieved_tag.tag == "UpdatedTag"


def test_delete_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addWordTag(sample_word_tag)

    # Act
    client.deleteWordTag(
        sample_word_tag.image_id, sample_word_tag.line_id, sample_word_tag.word_id, sample_word_tag.tag
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getWordTag(
            sample_word_tag.image_id, sample_word_tag.line_id, sample_word_tag.word_id, sample_word_tag.tag
        )


def test_list_word_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    tags = [
        WordTag(image_id=1, line_id=1, word_id=i, tag=f"Tag{i}") for i in range(1, 4)
    ]
    for t in tags:
        client.addWordTag(t)

    # Act
    returned_tags = client.listWordTags()

    # Assert
    for t in tags:
        assert t in returned_tags


def test_list_word_tags_from_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # WordTags in image_id=1
    same_image_tags = [
        WordTag(image_id=1, line_id=10, word_id=i, tag=f"ImageTag{i}")
        for i in range(1, 3)
    ]
    # Another WordTag with a different image_id
    different_image_tag = WordTag(image_id=2, line_id=10, word_id=999, tag="OtherImage")

    for wt in same_image_tags + [different_image_tag]:
        client.addWordTag(wt)

    # Act
    found_tags = client.listWordTagsFromImage(1)

    # Assert
    assert len(found_tags) == len(same_image_tags)
    for wt in same_image_tags:
        assert wt in found_tags
    assert different_image_tag not in found_tags
