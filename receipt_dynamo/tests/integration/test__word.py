"""
Integration tests for Word operations in DynamoDB.

This module tests the Word-related methods of DynamoClient, including
add, get, update, delete, and list operations. It follows the perfect
test patterns established in test__receipt.py and test__image.py.
"""

from typing import Any, Dict, List

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Word
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# =============================================================================
# TEST DATA AND FIXTURES
# =============================================================================

CORRECT_WORD_PARAMS: Dict[str, Any] = {
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

ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error"),
    ("UnknownException", DynamoDBError, "DynamoDB error"),
]


@pytest.fixture(name="example_word")
def _example_word() -> Word:
    """Provides a sample Word for testing."""
    return Word(**CORRECT_WORD_PARAMS)


@pytest.fixture(name="dynamodb_client")
def _dynamodb_client(dynamodb_table: str) -> DynamoClient:
    """Provides a DynamoClient instance."""
    return DynamoClient(dynamodb_table)


@pytest.fixture(name="batch_words")
def _batch_words() -> List[Word]:
    """Provides a list of words for batch testing."""
    words = []
    base_params = CORRECT_WORD_PARAMS.copy()

    # Create words across different lines
    for line_id in range(1, 11):  # 10 lines
        for word_id in range(1, 21):  # 20 words per line
            word_params = base_params.copy()
            word_params.update(
                {
                    "line_id": line_id,
                    "word_id": word_id,
                    "text": f"word_{line_id}_{word_id}",
                }
            )
            words.append(Word(**word_params))

    return words


# =============================================================================
# BASIC CRUD OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestWordBasicOperations:
    """Test basic CRUD operations for words."""

    def test_add_word_success(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test successful addition of a word."""
        # Act
        dynamodb_client.add_word(example_word)

        # Assert - verify through get
        retrieved = dynamodb_client.get_word(
            example_word.image_id,
            example_word.line_id,
            example_word.word_id,
        )
        assert retrieved == example_word

        # Also verify through direct DynamoDB check
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_client.table_name,
            Key=example_word.key,
        )
        assert "Item" in response
        assert response["Item"] == example_word.to_item()

    def test_add_word_duplicate_raises_error(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test that adding a duplicate word raises EntityAlreadyExistsError."""
        # Arrange
        dynamodb_client.add_word(example_word)

        # Act & Assert
        with pytest.raises(
            EntityAlreadyExistsError, match="word already exists"
        ):
            dynamodb_client.add_word(example_word)

    def test_get_word_success(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test successful retrieval of a word."""
        # Arrange
        dynamodb_client.add_word(example_word)

        # Act
        retrieved = dynamodb_client.get_word(
            example_word.image_id,
            example_word.line_id,
            example_word.word_id,
        )

        # Assert
        assert retrieved == example_word

    def test_get_word_not_found(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test get word raises EntityNotFoundError when not found."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.get_word(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 999
            )

    def test_update_word_success(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test successful update of a word."""
        # Arrange
        dynamodb_client.add_word(example_word)

        # Act - modify and update
        example_word.text = "updated_text"
        example_word.confidence = 0.95
        dynamodb_client.update_word(example_word)

        # Assert
        retrieved = dynamodb_client.get_word(
            example_word.image_id,
            example_word.line_id,
            example_word.word_id,
        )
        assert retrieved.text == "updated_text"
        assert retrieved.confidence == 0.95

    def test_update_word_not_found(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test update word raises EntityNotFoundError when not found."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.update_word(example_word)

    def test_delete_word_success(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test successful deletion of a word."""
        # Arrange
        dynamodb_client.add_word(example_word)

        # Act
        dynamodb_client.delete_word(
            example_word.image_id,
            example_word.line_id,
            example_word.word_id,
        )

        # Assert
        with pytest.raises(EntityNotFoundError):
            dynamodb_client.get_word(
                example_word.image_id,
                example_word.line_id,
                example_word.word_id,
            )

    def test_delete_word_not_found(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test delete non-existent word raises EntityNotFoundError."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.delete_word(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 999
            )


# =============================================================================
# BATCH OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestWordBatchOperations:
    """Test batch operations for words."""

    def test_add_words_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful batch addition of words."""
        # Arrange
        words = [
            Word(**CORRECT_WORD_PARAMS),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 4, "text": "word2"}),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 5, "text": "word3"}),
        ]

        # Act
        dynamodb_client.add_words(words)

        # Assert
        for word in words:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved == word

    def test_add_words_large_batch(
        self, dynamodb_client: DynamoClient, batch_words: List[Word]
    ) -> None:
        """Test adding a large batch of words (200 items)."""
        # Act
        dynamodb_client.add_words(batch_words)

        # Assert - spot check a few
        for i in [0, 100, 199]:
            word = batch_words[i]
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved == word

    def test_update_words_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful batch update of words."""
        # Arrange
        words = [
            Word(**CORRECT_WORD_PARAMS),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 4, "text": "word2"}),
        ]
        dynamodb_client.add_words(words)

        # Act - modify and update
        words[0].text = "updated_text_1"
        words[1].text = "updated_text_2"
        dynamodb_client.update_words(words)

        # Assert
        for word in words:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved.text == word.text

    def test_delete_words_from_line(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test deleting all words from a specific line."""
        # Arrange - add words to multiple lines
        line1_words = [
            Word(**{**CORRECT_WORD_PARAMS, "line_id": 1, "word_id": 1}),
            Word(**{**CORRECT_WORD_PARAMS, "line_id": 1, "word_id": 2}),
        ]
        line2_words = [
            Word(**{**CORRECT_WORD_PARAMS, "line_id": 2, "word_id": 1}),
            Word(**{**CORRECT_WORD_PARAMS, "line_id": 2, "word_id": 2}),
        ]

        dynamodb_client.add_words(line1_words + line2_words)

        # Act - delete only line 1 words
        dynamodb_client.delete_words_from_line(
            CORRECT_WORD_PARAMS["image_id"], 1
        )

        # Assert - line 1 words deleted, line 2 words remain
        for word in line1_words:
            with pytest.raises(EntityNotFoundError):
                dynamodb_client.get_word(
                    word.image_id, word.line_id, word.word_id
                )

        for word in line2_words:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved == word


