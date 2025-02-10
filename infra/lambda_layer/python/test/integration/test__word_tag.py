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
    return WordTag(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
        tag="SampleTag",
        timestamp_added="2021-01-01T00:00:00",
    )


@pytest.mark.integration
def test_add_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addWordTag(sample_word_tag)

    # Assert
    retrieved_tag = client.getWordTag(
        sample_word_tag.image_id,
        sample_word_tag.line_id,
        sample_word_tag.word_id,
        sample_word_tag.tag,
    )
    assert retrieved_tag == sample_word_tag


@pytest.mark.integration
def test_add_word_tag_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addWordTag(sample_word_tag)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addWordTag(sample_word_tag)


@pytest.mark.integration
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
        sample_word_tag.image_id,
        sample_word_tag.line_id,
        sample_word_tag.word_id,
        sample_word_tag.tag,
    )
    assert retrieved_tag.tag == "UpdatedTag"


@pytest.mark.integration
def test_delete_word_tag(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tag: WordTag
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addWordTag(sample_word_tag)

    # Act
    client.deleteWordTag(
        sample_word_tag.image_id,
        sample_word_tag.line_id,
        sample_word_tag.word_id,
        sample_word_tag.tag,
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getWordTag(
            sample_word_tag.image_id,
            sample_word_tag.line_id,
            sample_word_tag.word_id,
            sample_word_tag.tag,
        )


@pytest.mark.integration
def test_word_tag_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    tags = [
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=i,
            tag=f"Tag{i}",
            timestamp_added="2021-01-01T00:00:00",
        )
        for i in range(1, 4)
    ]
    for t in tags:
        client.addWordTag(t)

    # Act
    returned_tags, _ = client.listWordTags()

    # Assert
    for t in tags:
        assert t in returned_tags


@pytest.mark.integration
def test_word_tag_list_from_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # WordTags in image_id=1
    same_image_tags = [
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=i,
            tag=f"ImageTag{i}",
            timestamp_added="2021-01-01T00:00:00",
        )
        for i in range(1, 3)
    ]
    # Another WordTag with a different image_id
    different_image_tag = WordTag(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4",
        line_id=10,
        word_id=999,
        tag="OtherImage",
        timestamp_added="2021-01-01T00:00:00",
    )

    for wt in same_image_tags + [different_image_tag]:
        client.addWordTag(wt)

    # Act
    found_tags = client.listWordTagsFromImage("3f52804b-2fad-4e00-92c8-b593da3a8ed3")

    # Assert
    assert len(found_tags) == len(same_image_tags)
    for wt in same_image_tags:
        assert wt in found_tags
    assert different_image_tag not in found_tags


@pytest.fixture
def sample_word_tags():
    """
    Returns two groups of WordTag items:
      - Group A has tag="FOO" (underscore-padded to "FOO" eventually)
      - Group B has tag="BAR"
    """
    return [
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=100,
            tag="FOO",
            timestamp_added="2021-01-01T00:00:00",
        ),
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=11,
            word_id=101,
            tag="FOO",
            timestamp_added="2021-01-01T00:00:00",
        ),
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4",
            line_id=20,
            word_id=200,
            tag="BAR",
            timestamp_added="2021-01-01T00:00:00",
        ),
        WordTag(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed5",
            line_id=30,
            word_id=300,
            tag="FOO",
            timestamp_added="2021-01-01T00:00:00",
        ),
    ]


@pytest.mark.integration
def test_get_word_tags(
    dynamodb_table: Literal["MyMockedTable"], sample_word_tags: list[WordTag]
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    # Add them all
    client.addWordTags(sample_word_tags)

    # Act: Retrieve all WordTags with tag="FOO"
    foo_tags = client.getWordTags("FOO")

    # Assert: We expect 3 items with tag=FOO
    # Convert objects to sets of (image_id, line_id, word_id, tag) to compare easily
    foo_expected = {
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 10, 100, "FOO"),
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 11, 101, "FOO"),
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed5", 30, 300, "FOO"),
    }
    foo_returned = {(w.image_id, w.line_id, w.word_id, w.tag) for w in foo_tags}
    assert foo_returned == foo_expected

    # Also check that "BAR" is distinct
    bar_tags = client.getWordTags("BAR")
    bar_expected = {
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 20, 200, "BAR"),
    }
    bar_returned = {(w.image_id, w.line_id, w.word_id, w.tag) for w in bar_tags}
    assert bar_returned == bar_expected


@pytest.mark.integration
def test_word_tag_get_no_results(dynamodb_table: Literal["MyMockedTable"]):
    """
    If we request a tag that doesn't exist, we should get an empty list.
    """
    client = DynamoClient(dynamodb_table)
    # No items added
    results = client.getWordTags("NONEXISTENT")
    assert results == []


@pytest.mark.integration
def test_word_tag_get_pagination(dynamodb_table: Literal["MyMockedTable"]):
    """
    Test pagination by adding more than one 'page' (the default DynamoDB
    Query limit can be around 1 MB or 1k items). We'll just add ~30 items
    all with tag='PAGE'.
    """
    client = DynamoClient(dynamodb_table)

    big_list = []
    for i in range(30):
        big_list.append(
            WordTag(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4",
                line_id=1,
                word_id=i,
                tag="PAGE",
                timestamp_added="2021-01-01T00:00:00",
            )
        )

    client.addWordTags(big_list)

    results = client.getWordTags("PAGE")
    # Should retrieve all 30
    assert len(results) == 30
    # Compare sets
    returned_ids = {(r.image_id, r.line_id, r.word_id) for r in results}
    expected_ids = {("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 1, i) for i in range(30)}
    assert returned_ids == expected_ids
