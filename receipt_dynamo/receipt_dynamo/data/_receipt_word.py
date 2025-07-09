from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptWord, item_to_receipt_word
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
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
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        BatchGetItemInputTypeDef,
        DeleteRequestTypeDef,
        GetItemInputTypeDef,
        KeysAndAttributesTypeDef,
        PutRequestTypeDef,
        PutTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptWord(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to represent a ReceiptWord in the database.

    Methods
    -------
    add_receipt_word(word: ReceiptWord)
        Adds a single ReceiptWord.
    add_receipt_words(words: list[ReceiptWord])
        Adds multiple ReceiptWords.
    update_receipt_word(word: ReceiptWord)
        Updates a ReceiptWord.
    delete_receipt_word(receipt_id: int, image_id: str, line_id: int, word_id: int)
        Deletes a single ReceiptWord by IDs.
    delete_receipt_words(words: list[ReceiptWord])
        Deletes multiple ReceiptWords.
    delete_receipt_words_from_line(receipt_id: int, image_id: str, line_id: int)
        Deletes all ReceiptWords from a given line within a receipt/image.
    get_receipt_word(receipt_id: int, image_id: str, line_id: int, word_id: int) -> ReceiptWord
        Retrieves a single ReceiptWord by IDs.
    list_receipt_words() -> list[ReceiptWord]
        Returns all ReceiptWords from the table.
    list_receipt_words_by_embedding_status(embedding_status: EmbeddingStatus) -> list[ReceiptWord]
        Returns all ReceiptWords from the table with a given embedding status.
    list_receipt_words_from_line(receipt_id: int, image_id: str, line_id: int) -> list[ReceiptWord]
        Returns all ReceiptWords that match the given receipt/image/line IDs.
    list_receipt_words_from_receipt(image_id: str, receipt_id: int) -> list[ReceiptWord]
        Returns all ReceiptWords that match the given receipt.
    """

    @handle_dynamodb_errors("add_receipt_word")
    def add_receipt_word(self, word: ReceiptWord) -> None:
        """Adds a single ReceiptWord to DynamoDB."""
        self._validate_entity(word, ReceiptWord, "word")
        self._add_entity(word)

    @handle_dynamodb_errors("add_receipt_words")
    def add_receipt_words(self, words: list[ReceiptWord]) -> None:
        """Adds multiple ReceiptWords to DynamoDB in batches of CHUNK_SIZE."""
        self._validate_entity_list(words, ReceiptWord, "words")

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=w.to_item()))
            for w in words
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_word")
    def update_receipt_word(self, word: ReceiptWord) -> None:
        """Updates an existing ReceiptWord in DynamoDB."""
        self._validate_entity(word, ReceiptWord, "word")
        self._update_entity(word)

    @handle_dynamodb_errors("update_receipt_words")
    def update_receipt_words(self, words: list[ReceiptWord]) -> None:
        """Updates multiple existing ReceiptWords in DynamoDB."""
        self._validate_entity_list(words, ReceiptWord, "words")

        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=w.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for w in words
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_word")
    def delete_receipt_word(self, word: ReceiptWord) -> None:
        """Deletes a single ReceiptWord by IDs."""
        self._validate_entity(word, ReceiptWord, "word")
        self._delete_entity(word)

    @handle_dynamodb_errors("delete_receipt_words")
    def delete_receipt_words(self, words: list[ReceiptWord]) -> None:
        """Deletes multiple ReceiptWords in batch."""
        self._validate_entity_list(words, ReceiptWord, "words")

        request_items = [
            WriteRequestTypeDef(DeleteRequest=DeleteRequestTypeDef(Key=w.key))
            for w in words
        ]
        self._batch_write_with_retry(request_items)

    def delete_receipt_words_from_line(
        self, receipt_id: int, image_id: str, line_id: int
    ):
        """Deletes all ReceiptWords from a given line within a receipt/image."""
        words = self.list_receipt_words_from_line(
            receipt_id, image_id, line_id
        )
        self.delete_receipt_words(words)

    def get_receipt_word(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> ReceiptWord:
        """Retrieves a single ReceiptWord by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
            )
            return item_to_receipt_word(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptWord with ID {word_id} not found")

    def get_receipt_words_by_indices(
        self, indices: list[tuple[str, int, int, int]]
    ) -> list[ReceiptWord]:
        """Retrieves multiple ReceiptWords by their indices."""
        if indices is None:
            raise ValueError(
                "indices parameter is required and cannot be None."
            )
        if not isinstance(indices, list):
            raise ValueError("indices must be a list of tuples.")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("indices must be a list of tuples.")
        for index in indices:
            if len(index) != 4:
                raise ValueError(
                    "indices must be a list of tuples with 4 elements."
                )
            if not isinstance(index[0], str):
                raise ValueError("First element of tuple must be a string.")
            assert_valid_uuid(index[0])
            if not isinstance(index[1], int):
                raise ValueError("Second element of tuple must be an integer.")
            if index[1] <= 0:
                raise ValueError("Second element of tuple must be positive.")
            if not isinstance(index[2], int):
                raise ValueError("Third element of tuple must be an integer.")
            if index[2] <= 0:
                raise ValueError("Third element of tuple must be positive.")
            if not isinstance(index[3], int):
                raise ValueError("Fourth element of tuple must be an integer.")
            if index[3] <= 0:
                raise ValueError("Fourth element of tuple must be positive.")

        keys = [
            {
                "PK": {"S": f"IMAGE#{index[0]}"},
                "SK": {
                    "S": f"RECEIPT#{index[1]:05d}#LINE#{index[2]:05d}#WORD#{index[3]:05d}"
                },
            }
            for index in indices
        ]
        return self.get_receipt_words_by_keys(keys)

    def get_receipt_words_by_keys(self, keys: list[dict]) -> list[ReceiptWord]:
        # Check the validity of the keys
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("Keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise ValueError("SK must start with 'RECEIPT#'")
            if not key["SK"]["S"].split("#")[2] == "LINE":
                raise ValueError("SK must contain 'LINE'")
            if not key["SK"]["S"].split("#")[4] == "WORD":
                raise ValueError("SK must contain 'WORD'")
        results = []

        try:
            # Split keys into chunks of up to 100
            for i in range(0, len(keys), CHUNK_SIZE):
                chunk = keys[i : i + CHUNK_SIZE]

                # Prepare parameters for BatchGetItem
                request: BatchGetItemInputTypeDef = {
                    "RequestItems": {
                        self.table_name: {
                            "Keys": chunk,
                        }
                    }
                }

                # Perform BatchGet
                response = self._client.batch_get_item(**request)

                # Combine all found items
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)

                # Retry unprocessed keys if any
                unprocessed = response.get("UnprocessedKeys", {})
                while unprocessed.get(self.table_name, {}).get("Keys"):  # type: ignore[call-overload]
                    response = self._client.batch_get_item(
                        RequestItems=unprocessed
                    )
                    batch_items = response["Responses"].get(
                        self.table_name, []
                    )
                    results.extend(batch_items)
                    unprocessed = response.get("UnprocessedKeys", {})

            return [item_to_receipt_word(result) for result in results]

        except ClientError as e:
            raise ValueError(
                f"Could not get ReceiptWords from the database: {e}"
            ) from e

    def list_receipt_words(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[list[ReceiptWord], Optional[Dict[str, Any]]]:
        """Returns all ReceiptWords from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )

        receipt_words = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_WORD"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_words.extend(
                [item_to_receipt_word(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt words.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    receipt_words.extend(
                        [
                            item_to_receipt_word(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_words, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt words from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing receipt words: {e}"
                ) from e

    def list_receipt_words_from_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image/line IDs."""
        receipt_words = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                    },
                },
            )
            receipt_words.extend(
                [item_to_receipt_word(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                    ExpressionAttributeValues={
                        ":pk_val": {"S": f"IMAGE#{image_id}"},
                        ":sk_val": {
                            "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_words.extend(
                    [item_to_receipt_word(item) for item in response["Items"]]
                )
            return receipt_words
        except ClientError as e:
            raise ValueError(
                f"Could not list ReceiptWords from the database: {e}"
            )

    def list_receipt_words_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image IDs.

        Args:
            image_id (str): The ID of the image
            receipt_id (int): The ID of the receipt

        Returns:
            list[ReceiptWord]: List of ReceiptWord entities for the given receipt

        Raises:
            ValueError: If the parameters are invalid or if there's an error querying DynamoDB
        """
        if image_id is None:
            raise ValueError(
                "image_id parameter is required and cannot be None."
            )
        if receipt_id is None:
            raise ValueError(
                "receipt_id parameter is required and cannot be None."
            )
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")

        receipt_words = []
        try:
            # Query parameters using BETWEEN to get only WORD items
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk_val AND #sk BETWEEN :sk_start AND :sk_end",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_start": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                    ":sk_end": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#\uffff#WORD#\uffff"
                    },
                },
            }

            # Initial query
            response = self._client.query(**query_params)
            receipt_words.extend(
                [
                    item_to_receipt_word(item)
                    for item in response["Items"]
                    if "#WORD#" in item["SK"]["S"]
                    and not item["SK"]["S"].endswith("#TAG#")
                    and not item["SK"]["S"].endswith("#LETTER#")
                ]
            )

            # Handle pagination
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                receipt_words.extend(
                    [
                        item_to_receipt_word(item)
                        for item in response["Items"]
                        if "#WORD#" in item["SK"]["S"]
                        and not item["SK"]["S"].endswith("#TAG#")
                        and not item["SK"]["S"].endswith("#LETTER#")
                    ]
                )

            return receipt_words

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt words from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing receipt words: {e}"
                ) from e

    def list_receipt_words_by_embedding_status(
        self, embedding_status: EmbeddingStatus
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given embedding status."""
        receipt_words: list[ReceiptWord] = []
        # Validate and normalize embedding_status argument
        if isinstance(embedding_status, EmbeddingStatus):
            status_str = embedding_status.value
        elif isinstance(embedding_status, str):
            status_str = embedding_status
        else:
            raise ValueError(
                "embedding_status must be a string or EmbeddingStatus enum"
            )
        # Ensure the status_str is a valid EmbeddingStatus value
        valid_values = [s.value for s in EmbeddingStatus]
        if status_str not in valid_values:
            raise ValueError(
                "embedding_status must be one of: {', '.join(valid_values)}; Got: {status_str}"
            )
        try:
            # Query the GSI1 index on embedding status
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression="#gsi1pk = :status",
                ExpressionAttributeNames={"#gsi1pk": "GSI1PK"},
                ExpressionAttributeValues={
                    ":status": {"S": f"EMBEDDING_STATUS#{status_str}"}
                },
            )
            # First page
            for item in response.get("Items", []):
                receipt_words.append(item_to_receipt_word(item))
            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI1",
                    KeyConditionExpression="#gsi1pk = :status",
                    ExpressionAttributeNames={"#gsi1pk": "GSI1PK"},
                    ExpressionAttributeValues={
                        ":status": {"S": f"EMBEDDING_STATUS#{status_str}"}
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                for item in response.get("Items", []):
                    receipt_words.append(item_to_receipt_word(item))
            return receipt_words
        except ClientError as e:
            raise ValueError(
                f"Could not list receipt words by embedding status: {e}"
            ) from e