# =============================================================================
# ADVANCED OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestWordAdvancedOperations:
    """Test advanced word operations."""

    def test_get_words_by_keys_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful retrieval of words by keys."""
        # Arrange
        words = [
            Word(**CORRECT_WORD_PARAMS),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 4, "text": "word2"}),
        ]
        dynamodb_client.add_words(words)

        # Act
        keys = [word.key for word in words]
        retrieved_words = dynamodb_client.get_words(keys)

        # Assert
        assert len(retrieved_words) == 2
        assert set(w.word_id for w in retrieved_words) == {3, 4}

    def test_get_words_invalid_keys(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that get_words validates key structure."""
        # Test missing PK
        with pytest.raises(ValueError, match="Keys must contain 'PK' and 'SK'"):
            dynamodb_client.get_words([{"SK": {"S": "LINE#00002#WORD#00003"}}])

        # Test wrong PK prefix
        with pytest.raises(ValueError, match="PK must start with 'IMAGE#'"):
            dynamodb_client.get_words([
                {
                    "PK": {"S": "FOO#00001"},
                    "SK": {"S": "LINE#00002#WORD#00003"},
                }
            ])

        # Test SK missing WORD
        with pytest.raises(ValueError, match="SK must contain 'WORD'"):
            dynamodb_client.get_words([
                {
                    "PK": {"S": "IMAGE#00001"},
                    "SK": {"S": "LINE#00002#FOO#00003"},
                }
            ])


