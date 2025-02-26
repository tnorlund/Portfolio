from typing import Literal
import pytest
import boto3
from receipt_dynamo import Word, DynamoClient
from botocore.exceptions import ClientError

correct_word_params = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 2,
    "word_id": 3,
    "text": "test_string",
    "bounding_box": {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    },
    "top_right": {"y": 0.9307722198001792, "x": 0.5323281614683008},
    "top_left": {"x": 0.44837726658954413, "y": 0.9395758560096301},
    "bottom_right": {"y": 0.9167082878750482, "x": 0.529377231641995},
    "bottom_left": {"x": 0.4454263367632384, "y": 0.9255119240844992},
    "angle_degrees": -5.986527,
    "angle_radians": -0.10448461,
    "confidence": 1,
}


@pytest.mark.integration
def test_word_add_no_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)

    # Act
    client.addWord(word)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word.to_item()


@pytest.mark.integration
def test_word_add_with_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params, tags=["tag1", "tag2"])

    # Act
    client.addWord(word)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word.to_item()


@pytest.mark.integration
def test_word_add_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)

    # Act
    client.addWord(word)
    with pytest.raises(ValueError):
        client.addWord(word)


@pytest.mark.integration
def test_word_add_all(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2_params = correct_word_params.copy()
    word2_params["word_id"] = 4
    word2 = Word(**word2_params)

    # Act
    client.addWords([word1, word2])

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word1.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word1.to_item()

    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word2.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word2.to_item()


@pytest.mark.integration
def test_word_delete(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    client.deleteWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)

    # Assert
    with pytest.raises(ValueError):
        client.getWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)


@pytest.mark.integration
def test_word_delete_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.deleteWord(1, 2, 3)


@pytest.mark.integration
def test_word_delete_from_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2_params = correct_word_params.copy()
    word2_params["word_id"] = 4
    word2 = Word(**word2_params)
    client.addWord(word1)
    client.addWord(word2)

    # Act
    client.deleteWordsFromLine(1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.getWord(1, 1, 1)
    with pytest.raises(ValueError):
        client.getWord(1, 1, 2)


@pytest.mark.integration
def test_word_get(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    retrieved_word = client.getWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)

    # Assert
    assert retrieved_word == word


@pytest.mark.integration
def test_word_get_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.getWord(1, 2, 3)


@pytest.mark.integration
def test_word_get_all(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "word_id": 4}),
    ]
    client.addWords(words)

    # Act
    words_retrieved = client.getWords([words[0].key(), words[1].key()])

    # Assert
    assert words_retrieved == words


@pytest.mark.integration
def test_word_get_invalid_keys(dynamodb_table: Literal["MyMockedTable"]):
    """
    Shows how to test for invalid keys. We expect ValueError when PK or SK is invalid.
    """
    client = DynamoClient(dynamodb_table)

    # A key missing 'PK'
    bad_keys_missing_pk = [{"SK": {"S": "LINE#00002#WORD#00003"}}]
    with pytest.raises(ValueError, match="Keys must contain 'PK' and 'SK'"):
        client.getWords(bad_keys_missing_pk)

    # A key with PK not starting with 'IMAGE#'
    bad_keys_wrong_prefix = [
        {
            "PK": {"S": "FOO#00001"},
            "SK": {"S": "LINE#00002#WORD#00003"},
        }
    ]
    with pytest.raises(ValueError, match="PK must start with 'IMAGE#'"):
        client.getWords(bad_keys_wrong_prefix)

    # A key with SK missing 'WORD'
    bad_keys_no_word = [
        {
            "PK": {"S": "IMAGE#00001"},
            "SK": {"S": "LINE#00002#FOO#00003"},
        }
    ]
    with pytest.raises(ValueError, match="SK must contain 'WORD'"):
        client.getWords(bad_keys_no_word)


