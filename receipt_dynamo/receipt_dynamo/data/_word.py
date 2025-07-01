from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo import Word, item_to_word
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        BatchGetItemInputTypeDef,
        GetItemInputTypeDef,
        KeysAndAttributesTypeDef,
        QueryInputTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Word(DynamoClientProtocol):
    """
    A class used to represent a Word in the database.

    Methods
    -------
    add_word(word: Word)
        Adds a word to the database.

    """

    def add_word(self, word: Word):
        """Adds a word to the database

        Args:
            word (Word): The word to add to the database

        Raises:
            ValueError: When a word with the same ID already
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError:
            raise ValueError(f"Word with ID {word.word_id} already exists")

    def add_words(self, words: list[Word]):
        """Adds a list of words to the database

        Args:
            words (list[Word]): The words to add to the database

        Raises:
            ValueError: When a word with the same ID already
        """
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=word.to_item())
                    )
                    for word in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError:
            raise ValueError("Could not add words to the database")

    def update_word(self, word: Word):
        """Updates a word in the database

        Args:
            word (Word): The word to update in the database

        Raises:
            ValueError: When a word with the same ID does not exist
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Word with ID {word.word_id} not found") from e
            else:
                raise OperationError(f"Error updating word: {e}") from e

    def update_words(self, words: list[Word]):
        """
        Updates multiple Word items in the database.

        This method validates that the provided parameter is a list of Word instances.
        It uses DynamoDB's transact_write_items operation, which can handle up to 25 items
        per transaction. Any unprocessed items are automatically retried until no unprocessed
        items remain.

        Parameters
        ----------
        words : list[Word]
            The list of Word objects to update.

        Raises
        ------
        ValueError: When given a bad parameter.
        Exception: For underlying DynamoDB errors such as:
            - ProvisionedThroughputExceededException (exceeded capacity)
            - InternalServerError (server-side error)
            - ValidationException (invalid parameters)
            - AccessDeniedException (permission issues)
            - or any other unexpected errors.
        """
        if words is None:
            raise ValueError("Words parameter is required and cannot be None.")
        if not isinstance(words, list):
            raise ValueError("Words must be provided as a list.")
        if not all(isinstance(word, Word) for word in words):
            raise ValueError(
                "All items in the words list must be instances of the Word class."
            )

        for i in range(0, len(words), CHUNK_SIZE):
            chunk = words[i : i + CHUNK_SIZE]
            transact_items = []
            for word in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Put=PutTypeDef(
                            TableName=self.table_name,
                            Item=word.to_item(),
                            ConditionExpression="attribute_exists(PK)",
                        )
                    )
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError("One or more words do not exist") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise DynamoDBServerError(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise DynamoDBValidationError(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                elif error_code == "ResourceNotFoundException":
                    raise ValueError(f"Error updating words: {e}") from e
                else:
                    raise DynamoDBError(
                        f"Could not update words in the database: {e}"
                    ) from e

    def delete_word(self, image_id: str, line_id: int, word_id: int):
        """Deletes a word from the database

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Word with ID {word_id} not found") from e

    def delete_words(self, words: list[Word]):
        """Deletes a list of words from the database"""
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=word.key())
                    )
                    for word in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError:
            raise ValueError("Could not delete words from the database")

    def delete_words_from_line(self, image_id: str, line_id: int):
        """Deletes all words from a line

        Args:
            image_id (int): The ID of the image the line belongs to
            line_id (int): The ID of the line to delete words from
        """
        words = self.list_words_from_line(image_id, line_id)
        self.delete_words(words)

    def get_word(self, image_id: str, line_id: int, word_id: int) -> Word:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"},
                },
            )
            return item_to_word(response["Item"])
        except KeyError:
            raise ValueError(f"Word with ID {word_id} not found")

    def get_words(self, keys: list[dict]) -> list[Word]:
        """Get a list of words using a list of keys"""
        # Check the validity of the keys
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("Keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("LINE#"):
                raise ValueError("SK must start with 'LINE#'")
            if not key["SK"]["S"].split("#")[-2] == "WORD":
                raise ValueError("SK must contain 'WORD'")
        results = []

        # Split keys into chunks of up to 100
        for i in range(0, len(keys), CHUNK_SIZE):
            chunk = keys[i : i + CHUNK_SIZE]

            # Prepare parameters for BatchGetItem
            request: BatchGetItemInputTypeDef = {
                "RequestItems": {self.table_name: {"Keys": chunk}}
            }
            # (Optional) "ProjectionExpression": "..." if you only want certain attributes

            # Perform BatchGet
            response = self._client.batch_get_item(**request)

            # Combine all found items
            batch_items = response["Responses"].get(self.table_name, [])
            results.extend(batch_items)

            # Retry unprocessed keys if any
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name, {}).get("Keys"):  # type: ignore[call-overload]
                response = self._client.batch_get_item(RequestItems=unprocessed)
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})

        return [item_to_word(result) for result in results]

    def list_words(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[list[Word], Optional[Dict[str, Any]]]:
        words = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "WORD"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            words.extend([item_to_word(item) for item in response["Items"]])
            if limit is None:
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    words.extend([item_to_word(item) for item in response["Items"]])
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)
            return words, last_evaluated_key
        except ClientError as e:
            raise ValueError("Could not list words from the database") from e

    def list_words_from_line(self, image_id: str, line_id: int) -> list[Word]:
        words = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {"S": f"LINE#{line_id:05d}#WORD#"},
                },
            )
            words.extend([item_to_word(item) for item in response["Items"]])
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {"S": f"LINE#{line_id:05d}#WORD#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                words.extend([item_to_word(item) for item in response["Items"]])
            return words
        except ClientError as e:
            raise ValueError("Could not list words from the database") from e