# =============================================================================
# LIST AND QUERY OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestWordListOperations:
    """Test list and query operations for words."""

    def test_list_words_empty(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing words when table is empty."""
        words, last_key = dynamodb_client.list_words()
        assert words == []
        assert last_key is None

    def test_list_words_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing all words."""
        # Arrange
        words = [
            Word(**CORRECT_WORD_PARAMS),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 4, "text": "word2"}),
        ]
        dynamodb_client.add_words(words)

        # Act
        retrieved_words, last_key = dynamodb_client.list_words()

        # Assert
        assert len(retrieved_words) == 2
        assert set(w.word_id for w in retrieved_words) == {3, 4}
        assert last_key is None

    def test_list_words_with_pagination(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing words with pagination."""
        # Arrange - add 10 words
        words = []
        for i in range(10):
            word_params = CORRECT_WORD_PARAMS.copy()
            word_params["word_id"] = i + 1
            word_params["text"] = f"word_{i}"
            words.append(Word(**word_params))
        dynamodb_client.add_words(words)

        # Act - get first page
        page1, last_key1 = dynamodb_client.list_words(limit=5)
        assert len(page1) == 5
        assert last_key1 is not None

        # Act - get second page
        page2, last_key2 = dynamodb_client.list_words(
            limit=5, last_evaluated_key=last_key1
        )
        assert len(page2) == 5
        assert last_key2 is None

        # Verify all words retrieved
        all_retrieved = page1 + page2
        assert len(all_retrieved) == 10

    def test_list_words_from_line_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing words from a specific line."""
        # Arrange - add words to different lines
        line2_words = [
            Word(**CORRECT_WORD_PARAMS),
            Word(**{**CORRECT_WORD_PARAMS, "word_id": 4, "text": "word2"}),
        ]
        line3_word = Word(
            **{**CORRECT_WORD_PARAMS, "line_id": 3, "word_id": 1, "text": "word3"}
        )

        dynamodb_client.add_words(line2_words + [line3_word])

        # Act
        retrieved_words = dynamodb_client.list_words_from_line(
            CORRECT_WORD_PARAMS["image_id"], 2
        )

        # Assert
        assert len(retrieved_words) == 2
        # Sort by word_id for consistent comparison
        retrieved_words.sort(key=lambda x: x.word_id)
        line2_words.sort(key=lambda x: x.word_id)
        assert retrieved_words == line2_words

    def test_list_words_from_line_empty(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing words from a line with no words."""
        words = dynamodb_client.list_words_from_line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 999
        )
        assert words == []


# =============================================================================
# VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
class TestWordValidation:
    """Test validation for word operations."""

    def test_add_word_none_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding None raises OperationError."""
        with pytest.raises(OperationError, match="word cannot be None"):
            dynamodb_client.add_word(None)  # type: ignore

    def test_add_word_wrong_type_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding wrong type raises OperationError."""
        with pytest.raises(
            OperationError, match="word must be an instance of Word"
        ):
            dynamodb_client.add_word("not-a-word")  # type: ignore

    def test_add_words_none_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding None list raises OperationError."""
        with pytest.raises(OperationError, match="words cannot be None"):
            dynamodb_client.add_words(None)  # type: ignore

    def test_add_words_not_list_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding non-list raises OperationError."""
        with pytest.raises(OperationError, match="words must be a list"):
            dynamodb_client.add_words("not-a-list")  # type: ignore

    def test_add_words_wrong_item_type_raises_error(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test that adding list with wrong item type raises OperationError."""
        with pytest.raises(
            OperationError,
            match="words must be a list of Word instances",
        ):
            dynamodb_client.add_words([example_word, "not-a-word"])  # type: ignore

    def test_get_word_invalid_uuid_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that invalid UUID raises OperationError."""
        with pytest.raises(
            OperationError, match="uuid must be a valid UUIDv4"
        ):
            dynamodb_client.get_word("invalid-uuid", 1, 1)

    def test_delete_word_invalid_uuid_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that delete with invalid UUID raises OperationError."""
        with pytest.raises(
            OperationError, match="uuid must be a valid UUIDv4"
        ):
            dynamodb_client.delete_word("invalid-uuid", 1, 1)

    def test_update_words_validation_errors(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test update_words validation error handling."""
        # None parameter
        with pytest.raises(OperationError, match="words cannot be None"):
            dynamodb_client.update_words(None)  # type: ignore

        # Non-list parameter
        with pytest.raises(OperationError, match="words must be a list"):
            dynamodb_client.update_words("not-a-list")  # type: ignore

        # Wrong item types
        with pytest.raises(
            OperationError, match="words must be a list of Word instances"
        ):
            dynamodb_client.update_words([example_word, "not-a-word"])  # type: ignore


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================


@pytest.mark.integration
class TestWordErrorHandling:
    """Test error handling for word operations."""

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_add_word_error_handling(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        example_word: Word,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for add_word operation."""
        # Mock the put_item to raise specific error
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "put_item",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": f"Mocked {error_code}",
                    }
                },
                "PutItem",
            ),
        )

        # Act & Assert
        with pytest.raises(expected_exception, match=expected_message):
            dynamodb_client.add_word(example_word)

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_get_word_error_handling(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for get_word operation."""
        # Mock the get_item to raise specific error
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "get_item",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": f"Mocked {error_code}",
                    }
                },
                "GetItem",
            ),
        )

        # Act & Assert
        with pytest.raises(expected_exception, match=expected_message):
            dynamodb_client.get_word(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1
            )

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_update_words_error_handling(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        example_word: Word,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for update_words operation."""
        # Mock the transact_write_items to raise specific error
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "transact_write_items",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": f"Mocked {error_code}",
                    }
                },
                "TransactWriteItems",
            ),
        )

        # Act & Assert
        with pytest.raises(expected_exception, match=expected_message):
            dynamodb_client.update_words([example_word])

    def test_update_words_conditional_check_failed(
        self, dynamodb_client: DynamoClient, example_word: Word, mocker
    ) -> None:
        """Test update_words when word doesn't exist."""
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
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

        with pytest.raises(EntityNotFoundError, match="one or more words not found"):
            dynamodb_client.update_words([example_word])


