from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    handle_dynamodb_errors,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    WriteRequestTypeDef,
)
from receipt_dynamo.entities import item_to_letter
from receipt_dynamo.entities.letter import Letter
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Letter(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
):
    """
    A class used to represent a Letter in the database.

    Methods
    -------
    add_letter(letter: Letter)
        Adds a letter to the database.
    add_letters(letters: list[Letter])
        Adds multiple letters to the database.
    update_letter(letter: Letter)
        Updates a letter in the database.
    delete_letter(image_id: str, line_id: int, word_id: int, letter_id: int)
        Deletes a letter from the database.
    delete_letters(letters: list[Letter])
        Deletes multiple letters from the database.
    get_letter(image_id: str, line_id: int, word_id: int, letter_id: int)
        -> Letter
        Gets a letter from the database.
    list_letters(limit: Optional[int] = None, last_evaluated_key:
        Optional[Dict] = None) -> Tuple[list[Letter], Optional[Dict]]
        Lists all letters from the database.
    list_letters_from_word(image_id: str, line_id: int, word_id: int)
        -> list[Letter]
        Lists all letters from a specific word.
    """

    @handle_dynamodb_errors("add_letter")
    def add_letter(self, letter: Letter):
        """Adds a letter to the database

        Args:
            letter (Letter): The letter to add to the database

        Raises:
            ValueError: When a letter with the same ID already exists
        """
        self._validate_entity(letter, Letter, "letter")
        self._add_entity(letter)

    @handle_dynamodb_errors("add_letters")
    def add_letters(self, letters: List[Letter]):
        """Adds a list of letters to the database

        Args:
            letters (list[Letter]): The letters to add to the database

        Raises:
            ValueError: When validation fails or letters cannot be added
        """
        self._validate_entity_list(letters, Letter, "letters")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=letter.to_item())
            )
            for letter in letters
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_letter")
    def update_letter(self, letter: Letter):
        """Updates a letter in the database

        Args:
            letter (Letter): The letter to update in the database

        Raises:
            ValueError: When the letter does not exist
        """
        self._validate_entity(letter, Letter, "letter")
        self._update_entity(letter)

    @handle_dynamodb_errors("delete_letter")
    def delete_letter(
        self, image_id: str, line_id: int, word_id: int, letter_id: int
    ):
        """Deletes a letter from the database

        Args:
            image_id (str): The UUID of the image the letter belongs to
            line_id (int): The ID of the line the letter belongs to
            word_id (int): The ID of the word the letter belongs to
            letter_id (int): The ID of the letter to delete
        """
        # Validate UUID
        assert_valid_uuid(image_id)

        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"LINE#{line_id:05d}#WORD#{word_id:05d}#"
                        f"LETTER#{letter_id:05d}"
                    )
                },
            },
            ConditionExpression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("delete_letters")
    def delete_letters(self, letters: List[Letter]):
        """Deletes a list of letters from the database

        Args:
            letters (list[Letter]): The letters to delete
        """
        self._validate_entity_list(letters, Letter, "letters")

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=letter.key)
            )
            for letter in letters
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_letters_from_word")
    def delete_letters_from_word(
        self, image_id: str, line_id: int, word_id: int
    ):
        """Deletes all letters from a word

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete letters from
        """
        letters = self.list_letters_from_word(image_id, line_id, word_id)
        if letters:
            self.delete_letters(letters)

    @handle_dynamodb_errors("get_letter")
    def get_letter(
        self, image_id: str, line_id: int, word_id: int, letter_id: int
    ) -> Letter:
        """Gets a letter from the database

        Args:
            image_id (str): The UUID of the image the letter belongs to
            line_id (int): The ID of the line the letter belongs to
            word_id (int): The ID of the word the letter belongs to
            letter_id (int): The ID of the letter to get

        Returns:
            Letter: The letter object

        Raises:
            ValueError: When the letter is not found
        """
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"LINE#{line_id:05d}#WORD#{word_id:05d}#"
                        f"LETTER#{letter_id:05d}"
                    )
                },
            },
        )
        if "Item" not in response:
            raise ValueError(f"Letter with ID {letter_id} not found")
        return item_to_letter(response["Item"])

    @handle_dynamodb_errors("list_letters")
    def list_letters(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Letter], Optional[Dict]]:
        """Lists all letters in the database

        Args:
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination

        Returns:
            Tuple of letters list and last evaluated key for pagination
        """
        letters = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "LETTER"}},
            "ScanIndexForward": True,
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        letters.extend([item_to_letter(item) for item in response["Items"]])

        if limit is None:
            while (
                "LastEvaluatedKey" in response and response["LastEvaluatedKey"]
            ):
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                letters.extend(
                    [item_to_letter(item) for item in response["Items"]]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return letters, last_evaluated_key

    @handle_dynamodb_errors("list_letters_from_word")
    def list_letters_from_word(
        self, image_id: str, line_id: int, word_id: int
    ) -> List[Letter]:
        """List all letters from a specific word.

        Args:
            image_id: The UUID of the image
            line_id: The ID of the line
            word_id: The ID of the word

        Returns:
            List of Letter objects from the specified word
        """
        letters = []
        response = self._client.query(
            TableName=self.table_name,
            KeyConditionExpression=(
                "PK = :pkVal AND begins_with(SK, :skPrefix)"
            ),
            ExpressionAttributeValues={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {
                    "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#"
                },
            },
        )
        letters.extend([item_to_letter(item) for item in response["Items"]])

        while "LastEvaluatedKey" in response:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression=(
                    "PK = :pkVal AND begins_with(SK, :skPrefix)"
                ),
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#"
                    },
                },
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            letters.extend(
                [item_to_letter(item) for item in response["Items"]]
            )

        return letters
