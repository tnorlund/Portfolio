from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    OperationError,
)
from receipt_dynamo.entities import item_to_receipt_line
from receipt_dynamo.entities.receipt_line import ReceiptLine
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

CHUNK_SIZE = 25


class _ReceiptLine(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to represent a ReceiptLine in the database
    (similar to _line.py).

    Methods
    -------
    add_receipt_line(line: ReceiptLine)
        Adds a receipt-line to the database.
    add_receipt_lines(lines: list[ReceiptLine])
        Adds multiple receipt-lines in batch.
    update_receipt_line(line: ReceiptLine)
        Updates an existing receipt-line.
    delete_receipt_line(receipt_id: int, image_id: str, line_id: int)
        Deletes a specific receipt-line by IDs.
    delete_receipt_lines(lines: list[ReceiptLine])
        Deletes multiple receipt-lines in batch.
    get_receipt_line(receipt_id: int, image_id: str, line_id: int)
        -> ReceiptLine
        Retrieves a single receipt-line by IDs.
    list_receipt_lines() -> list[ReceiptLine]
        Returns all ReceiptLines from the table.
    list_receipt_lines_from_receipt(receipt_id: int, image_id: str)
        -> list[ReceiptLine]
        Returns all lines under a specific receipt/image.
    """

    @handle_dynamodb_errors("add_receipt_line")
    def add_receipt_line(self, line: ReceiptLine) -> None:
        """Adds a single ReceiptLine to DynamoDB."""
        self._validate_entity(line, ReceiptLine, "line")
        self._add_entity(line)

    @handle_dynamodb_errors("add_receipt_lines")
    def add_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Adds multiple ReceiptLines to DynamoDB in batches of CHUNK_SIZE."""
        self._validate_entity_list(lines, ReceiptLine, "lines")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=ln.to_item())
            )
            for ln in lines
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_line")
    def update_receipt_line(self, line: ReceiptLine) -> None:
        """Updates an existing ReceiptLine in DynamoDB."""
        self._validate_entity(line, ReceiptLine, "line")
        self._update_entity(line)

    @handle_dynamodb_errors("update_receipt_lines")
    def update_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Updates multiple existing ReceiptLines in DynamoDB."""
        self._validate_entity_list(lines, ReceiptLine, "lines")

        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=ln.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for ln in lines
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_line")
    def delete_receipt_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> None:
        """Deletes a single ReceiptLine by IDs."""
        # Direct key-based deletion is more efficient than creating
        # dummy objects
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
        }
        self._client.delete_item(
            TableName=self.table_name,
            Key=key,
            ConditionExpression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("delete_receipt_lines")
    def delete_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Deletes multiple ReceiptLines in batch."""
        self._validate_entity_list(lines, ReceiptLine, "lines")

        request_items = [
            WriteRequestTypeDef(DeleteRequest=DeleteRequestTypeDef(Key=ln.key))
            for ln in lines
        ]
        self._batch_write_with_retry(request_items)

    def get_receipt_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> ReceiptLine:
        """Retrieves a single ReceiptLine by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
                    },
                },
            )
            return item_to_receipt_line(response["Item"])
        except KeyError as e:
            raise ValueError(
                f"ReceiptLine with image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id} not found"
            )

    def get_receipt_lines_by_indices(
        self, indices: list[tuple[str, int, int]]
    ) -> list[ReceiptLine]:
        """Retrieves multiple ReceiptLines by their indices."""
        if indices is None:
            raise ValueError("indices cannot be None")
        if not isinstance(indices, list):
            raise ValueError("indices must be a list of tuples.")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("indices must be a list of tuples.")

        for index in indices:
            if len(index) != 3:
                raise ValueError(
                    "indices must be a list of tuples with 3 elements."
                )
            if not isinstance(index[0], str):
                raise ValueError("First element of tuple must be a string.")
            assert_valid_uuid(index[0])
            if not isinstance(index[1], int):
                raise ValueError("Second element of tuple must be an integer.")
            if not isinstance(index[2], int):
                raise ValueError("Third element of tuple must be an integer.")

        # Assemble the keys
        keys = []
        for index in indices:
            keys.append(
                {
                    "PK": {"S": f"IMAGE#{index[0]}"},
                    "SK": {"S": f"RECEIPT#{index[1]:05d}#LINE#{index[2]:05d}"},
                }
            )

        # Get the receipt lines
        return self.get_receipt_lines_by_keys(keys)

    def get_receipt_lines_by_keys(self, keys: list[dict]) -> list[ReceiptLine]:
        """Retrieves multiple ReceiptLines by their keys."""
        if keys is None:
            raise ValueError("keys cannot be None")
        if not isinstance(keys, list):
            raise ValueError("keys must be a list of dictionaries.")
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise ValueError("SK must start with 'RECEIPT#'")
            if len(key["SK"]["S"].split("#")[1]) != 5:
                raise ValueError("SK must contain a 5-digit receipt ID")
            if not key["SK"]["S"].split("#")[2] == "LINE":
                raise ValueError("SK must contain 'LINE'")
            if len(key["SK"]["S"].split("#")[3]) != 5:
                raise ValueError("SK must contain a 5-digit line ID")

        # Get the receipt lines
        results = []
        for i in range(0, len(keys), CHUNK_SIZE):
            chunk = keys[i : i + CHUNK_SIZE]
            request: BatchGetItemInputTypeDef = {
                "RequestItems": {
                    self.table_name: {
                        "Keys": chunk,
                    }
                }
            }
            try:
                response = self._client.batch_get_item(**request)
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)

                unprocessed = response.get("UnprocessedKeys", {})
                while unprocessed.get(  # type: ignore[call-overload]
                    self.table_name, {}
                ).get("Keys"):
                    response = self._client.batch_get_item(
                        RequestItems=unprocessed
                    )
                    batch_items = response["Responses"].get(
                        self.table_name, []
                    )
                    results.extend(batch_items)
                    unprocessed = response.get("UnprocessedKeys", {})
            except ClientError as e:
                raise ValueError(
                    f"Could not get ReceiptLines from the database: {e}"
                ) from e

        return [item_to_receipt_line(result) for result in results]

    def list_receipt_lines(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[list[ReceiptLine], Optional[Dict[str, Any]]]:
        """Returns all ReceiptLines from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )
        receipt_lines = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_LINE"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_lines.extend(
                [item_to_receipt_line(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt lines.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    receipt_lines.extend(
                        [
                            item_to_receipt_line(item)
                            for item in response["Items"]
                        ]
                    )
                # No further pages left. LEK is None.
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_lines, last_evaluated_key

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt lines from DynamoDB: {e}"
                )
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
                raise OperationError(f"Error listing receipt lines: {e}")

    def list_receipt_lines_by_embedding_status(
        self, embedding_status: EmbeddingStatus | str
    ) -> list[ReceiptLine]:
        """Returns all ReceiptLines from the table with a given embedding
        status."""
        receipt_lines: list[ReceiptLine] = []

        if isinstance(embedding_status, EmbeddingStatus):
            status_str = embedding_status.value
        elif isinstance(embedding_status, str):
            status_str = embedding_status
        else:
            raise ValueError(
                "embedding_status must be an instance of EmbeddingStatus "
                "or a string"
            )

        if status_str not in [status.value for status in EmbeddingStatus]:
            raise ValueError(
                "embedding_status must be a valid EmbeddingStatus"
            )

        try:
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
                receipt_lines.append(item_to_receipt_line(item))
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
                    receipt_lines.append(item_to_receipt_line(item))
            return receipt_lines
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt lines from DynamoDB: {e}"
                )
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
                raise ValueError(
                    f"Could not list ReceiptLines from the database: {e}"
                ) from e

    def list_receipt_lines_from_receipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptLine]:
        """Returns all lines under a specific receipt/image."""
        receipt_lines = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                },
            )
            receipt_lines.extend(
                [item_to_receipt_line(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"IMAGE#{image_id}"},
                        ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_lines.extend(
                    [item_to_receipt_line(item) for item in response["Items"]]
                )

            return receipt_lines
        except ClientError as e:
            raise ValueError(
                "Could not list ReceiptLines from the database"
            ) from e