# =============================================================================
# SPECIAL CASES AND EDGE CASES
# =============================================================================


@pytest.mark.integration
class TestWordSpecialCases:
    """Test special cases and edge cases for word operations."""

    def test_word_with_special_characters(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test handling words with special characters."""
        # Arrange
        special_texts = ["@word", "#hashtag", "$money", "word!", "word?", "word&more"]
        words = []

        for i, text in enumerate(special_texts):
            word_params = CORRECT_WORD_PARAMS.copy()
            word_params.update({"word_id": i + 1, "text": text})
            words.append(Word(**word_params))

        # Act
        dynamodb_client.add_words(words)

        # Assert
        retrieved, _ = dynamodb_client.list_words()
        assert len(retrieved) == len(special_texts)
        retrieved_texts = {w.text for w in retrieved}
        assert retrieved_texts == set(special_texts)

    def test_word_with_unicode_text(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test handling words with unicode characters."""
        # Arrange
        unicode_texts = ["café", "naïve", "Zürich", "北京", "🌟", "αβγ"]
        words = []

        for i, text in enumerate(unicode_texts):
            word_params = CORRECT_WORD_PARAMS.copy()
            word_params.update({"word_id": i + 1, "text": text})
            words.append(Word(**word_params))

        # Act
        dynamodb_client.add_words(words)

        # Assert
        for word in words:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved.text == word.text

    def test_word_boundary_values(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test words with boundary values for numeric fields."""
        # Test with very small confidence
        word_params = CORRECT_WORD_PARAMS.copy()
        word_params["confidence"] = 0.01
        word1 = Word(**word_params)

        # Test with maximum confidence
        word_params["word_id"] = 2
        word_params["confidence"] = 1.0
        word2 = Word(**word_params)

        # Test with large IDs
        word_params["word_id"] = 99999
        word_params["line_id"] = 99999
        word3 = Word(**word_params)

        # Act
        dynamodb_client.add_words([word1, word2, word3])

        # Assert
        for word in [word1, word2, word3]:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved == word

    def test_concurrent_word_operations(
        self, dynamodb_client: DynamoClient, example_word: Word
    ) -> None:
        """Test that concurrent operations are handled correctly."""
        # This tests the conditional checks in DynamoDB
        # Add the word
        dynamodb_client.add_word(example_word)

        # Try to add again (should fail)
        with pytest.raises(EntityAlreadyExistsError):
            dynamodb_client.add_word(example_word)

        # Update should succeed
        example_word.text = "updated_concurrent"
        dynamodb_client.update_word(example_word)

        # Delete should succeed
        dynamodb_client.delete_word(
            example_word.image_id,
            example_word.line_id,
            example_word.word_id,
        )

        # Update after delete should fail
        with pytest.raises(EntityNotFoundError):
            dynamodb_client.update_word(example_word)

    def test_empty_and_long_text_values(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test words with empty and very long text values."""
        # Empty text (if allowed by entity validation)
        word_params = CORRECT_WORD_PARAMS.copy()
        word_params["text"] = ""
        word_params["word_id"] = 1
        word1 = Word(**word_params)

        # Very long text
        word_params["text"] = "a" * 1000  # 1000 character word
        word_params["word_id"] = 2
        word2 = Word(**word_params)

        # Act
        dynamodb_client.add_words([word1, word2])

        # Assert
        for word in [word1, word2]:
            retrieved = dynamodb_client.get_word(
                word.image_id, word.line_id, word.word_id
            )
            assert retrieved.text == word.text