@pytest.mark.integration
def test_word_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "word_id": 4}),
    ]
    client.addWords(words)

    # Act
    words_retrieved, _ = client.listWords()

    # Assert
    assert words_retrieved == words


@pytest.mark.integration
def test_word_list_from_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "word_id": 1}),
        Word(**{**correct_word_params, "word_id": 2}),
    ]
    client.addWords(words)
    # sort words by id
    words = sorted(words, key=lambda x: x.word_id)

    # Act
    response = client.listWordsFromLine("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2)

    # Assert
    assert words == response


@pytest.mark.integration
def test_updateWords_success(dynamodb_table):
    """
    Tests happy path for updateWords.
    """
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2 = Word(**{**correct_word_params, "word_id": 4})
    client.addWords([word1, word2])

    # Now update them
    word1.text = "updated_text_1"
    word2.text = "updated_text_2"
    client.updateWords([word1, word2])

    # Verify updates
    retrieved_words = client.getWords([word1.key(), word2.key()])
    assert len(retrieved_words) == 2
    for word in retrieved_words:
        if word.word_id == word1.word_id:
            assert word.text == "updated_text_1"
        else:
            assert word.text == "updated_text_2"


@pytest.mark.integration
def test_updateWords_with_tags_success(dynamodb_table):
    """
    Tests updating words with tags.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params, tags=["tag1"])
    client.addWord(word)

    # Update with new tags
    word.tags = ["tag2", "tag3"]
    client.updateWords([word])

    # Verify update
    retrieved_word = client.getWord(word.image_id, word.line_id, word.word_id)
    assert retrieved_word.tags == ["tag2", "tag3"]


@pytest.mark.integration
def test_updateWords_raises_value_error_words_none(dynamodb_table):
    """
    Tests that updateWords raises ValueError when the words parameter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="Words parameter is required and cannot be None."
    ):
        client.updateWords(None)  # type: ignore


@pytest.mark.integration
def test_updateWords_raises_value_error_words_not_list(dynamodb_table):
    """
    Tests that updateWords raises ValueError when the words parameter is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Words must be provided as a list."):
        client.updateWords("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateWords_raises_value_error_words_not_list_of_words(dynamodb_table):
    """
    Tests that updateWords raises ValueError when the words parameter is not a list of Word instances.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    with pytest.raises(
        ValueError,
        match="All items in the words list must be instances of the Word class.",
    ):
        client.updateWords([word, "not-a-word"])  # type: ignore


@pytest.mark.integration
def test_updateWords_raises_value_error_duplicate_tags(dynamodb_table):
    """
    Tests that updateWords raises ValueError when a word has duplicate tags.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params, tags=["tag1", "tag1"])
    with pytest.raises(ValueError, match="Word tags must be unique"):
        client.updateWords([word])


@pytest.mark.integration
def test_updateWords_raises_clienterror_conditional_check_failed(
    dynamodb_table, mocker
):
    """
    Tests that updateWords raises ValueError when trying to update non-existent words.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "One or more words do not exist",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(ValueError, match="One or more words do not exist"):
        client.updateWords([word])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWords_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, mocker
):
    """
    Tests that updateWords raises an Exception when the ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
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
        client.updateWords([word])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWords_raises_clienterror_internal_server_error(dynamodb_table, mocker):
    """
    Tests that updateWords raises an Exception when the InternalServerError error is raised.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
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
        client.updateWords([word])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWords_raises_clienterror_validation_exception(dynamodb_table, mocker):
    """
    Tests that updateWords raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
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
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.updateWords([word])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWords_raises_clienterror_access_denied(dynamodb_table, mocker):
    """
    Tests that updateWords raises an Exception when the AccessDeniedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.updateWords([word])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateWords_raises_client_error(dynamodb_table, mocker):
    """
    Simulate any error (ResourceNotFound, etc.) in transact_write_items.
    """
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
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
    with pytest.raises(ValueError, match="Error updating words"):
        client.updateWords([word])
    mock_transact.assert_called_once()
