"""
Integration tests for Letter operations in DynamoDB.

This module tests the Letter-related methods of DynamoClient, including
add, get, update, delete, and list operations. It follows the perfect
test patterns established in test__receipt.py, test__image.py, and
test__word.py.
"""

from typing import Any, Dict, List

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Letter
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

CORRECT_LETTER_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 1,
    "word_id": 1,
    "letter_id": 1,
    "text": "0",
    "bounding_box": {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    },
    "top_right": {"x": 0.5323208803321982, "y": 0.930772983660083},
    "top_left": {"x": 0.44837726707985254, "y": 0.9395758561092415},
    "bottom_right": {"x": 0.5293772311516867, "y": 0.9167082877754368},
    "bottom_left": {"x": 0.4454336178993411, "y": 0.9255111602245953},
    "angle_degrees": -5.986527,
    "angle_radians": -0.1044846,
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


@pytest.fixture(name="example_letter")
def _example_letter() -> Letter:
    """Provides a sample Letter for testing."""
    return Letter(**CORRECT_LETTER_PARAMS)


@pytest.fixture(name="dynamodb_client")
def _dynamodb_client(dynamodb_table: str) -> DynamoClient:
    """Provides a DynamoClient instance."""
    return DynamoClient(dynamodb_table)


@pytest.fixture(name="batch_letters")
def _batch_letters() -> List[Letter]:
    """Provides a list of letters for batch testing."""
    letters = []
    base_params = CORRECT_LETTER_PARAMS.copy()

    # Create letters across different words and lines
    for line_id in range(1, 11):  # 10 lines
        for word_id in range(1, 11):  # 10 words per line
            for letter_id in range(1, 6):  # 5 letters per word
                letter_params = base_params.copy()
                letter_params.update(
                    {
                        "line_id": line_id,
                        "word_id": word_id,
                        "letter_id": letter_id,
                        "text": chr(ord("A") + (letter_id - 1) % 26),
                    }
                )
                letters.append(Letter(**letter_params))

    return letters


