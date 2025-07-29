# infra/lambda_layer/python/dynamo/data/_receipt_letter.py
from typing import TYPE_CHECKING, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    QueryInputTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)
from receipt_dynamo.entities import item_to_receipt_letter
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptLetter(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
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
    add_receipt_letter(letter: ReceiptLetter):
        Adds a ReceiptLetter to DynamoDB.
    add_receipt_letters(letters: list[ReceiptLetter]):
        Adds multiple ReceiptLetters to DynamoDB in batches.
    update_receipt_letter(letter: ReceiptLetter):
        Updates an existing ReceiptLetter in the database.
    update_receipt_letters(letters: list[ReceiptLetter]):
        Updates multiple ReceiptLetters in the database.
    delete_receipt_letter(letter: ReceiptLetter):
        Deletes a single ReceiptLetter by IDs.
    delete_receipt_letters(letters: list[ReceiptLetter]):
        Deletes multiple ReceiptLetters in batch.
    get_receipt_letter(...) -> ReceiptLetter:
        Retrieves a single ReceiptLetter by IDs.
    list_receipt_letters(...) -> tuple[list[ReceiptLetter], dict | None]:
        Returns ReceiptLetters and the last evaluated key.
    list_receipt_letters_from_word(...) -> list[ReceiptLetter]:
        Returns all ReceiptLetters for a given word.
    """

    @handle_dynamodb_errors("add_receipt_letter")
    def add_receipt_letter(self, letter: ReceiptLetter) -> None:
        """
        Adds a ReceiptLetter to DynamoDB.

        Parameters
        ----------
        letter : ReceiptLetter
            The ReceiptLetter to add.

        Raises
        ------
        ValueError
            If the letter is invalid or already exists.
        """
        self._validate_entity(letter, ReceiptLetter, "letter")
        self._add_entity(letter)

    @handle_dynamodb_errors("add_receipt_letters")
    def add_receipt_letters(self, letters: list[ReceiptLetter]) -> None:
        """
        Adds multiple ReceiptLetters to DynamoDB in batches.

        Parameters
        ----------
        letters : list[ReceiptLetter]
            The ReceiptLetters to add.

        Raises
        ------
        ValueError
            If the letters are invalid.
        """
        self._validate_entity_list(letters, ReceiptLetter, "letters")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=lt.to_item())
            )
            for lt in letters
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_letter")
    def update_receipt_letter(self, letter: ReceiptLetter) -> None:
        """
        Updates an existing ReceiptLetter in the database.

        Parameters
        ----------
        letter : ReceiptLetter
            The ReceiptLetter to update.

        Raises
        ------
        ValueError
            If the letter is invalid or does not exist.
        """
        self._validate_entity(letter, ReceiptLetter, "letter")
        self._update_entity(letter)

    @handle_dynamodb_errors("update_receipt_letters")
    def update_receipt_letters(self, letters: list[ReceiptLetter]) -> None:
        """
        Updates multiple ReceiptLetters in the database.

        Parameters
        ----------
        letters : list[ReceiptLetter]
            The ReceiptLetters to update.

        Raises
        ------
        ValueError
            If the letters are invalid or do not exist.
        """
        self._update_entities(letters, ReceiptLetter, "letters")

    @handle_dynamodb_errors("delete_receipt_letter")
    def delete_receipt_letter(self, letter: ReceiptLetter) -> None:
        """
        Deletes a single ReceiptLetter.

        Parameters
        ----------
        letter : ReceiptLetter
            The ReceiptLetter to delete.

        Raises
        ------
        ValueError
            If the letter is invalid or does not exist.
        """
        self._validate_entity(letter, ReceiptLetter, "letter")
        self._delete_entity(letter)

    @handle_dynamodb_errors("delete_receipt_letters")
    def delete_receipt_letters(self, letters: list[ReceiptLetter]) -> None:
        """
        Deletes multiple ReceiptLetters in batch.

        Parameters
        ----------
        letters : list[ReceiptLetter]
            The ReceiptLetters to delete.

        Raises
        ------
        ValueError
            If the letters are invalid.
        """
        self._validate_entity_list(letters, ReceiptLetter, "letters")

        request_items = [
            WriteRequestTypeDef(DeleteRequest=DeleteRequestTypeDef(Key=lt.key))
            for lt in letters
        ]
        self._batch_write_with_retry(request_items)

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
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id cannot be None")
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError("line_id cannot be None")
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError("word_id cannot be None")
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")
        if letter_id is None:
            raise ValueError("letter_id cannot be None")
        if not isinstance(letter_id, int):
            raise ValueError("letter_id must be an integer.")

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}",
            entity_class=ReceiptLetter,
            converter_func=item_to_receipt_letter
        )
        
        if result is None:
            raise ValueError(f"ReceiptLetter with ID {letter_id} not found")
        
        return result

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
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={":val": {"S": "RECEIPT_LETTER"}},
            converter_func=item_to_receipt_letter,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    def list_receipt_letters_from_word(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> list[ReceiptLetter]:
        """
        Returns all ReceiptLetters for a given word.

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

        Returns
        -------
        list[ReceiptLetter]
            List of ReceiptLetters for the word.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id cannot be None")
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError("line_id cannot be None")
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError("word_id cannot be None")
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pkVal AND begins_with(SK, :skPrefix)",
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
            converter_func=item_to_receipt_letter
        )
        return results
