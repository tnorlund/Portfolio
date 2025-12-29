# infra/lambda_layer/python/dynamo/data/_receipt_letter.py
from typing import TYPE_CHECKING, Optional

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities import item_to_receipt_letter
from receipt_dynamo.entities.receipt_letter import ReceiptLetter

if TYPE_CHECKING:
    pass


class _ReceiptLetter(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    """
    A class providing methods to interact with "ReceiptLetter" entities in
    DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    receipt letter records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_letter(receipt_letter: ReceiptLetter):
        Adds a ReceiptLetter to DynamoDB.
    add_receipt_letters(receipt_letters: list[ReceiptLetter]):
        Adds multiple ReceiptLetters to DynamoDB in batches.
    update_receipt_letter(receipt_letter: ReceiptLetter):
        Updates an existing ReceiptLetter in the database.
    update_receipt_letters(receipt_letters: list[ReceiptLetter]):
        Updates multiple ReceiptLetters in the database.
    delete_receipt_letter(image_id, receipt_id, line_id, word_id, letter_id):
        Deletes a single ReceiptLetter by IDs.
    delete_receipt_letters(receipt_letters: list[ReceiptLetter]):
        Deletes multiple ReceiptLetters in batch.
    get_receipt_letter(...) -> ReceiptLetter:
        Retrieves a single ReceiptLetter by IDs.
    list_receipt_letters(...) -> tuple[list[ReceiptLetter], dict | None]:
        Returns ReceiptLetters and the last evaluated key.
    list_receipt_letters_from_word(...) -> list[ReceiptLetter]:
        Returns all ReceiptLetters for a given word.
    """

    @handle_dynamodb_errors("add_receipt_letter")
    def add_receipt_letter(self, receipt_letter: ReceiptLetter) -> None:
        """
        Adds a ReceiptLetter to DynamoDB.

        Parameters
        ----------
        receipt_letter : ReceiptLetter
            The ReceiptLetter to add.

        Raises
        ------
        ValueError
            If the receipt_letter is invalid or already exists.
        """
        if receipt_letter is None:
            raise EntityValidationError("receipt_letter cannot be None")
        if not isinstance(receipt_letter, ReceiptLetter):
            raise EntityValidationError(
                "receipt_letter must be an instance of ReceiptLetter"
            )
        self._add_entity(receipt_letter)

    @handle_dynamodb_errors("add_receipt_letters")
    def add_receipt_letters(self, receipt_letters: list[ReceiptLetter]) -> None:
        """
        Adds multiple ReceiptLetters to DynamoDB in batches.

        Parameters
        ----------
        receipt_letters : list[ReceiptLetter]
            The ReceiptLetters to add.

        Raises
        ------
        ValueError
            If the receipt_letters are invalid.
        """
        if receipt_letters is None:
            raise EntityValidationError("receipt_letters cannot be None")
        if not isinstance(receipt_letters, list):
            raise EntityValidationError("receipt_letters must be a list")
        for i, receipt_letter in enumerate(receipt_letters):
            if not isinstance(receipt_letter, ReceiptLetter):
                raise EntityValidationError(
                    f"receipt_letters[{i}] must be an instance of "
                    f"ReceiptLetter, got {type(receipt_letter).__name__}"
                )
        if not receipt_letters:  # Empty list check
            raise OperationError("Parameter validation failed")
        self._add_entities(receipt_letters, ReceiptLetter, "receipt_letters")

    @handle_dynamodb_errors("update_receipt_letter")
    def update_receipt_letter(self, receipt_letter: ReceiptLetter) -> None:
        """
        Updates an existing ReceiptLetter in the database.

        Parameters
        ----------
        receipt_letter : ReceiptLetter
            The ReceiptLetter to update.

        Raises
        ------
        ValueError
            If the receipt_letter is invalid or does not exist.
        """
        if receipt_letter is None:
            raise EntityValidationError("receipt_letter cannot be None")
        if not isinstance(receipt_letter, ReceiptLetter):
            raise EntityValidationError(
                "receipt_letter must be an instance of ReceiptLetter"
            )
        self._update_entity(receipt_letter)

    @handle_dynamodb_errors("update_receipt_letters")
    def update_receipt_letters(self, receipt_letters: list[ReceiptLetter]) -> None:
        """
        Updates multiple ReceiptLetters in the database.

        Parameters
        ----------
        receipt_letters : list[ReceiptLetter]
            The ReceiptLetters to update.

        Raises
        ------
        ValueError
            If the receipt_letters are invalid or do not exist.
        """
        if receipt_letters is None:
            raise EntityValidationError("receipt_letters cannot be None")
        if not isinstance(receipt_letters, list):
            raise EntityValidationError("receipt_letters must be a list")
        for i, receipt_letter in enumerate(receipt_letters):
            if not isinstance(receipt_letter, ReceiptLetter):
                raise EntityValidationError(
                    f"receipt_letters[{i}] must be an instance of "
                    f"ReceiptLetter, got {type(receipt_letter).__name__}"
                )
        self._update_entities(receipt_letters, ReceiptLetter, "receipt_letters")

    @handle_dynamodb_errors("delete_receipt_letter")
    def delete_receipt_letter(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        letter_id: int,
    ) -> None:
        """
        Deletes a single ReceiptLetter.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.
        line_id : int
            The line ID.
        word_id : int
            The word ID.
        letter_id : int
            The letter ID.

        Raises
        ------
        ValueError
            If parameters are invalid or letter does not exist.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        if line_id is None:
            raise EntityValidationError("line_id cannot be None")
        if not isinstance(line_id, int):
            raise EntityValidationError("line_id must be an integer.")
        if word_id is None:
            raise EntityValidationError("word_id cannot be None")
        if not isinstance(word_id, int):
            raise EntityValidationError("word_id must be an integer.")
        if letter_id is None:
            raise EntityValidationError("letter_id cannot be None")
        if not isinstance(letter_id, int):
            raise EntityValidationError("letter_id must be an integer.")

        # Direct key-based deletion is more efficient
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
                    f"#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                )
            },
        }
        self._client.delete_item(
            TableName=self.table_name,
            Key=key,
            ConditionExpression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("delete_receipt_letters")
    def delete_receipt_letters(self, receipt_letters: list[ReceiptLetter]) -> None:
        """
        Deletes multiple ReceiptLetters in batch.

        Parameters
        ----------
        receipt_letters : list[ReceiptLetter]
            The ReceiptLetters to delete.

        Raises
        ------
        ValueError
            If the receipt_letters are invalid.
        """
        if receipt_letters is None:
            raise EntityValidationError("receipt_letters cannot be None")
        if not isinstance(receipt_letters, list):
            raise EntityValidationError("receipt_letters must be a list")
        for i, receipt_letter in enumerate(receipt_letters):
            if not isinstance(receipt_letter, ReceiptLetter):
                raise EntityValidationError(
                    f"receipt_letters[{i}] must be an instance of "
                    f"ReceiptLetter, got {type(receipt_letter).__name__}"
                )
        self._delete_entities(receipt_letters)

    @handle_dynamodb_errors("get_receipt_letter")
    def get_receipt_letter(
        self,
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int,
        letter_id: int,
    ) -> ReceiptLetter:
        """
        Retrieves a single ReceiptLetter by IDs.

        Parameters
        ----------
        receipt_id : int
            The receipt ID.
        image_id : str
            The image ID.
        line_id : int
            The line ID.
        word_id : int
            The word ID.
        letter_id : int
            The letter ID.

        Returns
        -------
        ReceiptLetter
            The retrieved ReceiptLetter.

        Raises
        ------
        ValueError
            If parameters are invalid or letter not found.
        """
        # Validate all parameters
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be a positive integer")
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        self._validate_image_id(image_id)
        if line_id is None:
            raise EntityValidationError("line_id cannot be None")
        if not isinstance(line_id, int):
            raise EntityValidationError("line_id must be an integer")
        if line_id <= 0:
            raise EntityValidationError("line_id must be a positive integer")
        if word_id is None:
            raise EntityValidationError("word_id cannot be None")
        if not isinstance(word_id, int):
            raise EntityValidationError("word_id must be an integer")
        if word_id <= 0:
            raise EntityValidationError("word_id must be a positive integer")
        if letter_id is None:
            raise EntityValidationError("letter_id cannot be None")
        if not isinstance(letter_id, int):
            raise EntityValidationError("letter_id must be an integer")
        if letter_id <= 0:
            raise EntityValidationError("letter_id must be a positive integer")

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#"
                f"WORD#{word_id:05d}#LETTER#"
                f"{letter_id:05d}"
            ),
            entity_class=ReceiptLetter,
            converter_func=item_to_receipt_letter,
        )

        if result is None:
            raise EntityNotFoundError(f"ReceiptLetter with ID {letter_id} not found")

        return result

    @handle_dynamodb_errors("list_receipt_letters")
    def list_receipt_letters(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLetter], dict | None]:
        """
        Returns all ReceiptLetters from the table with pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of items to return.
        last_evaluated_key : dict, optional
            Key to continue pagination from.

        Returns
        -------
        tuple[list[ReceiptLetter], dict | None]
            List of ReceiptLetters and last evaluated key.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Parameter validation failed")
        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_by_type(
            entity_type="RECEIPT_LETTER",
            converter_func=item_to_receipt_letter,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_letters_from_word")
    def list_receipt_letters_from_word(
        self, image_id: str, receipt_id: int, line_id: int, word_id: int
    ) -> list[ReceiptLetter]:
        """
        Returns all ReceiptLetters for a given word.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.
        line_id : int
            The line ID.
        word_id : int
            The word ID.

        Returns
        -------
        list[ReceiptLetter]
            List of ReceiptLetters for the word.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        # Validate parameters
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        self._validate_image_id(image_id)
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be a positive integer")
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be a positive integer")
        if line_id is None:
            raise EntityValidationError("line_id cannot be None")
        if not isinstance(line_id, int):
            raise EntityValidationError("line_id must be an integer")
        if line_id <= 0:
            raise EntityValidationError("line_id must be a positive integer")
        if word_id is None:
            raise EntityValidationError("word_id cannot be None")
        if not isinstance(word_id, int):
            raise EntityValidationError("word_id must be an integer")
        if word_id <= 0:
            raise EntityValidationError("word_id must be a positive integer")

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression=("PK = :pkVal AND begins_with(SK, :skPrefix)"),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}"
                        f"#LINE#{line_id:05d}"
                        f"#WORD#{word_id:05d}"
                        "#LETTER#"
                    )
                },
            },
            converter_func=item_to_receipt_letter,
        )
        return results
