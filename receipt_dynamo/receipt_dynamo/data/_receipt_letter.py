from typing import TYPE_CHECKING, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptLetter, item_to_receipt_letter
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        QueryInputTypeDef,
        PutRequestTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
        DeleteRequestTypeDef,
    )

from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptLetter(DynamoClientProtocol):
    """
    A class used to access receipt letters in DynamoDB.

    Methods
    -------
    add_receipt_letter(letter: ReceiptLetter)
        Adds a ReceiptLetter to DynamoDB.
    add_receipt_letters(letters: list[ReceiptLetter])
        Adds multiple ReceiptLetters to DynamoDB in batches.
    update_receipt_letter(letter: ReceiptLetter)
        Updates an existing ReceiptLetter in the database.
    update_receipt_letters(letters: list[ReceiptLetter])
        Updates multiple ReceiptLetters in the database.
    delete_receipt_letter(letter: ReceiptLetter)
        Deletes a single ReceiptLetter by IDs.
    delete_receipt_letters(letters: list[ReceiptLetter])
        Deletes multiple ReceiptLetters in batch.
    get_receipt_letter(
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int,
        letter_id: int
    ) -> ReceiptLetter:
        Retrieves a single ReceiptLetter by IDs.
    list_receipt_letters(
        limit: Optional[int] = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptLetter], dict | None]:
        Returns ReceiptLetters and the last evaluated key.
    list_receipt_letters_from_word(
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int
    ) -> list[ReceiptLetter]:
        Returns all ReceiptLetters for a given word.
    """

    def add_receipt_letter(self, letter: ReceiptLetter):
        """Adds a ReceiptLetter to DynamoDB.

        Args:
            letter (ReceiptLetter): The ReceiptLetter to add.

        Raises:
            ValueError: If the letter is None or not an instance of ReceiptLetter.
            Exception: If the letter cannot be added to DynamoDB.
        """
        if letter is None:
            raise ValueError(
                "letter parameter is required and cannot be None."
            )
        if not isinstance(letter, ReceiptLetter):
            raise ValueError(
                "letter must be an instance of the ReceiptLetter class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add receipt letter to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not add receipt letter to DynamoDB: {e}"
                ) from e

    def add_receipt_letters(self, letters: list[ReceiptLetter]):
        """Adds multiple ReceiptLetters to DynamoDB in batches.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to add.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be added to DynamoDB.
        """
        if letters is None:
            raise ValueError(
                "letters parameter is required and cannot be None."
            )
        if not isinstance(letters, list):
            raise ValueError(
                "letters must be a list of ReceiptLetter instances."
            )
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        try:
            for i in range(0, len(letters), 25):
                chunk = letters[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=lt.to_item())
                    )
                    for lt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not add ReceiptLetters to the database: {e}"
                ) from e

    def update_receipt_letter(self, letter: ReceiptLetter):
        """Updates an existing ReceiptLetter in the database.

        Args:
            letter (ReceiptLetter): The ReceiptLetter to update.

        Raises:
            ValueError: If the letter is None or not an instance of ReceiptLetter.
            Exception: If the letter cannot be updated in DynamoDB.
        """
        if letter is None:
            raise ValueError(
                "letter parameter is required and cannot be None."
            )
        if not isinstance(letter, ReceiptLetter):
            raise ValueError(
                "letter must be an instance of the ReceiptLetter class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not update ReceiptLetter in the database: {e}"
                ) from e

    def update_receipt_letters(self, letters: list[ReceiptLetter]):
        """Updates multiple ReceiptLetters in the database.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to update.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be updated in DynamoDB.
        """
        if letters is None:
            raise ValueError(
                "letters parameter is required and cannot be None."
            )
        if not isinstance(letters, list):
            raise ValueError(
                "letters must be a list of ReceiptLetter instances."
            )
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        for i in range(0, len(letters), 25):
            chunk = letters[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": lt.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for lt in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)  # type: ignore[arg-type]
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptLetters do not exist"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise DynamoDBServerError(
                        f"Internal server error: {e}"
                    ) from e
                elif error_code == "ValidationException":
                    raise DynamoDBValidationError(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                else:
                    raise DynamoDBError(
                        f"Could not update ReceiptLetters in the database: {e}"
                    ) from e

    def delete_receipt_letter(
        self,
        letter: ReceiptLetter,
    ):
        """Deletes a single ReceiptLetter by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            line_id (int): The line ID.
            word_id (int): The word ID.
            letter_id (int): The letter ID.

        Raises:
            ValueError: If the letter ID is None.
            Exception: If the letter cannot be deleted from DynamoDB.
        """
        if letter is None:
            raise ValueError(
                "letter parameter is required and cannot be None."
            )
        if not isinstance(letter, ReceiptLetter):
            raise ValueError(
                "letter must be an instance of the ReceiptLetter class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=letter.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not delete ReceiptLetter from the database: {e}"
                ) from e

    def delete_receipt_letters(self, letters: list[ReceiptLetter]):
        """Deletes multiple ReceiptLetters in batch.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to delete.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be deleted from DynamoDB.
        """
        if letters is None:
            raise ValueError(
                "letters parameter is required and cannot be None."
            )
        if not isinstance(letters, list):
            raise ValueError(
                "letters must be a list of ReceiptLetter instances."
            )
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        try:
            for i in range(0, len(letters), 25):
                chunk = letters[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=lt.key())
                    )
                    for lt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not delete ReceiptLetters from the database: {e}"
                ) from e

    def get_receipt_letter(
        self,
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int,
        letter_id: int,
    ) -> ReceiptLetter:
        """Retrieves a single ReceiptLetter by IDs."""
        if receipt_id is None:
            raise ValueError(
                "receipt_id parameter is required and cannot be None."
            )
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError(
                "image_id parameter is required and cannot be None."
            )
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError(
                "line_id parameter is required and cannot be None."
            )
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError(
                "word_id parameter is required and cannot be None."
            )
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")
        if letter_id is None:
            raise ValueError(
                "letter_id parameter is required and cannot be None."
            )
        if not isinstance(letter_id, int):
            raise ValueError("letter_id must be an integer.")
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                    },
                },
            )
            if "Item" in response:
                return item_to_receipt_letter(response["Item"])
            else:
                raise ValueError(
                    f"ReceiptLetter with ID {letter_id} not found"
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "ValidationException":
                raise OperationError(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise OperationError(
                    f"Error getting receipt letter: {e}"
                ) from e

    def list_receipt_letters(
        self, limit: Optional[int] = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptLetter], dict | None]:
        """Returns all ReceiptLetters from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        receipt_letters = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_LETTER"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_letters.extend(
                [item_to_receipt_letter(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt letters.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    receipt_letters.extend(
                        [
                            item_to_receipt_letter(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_letters, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt letters from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            else:
                raise OperationError(
                    f"Error listing receipt letters: {e}"
                ) from e

    def list_receipt_letters_from_word(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> list[ReceiptLetter]:
        """Returns all ReceiptLetters for a given word."""
        if receipt_id is None:
            raise ValueError(
                "receipt_id parameter is required and cannot be None."
            )
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError(
                "image_id parameter is required and cannot be None."
            )
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError(
                "line_id parameter is required and cannot be None."
            )
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError(
                "word_id parameter is required and cannot be None."
            )
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")

        receipt_letters = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
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
            )
            receipt_letters.extend(
                [item_to_receipt_letter(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
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
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_letters.extend(
                    [
                        item_to_receipt_letter(item)
                        for item in response["Items"]
                    ]
                )
            return receipt_letters

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not list ReceiptLetters from the database: {e}"
                ) from e