# =============================================================================
# BASIC CRUD OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestLetterBasicOperations:
    """Test basic CRUD operations for letters."""

    def test_add_letter_success(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test successful addition of a letter."""
        # Act
        dynamodb_client.add_letter(example_letter)

        # Assert - verify through get
        retrieved = dynamodb_client.get_letter(
            example_letter.image_id,
            example_letter.line_id,
            example_letter.word_id,
            example_letter.letter_id,
        )
        assert retrieved == example_letter

        # Also verify through direct DynamoDB check
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_client.table_name,
            Key=example_letter.key,
        )
        assert "Item" in response
        assert response["Item"] == example_letter.to_item()

    def test_add_letter_duplicate_raises_error(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test that adding a duplicate letter raises EntityValidationError."""
        # Arrange
        dynamodb_client.add_letter(example_letter)

        # Act & Assert
        with pytest.raises(
            EntityAlreadyExistsError, match="letter already exists"
        ):
            dynamodb_client.add_letter(example_letter)

    def test_get_letter_success(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test successful retrieval of a letter."""
        # Arrange
        dynamodb_client.add_letter(example_letter)

        # Act
        retrieved = dynamodb_client.get_letter(
            example_letter.image_id,
            example_letter.line_id,
            example_letter.word_id,
            example_letter.letter_id,
        )

        # Assert
        assert retrieved == example_letter

    def test_get_letter_not_found(self, dynamodb_client: DynamoClient) -> None:
        """Test get letter raises EntityNotFoundError when not found."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.get_letter(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 999
            )

    def test_update_letter_success(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test successful update of a letter."""
        # Arrange
        dynamodb_client.add_letter(example_letter)

        # Act - modify and update
        example_letter.text = "X"
        example_letter.confidence = 0.95
        dynamodb_client.update_letter(example_letter)

        # Assert
        retrieved = dynamodb_client.get_letter(
            example_letter.image_id,
            example_letter.line_id,
            example_letter.word_id,
            example_letter.letter_id,
        )
        assert retrieved.text == "X"
        assert retrieved.confidence == 0.95

    def test_update_letter_not_found(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test update letter raises EntityNotFoundError when not found."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.update_letter(example_letter)

    def test_delete_letter_success(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test successful deletion of a letter."""
        # Arrange
        dynamodb_client.add_letter(example_letter)

        # Act
        dynamodb_client.delete_letter(
            example_letter.image_id,
            example_letter.line_id,
            example_letter.word_id,
            example_letter.letter_id,
        )

        # Assert
        with pytest.raises(EntityNotFoundError):
            dynamodb_client.get_letter(
                example_letter.image_id,
                example_letter.line_id,
                example_letter.word_id,
                example_letter.letter_id,
            )

    def test_delete_letter_not_found(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test delete non-existent letter raises EntityNotFoundError."""
        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.delete_letter(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 999
            )


# =============================================================================
# BATCH OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestLetterBatchOperations:
    """Test batch operations for letters."""

    def test_add_letters_success(self, dynamodb_client: DynamoClient) -> None:
        """Test successful batch addition of letters."""
        # Arrange
        letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 3, "text": "2"}),
        ]

        # Act
        dynamodb_client.add_letters(letters)

        # Assert
        for letter in letters:
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved == letter

    def test_add_letters_large_batch(
        self, dynamodb_client: DynamoClient, batch_letters: List[Letter]
    ) -> None:
        """Test adding a large batch of letters (500 items)."""
        # Act
        dynamodb_client.add_letters(batch_letters)

        # Assert - spot check a few
        for i in [0, 250, 499]:
            letter = batch_letters[i]
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved == letter

    def test_delete_letters_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful batch deletion of letters."""
        # Arrange
        letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
        ]
        dynamodb_client.add_letters(letters)

        # Act
        dynamodb_client.delete_letters(letters)

        # Assert
        for letter in letters:
            with pytest.raises(EntityNotFoundError):
                dynamodb_client.get_letter(
                    letter.image_id,
                    letter.line_id,
                    letter.word_id,
                    letter.letter_id,
                )

    def test_delete_letters_from_word(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test deleting all letters from a specific word."""
        # Arrange - add letters to multiple words
        word1_letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
        ]
        word2_letters = [
            Letter(
                **{
                    **CORRECT_LETTER_PARAMS,
                    "word_id": 2,
                    "letter_id": 1,
                    "text": "A",
                }
            ),
            Letter(
                **{
                    **CORRECT_LETTER_PARAMS,
                    "word_id": 2,
                    "letter_id": 2,
                    "text": "B",
                }
            ),
        ]

        dynamodb_client.add_letters(word1_letters + word2_letters)

        # Act - delete only word 1 letters
        dynamodb_client.delete_letters_from_word(
            CORRECT_LETTER_PARAMS["image_id"], 1, 1
        )

        # Assert - word 1 letters deleted, word 2 letters remain
        for letter in word1_letters:
            with pytest.raises(EntityNotFoundError):
                dynamodb_client.get_letter(
                    letter.image_id,
                    letter.line_id,
                    letter.word_id,
                    letter.letter_id,
                )

        for letter in word2_letters:
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved == letter

    def test_update_letters_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful batch update of letters."""
        # Arrange
        letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
        ]
        dynamodb_client.add_letters(letters)

        # Act - modify and update
        letters[0].text = "X"
        letters[1].text = "Y"
        dynamodb_client.update_letters(letters)

        # Assert
        for letter in letters:
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved.text == letter.text


# =============================================================================
# ADVANCED OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestLetterAdvancedOperations:
    """Test advanced letter operations."""

    def test_get_letters_by_keys_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test successful retrieval of letters by keys."""
        # Arrange
        letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
        ]
        dynamodb_client.add_letters(letters)

        # Act
        keys = [letter.key for letter in letters]
        retrieved_letters = dynamodb_client.get_letters(keys)

        # Assert
        assert len(retrieved_letters) == 2
        assert set(l.letter_id for l in retrieved_letters) == {1, 2}

    def test_get_letters_invalid_keys(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that get_letters validates key structure."""
        # Test missing PK
        with pytest.raises(
            EntityValidationError, match="Keys must contain 'PK' and 'SK'"
        ):
            dynamodb_client.get_letters(
                [{"SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"}}]
            )

        # Test wrong PK prefix
        with pytest.raises(
            EntityValidationError, match="PK must start with 'IMAGE#'"
        ):
            dynamodb_client.get_letters(
                [
                    {
                        "PK": {"S": "FOO#00001"},
                        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
                    }
                ]
            )

        # Test SK missing LETTER
        with pytest.raises(
            EntityValidationError, match="SK must contain 'LETTER'"
        ):
            dynamodb_client.get_letters(
                [
                    {
                        "PK": {"S": "IMAGE#00001"},
                        "SK": {"S": "LINE#00001#WORD#00001#FOO#00001"},
                    }
                ]
            )


# =============================================================================
# LIST AND QUERY OPERATIONS
# =============================================================================


@pytest.mark.integration
class TestLetterListOperations:
    """Test list and query operations for letters."""

    def test_list_letters_empty(self, dynamodb_client: DynamoClient) -> None:
        """Test listing letters when table is empty."""
        letters, last_key = dynamodb_client.list_letters()
        assert letters == []
        assert last_key is None

    def test_list_letters_success(self, dynamodb_client: DynamoClient) -> None:
        """Test listing all letters."""
        # Arrange
        letters = [
            Letter(**CORRECT_LETTER_PARAMS),
            Letter(**{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}),
        ]
        dynamodb_client.add_letters(letters)

        # Act
        retrieved_letters, last_key = dynamodb_client.list_letters()

        # Assert
        assert len(retrieved_letters) == 2
        assert set(l.letter_id for l in retrieved_letters) == {1, 2}
        assert last_key is None

    def test_list_letters_with_pagination(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing letters with pagination."""
        # Arrange - add 10 letters
        letters = []
        for i in range(10):
            letter_params = CORRECT_LETTER_PARAMS.copy()
            letter_params["letter_id"] = i + 1
            letter_params["text"] = str(i)
            letters.append(Letter(**letter_params))
        dynamodb_client.add_letters(letters)

        # Act - get first page
        page1, last_key1 = dynamodb_client.list_letters(limit=5)
        assert len(page1) == 5
        assert last_key1 is not None

        # Act - get second page
        page2, last_key2 = dynamodb_client.list_letters(
            limit=5, last_evaluated_key=last_key1
        )
        assert len(page2) == 5
        assert last_key2 is None

        # Verify all letters retrieved
        all_retrieved = page1 + page2
        assert len(all_retrieved) == 10

    def test_list_letters_from_word_success(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing letters from a specific word."""
        # Arrange - add letters to different words
        word1_letter1 = Letter(**CORRECT_LETTER_PARAMS)
        word1_letter2 = Letter(
            **{**CORRECT_LETTER_PARAMS, "letter_id": 2, "text": "1"}
        )
        word2_letter = Letter(
            **{
                **CORRECT_LETTER_PARAMS,
                "word_id": 2,
                "letter_id": 1,
                "text": "A",
            }
        )

        dynamodb_client.add_letters(
            [word1_letter1, word1_letter2, word2_letter]
        )

        # Act
        word1_letters = dynamodb_client.list_letters_from_word(
            CORRECT_LETTER_PARAMS["image_id"], 1, 1
        )

        # Assert
        assert len(word1_letters) == 2
        assert word1_letter1 in word1_letters
        assert word1_letter2 in word1_letters
        assert word2_letter not in word1_letters

    def test_list_letters_from_word_empty(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test listing letters from a word with no letters."""
        letters = dynamodb_client.list_letters_from_word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1
        )
        assert letters == []


# =============================================================================
# VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
class TestLetterValidation:
    """Test validation for letter operations."""

    def test_add_letter_none_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding None raises OperationError."""
        with pytest.raises(OperationError, match="letter cannot be None"):
            dynamodb_client.add_letter(None)  # type: ignore

    def test_add_letter_wrong_type_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding wrong type raises OperationError."""
        with pytest.raises(
            OperationError, match="letter must be an instance of Letter"
        ):
            dynamodb_client.add_letter("not-a-letter")  # type: ignore

    def test_add_letters_none_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding None list raises OperationError."""
        with pytest.raises(OperationError, match="letters cannot be None"):
            dynamodb_client.add_letters(None)  # type: ignore

    def test_add_letters_not_list_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that adding non-list raises OperationError."""
        with pytest.raises(OperationError, match="letters must be a list"):
            dynamodb_client.add_letters("not-a-list")  # type: ignore

    def test_add_letters_wrong_item_type_raises_error(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test that adding list with wrong item type raises OperationError."""
        with pytest.raises(
            OperationError,
            match="letters must be a list of Letter instances",
        ):
            dynamodb_client.add_letters(
                [example_letter, "not-a-letter"]  # type: ignore
            )

    def test_get_letter_invalid_uuid_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that invalid UUID raises OperationError."""
        with pytest.raises(
            OperationError, match="uuid must be a valid UUIDv4"
        ):
            dynamodb_client.get_letter("invalid-uuid", 1, 1, 1)

    def test_delete_letter_invalid_uuid_raises_error(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test that delete with invalid UUID raises OperationError."""
        with pytest.raises(
            OperationError, match="uuid must be a valid UUIDv4"
        ):
            dynamodb_client.delete_letter("invalid-uuid", 1, 1, 1)

    def test_update_letter_validation_errors(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test update_letter validation error handling."""
        # None parameter
        with pytest.raises(OperationError, match="letter cannot be None"):
            dynamodb_client.update_letter(None)  # type: ignore

        # Wrong type parameter
        with pytest.raises(
            OperationError, match="letter must be an instance of Letter"
        ):
            dynamodb_client.update_letter("not-a-letter")  # type: ignore

    def test_update_letters_validation_errors(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test update_letters validation error handling."""
        # None parameter
        with pytest.raises(OperationError, match="letters cannot be None"):
            dynamodb_client.update_letters(None)  # type: ignore

        # Non-list parameter
        with pytest.raises(OperationError, match="letters must be a list"):
            dynamodb_client.update_letters("not-a-list")  # type: ignore

        # Wrong item types
        with pytest.raises(
            OperationError, match="letters must be a list of Letter instances"
        ):
            dynamodb_client.update_letters(
                [example_letter, "not-a-letter"]  # type: ignore
            )


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================


@pytest.mark.integration
class TestLetterErrorHandling:
    """Test error handling for letter operations."""

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_add_letter_error_handling(  # pylint: disable=too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        example_letter: Letter,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for add_letter operation."""
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
            dynamodb_client.add_letter(example_letter)

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_get_letter_error_handling(  # pylint: disable=too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for get_letter operation."""
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
            dynamodb_client.get_letter(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1
            )

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_delete_letter_error_handling(  # pylint: disable=too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for delete_letter operation."""
        # Mock the delete_item to raise specific error
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "delete_item",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": f"Mocked {error_code}",
                    }
                },
                "DeleteItem",
            ),
        )

        # Act & Assert
        with pytest.raises(expected_exception, match=expected_message):
            dynamodb_client.delete_letter(
                "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1
            )

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_update_letter_error_handling(  # pylint: disable=too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        example_letter: Letter,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for update_letter operation."""
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
            dynamodb_client.update_letter(example_letter)

    def test_update_letter_conditional_check_failed(
        self, dynamodb_client: DynamoClient, example_letter: Letter, mocker
    ) -> None:
        """Test update_letter when letter doesn't exist."""
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "put_item",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The letter does not exist",
                    }
                },
                "PutItem",
            ),
        )

        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.update_letter(example_letter)

    @pytest.mark.parametrize(
        "error_code,expected_exception,expected_message",
        ERROR_SCENARIOS,
    )
    def test_update_letters_error_handling(  # pylint: disable=too-many-positional-arguments
        self,
        dynamodb_client: DynamoClient,
        example_letter: Letter,
        error_code: str,
        expected_exception: type,
        expected_message: str,
        mocker,
    ) -> None:
        """Test error handling for update_letters operation."""
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
            dynamodb_client.update_letters([example_letter])

    def test_update_letters_conditional_check_failed(
        self, dynamodb_client: DynamoClient, example_letter: Letter, mocker
    ) -> None:
        """Test update_letters when letter doesn't exist."""
        mocker.patch.object(
            dynamodb_client._client,  # pylint: disable=protected-access
            "transact_write_items",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "One or more letters do not exist",
                    }
                },
                "TransactWriteItems",
            ),
        )

        with pytest.raises(EntityNotFoundError, match="not found"):
            dynamodb_client.update_letters([example_letter])


