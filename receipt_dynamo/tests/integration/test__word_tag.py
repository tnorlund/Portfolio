# test__word_tag.py

from typing import Literal

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, WordTag


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
    found_tags = client.listWordTagsFromImage(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )

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
    # Convert objects to sets of (image_id, line_id, word_id, tag) to compare
    # easily
    foo_expected = {
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 10, 100, "FOO"),
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 11, 101, "FOO"),
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed5", 30, 300, "FOO"),
    }
    foo_returned = {
        (w.image_id, w.line_id, w.word_id, w.tag) for w in foo_tags
    }
    assert foo_returned == foo_expected

    # Also check that "BAR" is distinct
    bar_tags = client.getWordTags("BAR")
    bar_expected = {
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 20, 200, "BAR"),
    }
    bar_returned = {
        (w.image_id, w.line_id, w.word_id, w.tag) for w in bar_tags
    }
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
    expected_ids = {
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 1, i) for i in range(30)
    }
    assert returned_ids == expected_ids


@pytest.mark.integration
def test_updateWordTags_success(dynamodb_table, sample_word_tag):
    """
    Tests happy path for updateWordTags.
    """
    client = DynamoClient(dynamodb_table)
    tag1 = sample_word_tag
    tag2 = WordTag(
        image_id=sample_word_tag.image_id,
        line_id=11,
        word_id=6,
        tag="AnotherTag",
        timestamp_added="2021-01-01T00:00:00",
    )
    client.addWordTags([tag1, tag2])

    # Store the original keys before updating
    tag1.key()
    tag2.key()

    # Now update them
    tag1.tag = "UpdatedTag1"
    tag2.tag = "UpdatedTag2"

    # First delete the old tags
    client.deleteWordTags(
        [
            WordTag(
                image_id=tag1.image_id,
                line_id=tag1.line_id,
                word_id=tag1.word_id,
                tag=sample_word_tag.tag,  # Original tag value
                timestamp_added=tag1.timestamp_added,
            ),
            WordTag(
                image_id=tag2.image_id,
                line_id=tag2.line_id,
                word_id=tag2.word_id,
                tag="AnotherTag",  # Original tag value
                timestamp_added=tag2.timestamp_added,
            ),
        ]
    )

    # Then add the updated tags
    client.addWordTags([tag1, tag2])

    # Verify updates - use getWordTags to find by tag value
    tags_with_updated_tag1 = client.getWordTags("UpdatedTag1")
    tags_with_updated_tag2 = client.getWordTags("UpdatedTag2")

    assert len(tags_with_updated_tag1) == 1
    assert len(tags_with_updated_tag2) == 1
    assert tags_with_updated_tag1[0].tag == "UpdatedTag1"
    assert tags_with_updated_tag2[0].tag == "UpdatedTag2"


@pytest.mark.integration
def test_updateWordTags_raises_value_error_word_tags_none(dynamodb_table):
    """
    Tests that updateWordTags raises ValueError when the word_tags parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="WordTags parameter is required and cannot be None."
    ):
        client.updateWordTags(None)  # type: ignore


@pytest.mark.integration
def test_updateWordTags_raises_value_error_word_tags_not_list(dynamodb_table):
    """
    Tests that updateWordTags raises ValueError when the word_tags parameter
    is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="WordTags must be provided as a list."
    ):
        client.updateWordTags("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateWordTags_raises_value_error_word_tags_not_list_of_word_tags(
    dynamodb_table, sample_word_tag
):
    """
    Tests that updateWordTags raises ValueError when the word_tags parameter
    is not a list of WordTag instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="All items in the word_tags list must be instances of the "
        "WordTag class.",
    ):
        client.updateWordTags([sample_word_tag, "not-a-word-tag"])


@pytest.mark.integration
def test_updateWordTags_raises_clienterror_conditional_check_failed(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Tests that updateWordTags raises ValueError when trying to update
    non-existent word tags.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "One or more word tags do not exist",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(ValueError, match="One or more word tags do not exist"):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWordTags_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Tests that updateWordTags raises an Exception when the
    ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWordTags_raises_clienterror_internal_server_error(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Tests that updateWordTags raises an Exception when the InternalServerError
    error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWordTags_raises_clienterror_validation_exception(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Tests that updateWordTags raises an Exception when the ValidationException
    error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWordTags_raises_clienterror_access_denied(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Tests that updateWordTags raises an Exception when the
    AccessDeniedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "Access denied",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWordTags_raises_client_error(
    dynamodb_table, sample_word_tag, mocker
):
    """
    Simulate any error (ResourceNotFound, etc.) in transact_write_items.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(ValueError, match="Error updating word tags"):
        client.updateWordTags([sample_word_tag])
    mock_transact.assert_called_once()