# =============================================================================
# SPECIAL CASES AND EDGE CASES
# =============================================================================


@pytest.mark.integration
class TestLetterSpecialCases:
    """Test special cases and edge cases for letter operations."""

    def test_letter_with_special_characters(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test handling letters with special characters."""
        # Arrange
        special_chars = ["@", "#", "$", "%", "&", "*", "(", ")", "!", "?"]
        letters = []

        for i, char in enumerate(special_chars):
            letter_params = CORRECT_LETTER_PARAMS.copy()
            letter_params.update({"letter_id": i + 1, "text": char})
            letters.append(Letter(**letter_params))

        # Act
        dynamodb_client.add_letters(letters)

        # Assert
        retrieved, _ = dynamodb_client.list_letters()
        assert len(retrieved) == len(special_chars)
        retrieved_chars = {l.text for l in retrieved}
        assert retrieved_chars == set(special_chars)

    def test_letter_with_unicode_text(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test handling letters with unicode characters."""
        # Arrange
        unicode_chars = ["α", "β", "γ", "中", "文", "😀", "🚀"]
        letters = []

        for i, char in enumerate(unicode_chars):
            letter_params = CORRECT_LETTER_PARAMS.copy()
            letter_params.update({"letter_id": i + 1, "text": char})
            letters.append(Letter(**letter_params))

        # Act
        dynamodb_client.add_letters(letters)

        # Assert
        for letter in letters:
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved.text == letter.text

    def test_letter_boundary_values(
        self, dynamodb_client: DynamoClient
    ) -> None:
        """Test letters with boundary values for numeric fields."""
        # Test with very small confidence (must be > 0)
        letter_params = CORRECT_LETTER_PARAMS.copy()
        letter_params["confidence"] = 0.01
        letter1 = Letter(**letter_params)

        # Test with maximum confidence
        letter_params["letter_id"] = 2
        letter_params["confidence"] = 1.0
        letter2 = Letter(**letter_params)

        # Test with large IDs
        letter_params["letter_id"] = 99999
        letter_params["line_id"] = 99999
        letter_params["word_id"] = 99999
        letter3 = Letter(**letter_params)

        # Act
        dynamodb_client.add_letters([letter1, letter2, letter3])

        # Assert
        for letter in [letter1, letter2, letter3]:
            retrieved = dynamodb_client.get_letter(
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )
            assert retrieved == letter

    def test_concurrent_letter_operations(
        self, dynamodb_client: DynamoClient, example_letter: Letter
    ) -> None:
        """Test that concurrent operations are handled correctly."""
        # This tests the conditional checks in DynamoDB
        # Add the letter
        dynamodb_client.add_letter(example_letter)

        # Try to add again (should fail)
        with pytest.raises(EntityAlreadyExistsError):
            dynamodb_client.add_letter(example_letter)

        # Update should succeed
        example_letter.text = "Z"
        dynamodb_client.update_letter(example_letter)

        # Delete should succeed
        dynamodb_client.delete_letter(
            example_letter.image_id,
            example_letter.line_id,
            example_letter.word_id,
            example_letter.letter_id,
        )

        # Update after delete should fail
        with pytest.raises(EntityNotFoundError):
            dynamodb_client.update_letter(example_letter)
