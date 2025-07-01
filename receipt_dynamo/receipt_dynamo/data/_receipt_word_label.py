from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        BatchGetItemInputTypeDef,
        DeleteTypeDef,
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
    DeleteTypeDef,
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
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _ReceiptWordLabel(DynamoClientProtocol):
    def add_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Adds a receipt word label to the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to add to the database

        Raises:
            ValueError: When a receipt word label with the same ID already exists
        """
        if receipt_word_label is None:
            raise ValueError(
                "ReceiptWordLabel parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_label, ReceiptWordLabel):
            raise ValueError(
                "receipt_word_label must be an instance of the ReceiptWordLabel class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_word_label.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt word label for Image ID '{receipt_word_label.image_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add receipt word label to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not add receipt word label to DynamoDB: {e}"
                ) from e

    def add_receipt_word_labels(
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """Adds a list of receipt word labels to the database

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word labels to add to the database

        Raises:
            ValueError: When a receipt word label with the same ID already exists
        """
        if receipt_word_labels is None:
            raise ValueError(
                "ReceiptWordLabels parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_labels, list):
            raise ValueError(
                "receipt_word_labels must be a list of ReceiptWordLabel instances."
            )
        if not all(
            isinstance(label, ReceiptWordLabel)
            for label in receipt_word_labels
        ):
            raise ValueError(
                "All receipt word labels must be instances of the ReceiptWordLabel class."
            )
        try:
            for i in range(0, len(receipt_word_labels), 25):
                chunk = receipt_word_labels[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=label.to_item())
                    )
                    for label in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
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
            else:
                raise ValueError(
                    f"Error adding receipt word labels: {e}"
                ) from e

    def update_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Updates a receipt word label in the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to update in the database

        Raises:
            ValueError: When the receipt word label does not exist
        """
        if receipt_word_label is None:
            raise ValueError(
                "ReceiptWordLabel parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_label, ReceiptWordLabel):
            raise ValueError(
                "receipt_word_label must be an instance of the ReceiptWordLabel class."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_word_label.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt word label for Image ID '{receipt_word_label.image_id}' does not exist"
                )
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
            else:
                raise ValueError(
                    f"Error updating receipt word label: {e}"
                ) from e

    def update_receipt_word_labels(
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """
        Updates a list of receipt word labels in the database using transactions.
        Each receipt word label update is conditional upon the label already existing.

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word labels to update in the database.

        Raises:
            ValueError: When given a bad parameter or if a label doesn't exist.
            Exception: For underlying DynamoDB errors.
        """
        if receipt_word_labels is None:
            raise ValueError(
                "ReceiptWordLabels parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_labels, list):
            raise ValueError(
                "receipt_word_labels must be a list of ReceiptWordLabel instances."
            )
        if not all(
            isinstance(label, ReceiptWordLabel)
            for label in receipt_word_labels
        ):
            raise ValueError(
                "All receipt word labels must be instances of the ReceiptWordLabel class."
            )

        # Process labels in chunks of 25 because transact_write_items
        # supports a maximum of 25 operations.
        for i in range(0, len(receipt_word_labels), 25):
            chunk = receipt_word_labels[i : i + 25]
            transact_items = []
            for label in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Put=PutTypeDef(
                            TableName=self.table_name,
                            Item=label.to_item(),
                            ConditionExpression="attribute_exists(PK)",
                        )
                    )
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "One or more receipt word labels do not exist"
                    ) from e
                elif error_code == "TransactionCanceledException":
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more receipt word labels do not exist"
                        ) from e
                    else:
                        raise DynamoDBError(
                            f"Error updating receipt word labels: {e}"
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
                elif error_code == "ResourceNotFoundException":
                    raise ValueError(
                        f"Error updating receipt word labels: {e}"
                    ) from e
                else:
                    raise DynamoDBError(
                        f"Error updating receipt word labels: {e}"
                    ) from e

    def delete_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Deletes a receipt word label from the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to delete from the database

        Raises:
            ValueError: When the receipt word label does not exist
        """
        if receipt_word_label is None:
            raise ValueError(
                "ReceiptWordLabel parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_label, ReceiptWordLabel):
            raise ValueError(
                "receipt_word_label must be an instance of the ReceiptWordLabel class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=receipt_word_label.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt word label for Image ID '{receipt_word_label.image_id}' does not exist"
                )
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
            else:
                raise ValueError(
                    f"Error deleting receipt word label: {e}"
                ) from e

    def delete_receipt_word_labels(
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """
        Deletes a list of receipt word labels from the database using transactions.
        Each delete operation is conditional upon the label existing.

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word labels to delete from the database.

        Raises:
            ValueError: When a receipt word label does not exist or if another error occurs.
        """
        if receipt_word_labels is None:
            raise ValueError(
                "ReceiptWordLabels parameter is required and cannot be None."
            )
        if not isinstance(receipt_word_labels, list):
            raise ValueError(
                "receipt_word_labels must be a list of ReceiptWordLabel instances."
            )
        if not all(
            isinstance(label, ReceiptWordLabel)
            for label in receipt_word_labels
        ):
            raise ValueError(
                "All receipt word labels must be instances of the ReceiptWordLabel class."
            )

        try:
            # Process labels in chunks of 25 items (the maximum allowed per
            # transaction)
            for i in range(0, len(receipt_word_labels), 25):
                chunk = receipt_word_labels[i : i + 25]
                transact_items = []
                for label in chunk:
                    transact_items.append(
                        TransactWriteItemTypeDef(
                            Delete=DeleteTypeDef(
                                TableName=self.table_name,
                                Key=label.key(),
                                ConditionExpression="attribute_exists(PK)",
                            )
                        )
                    )
                # Execute the transaction for this chunk.
                self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "One or more receipt word labels do not exist"
                ) from e
            elif error_code == "TransactionCanceledException":
                if "ConditionalCheckFailed" in str(e):
                    raise ValueError(
                        "One or more receipt word labels do not exist"
                    ) from e
                else:
                    raise DynamoDBError(f"Transaction canceled: {e}") from e
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
                raise DynamoDBError(f"Resource not found: {e}") from e
            else:
                raise DynamoDBError(
                    f"Error deleting receipt word labels: {e}"
                ) from e

    def get_receipt_word_label(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        label: str,
    ) -> ReceiptWordLabel:
        """
        Retrieves a receipt word label from the database.

        Args:
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt.
            line_id (int): The ID of the line containing the word.
            word_id (int): The ID of the word.
            label (str): The label to retrieve.

        Returns:
            ReceiptWordLabel: The receipt word label object.

        Raises:
            ValueError: If input parameters are invalid or if the label does not exist.
            Exception: For underlying DynamoDB errors.
        """
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if receipt_id is None:
            raise ValueError("Receipt ID is required and cannot be None.")
        if line_id is None:
            raise ValueError("Line ID is required and cannot be None.")
        if word_id is None:
            raise ValueError("Word ID is required and cannot be None.")
        if label is None:
            raise ValueError("Label is required and cannot be None.")

        # Validate image_id as a UUID and IDs as positive integers
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")
        if not isinstance(line_id, int) or line_id <= 0:
            raise ValueError("Line ID must be a positive integer.")
        if not isinstance(word_id, int) or word_id <= 0:
            raise ValueError("Word ID must be a positive integer.")
        if not isinstance(label, str) or not label:
            raise ValueError("Label must be a non-empty string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LABEL#{label}"
                    },
                },
            )
            if "Item" in response:
                return item_to_receipt_word_label(response["Item"])
            else:
                raise ValueError(
                    f"Receipt word label for Image ID '{image_id}', Receipt ID {receipt_id}, Line ID {line_id}, Word ID {word_id}, and Label '{label}' does not exist."
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise OperationError(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise OperationError(
                    f"Error getting receipt word label: {e}"
                ) from e

    def get_receipt_word_labels_by_indices(
        self, indices: list[tuple[str, int, int, int, str]]
    ) -> list[ReceiptWordLabel]:
        """Retrieves multiple receipt word labels by their indices."""
        if indices is None:
            raise ValueError("Indices is required and cannot be None.")
        if not isinstance(indices, list):
            raise ValueError("Indices must be a list.")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("Indices must be a list of tuples.")
        for index in indices:
            if len(index) != 5:
                raise ValueError(
                    "Indices must be a list of tuples with 5 elements."
                )
            if not isinstance(index[0], str):
                raise ValueError("First element of tuple must be a string.")
            assert_valid_uuid(index[0])
            if not isinstance(index[1], int):
                raise ValueError("Second element of tuple must be an integer.")
            if not isinstance(index[2], int):
                raise ValueError("Third element of tuple must be an integer.")
            if not isinstance(index[3], int):
                raise ValueError("Fourth element of tuple must be an integer.")
            if not isinstance(index[4], str):
                raise ValueError("Fifth element of tuple must be a string.")

        # Assemble the keys
        keys = []
        for index in indices:
            keys.append(
                {
                    "PK": {"S": f"IMAGE#{index[0]}"},
                    "SK": {
                        "S": f"RECEIPT#{index[1]:05d}#LINE#{index[2]:05d}#WORD#{index[3]:05d}#LABEL#{index[4]}"
                    },
                }
            )
        return self.get_receipt_word_labels_by_keys(keys)

    def get_receipt_word_labels_by_keys(
        self, keys: list[dict]
    ) -> list[ReceiptWordLabel]:
        """Retrieves multiple receipt word labels by their keys."""
        # Check the validity of the keys
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("Keys must contain 'PK' and 'SK'")
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
            if not key["SK"]["S"].split("#")[4] == "WORD":
                raise ValueError("SK must contain 'WORD'")
            if len(key["SK"]["S"].split("#")[5]) != 5:
                raise ValueError("SK must contain a 5-digit word ID")
            if not key["SK"]["S"].split("#")[6] == "LABEL":
                raise ValueError("SK must contain 'LABEL'")

        results = []
        try:
            # Split keys into chunks of up to 100
            for i in range(0, len(keys), 100):
                chunk = keys[i : i + 100]

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
                unprocessed: Dict[str, Any] = response.get(
                    "UnprocessedKeys", {}
                )
                while unprocessed.get(self.table_name, {}).get("Keys"):
                    response = self._client.batch_get_item(
                        RequestItems=unprocessed
                    )
                    batch_items = response["Responses"].get(
                        self.table_name, []
                    )
                    results.extend(batch_items)
                    unprocessed = response.get("UnprocessedKeys", {})

            return [item_to_receipt_word_label(result) for result in results]
        except ClientError as e:
            raise ValueError(
                f"Could not get ReceiptWordLabels from the database: {e}"
            )

    def list_receipt_word_labels(
        self, limit: Optional[int] = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptWordLabel], dict | None]:
        """
        Retrieve receipt word label records from the database with support for precise pagination.

        Parameters:
            limit (int, optional): The maximum number of receipt word label items to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of ReceiptWordLabel objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises:
            ValueError: If the limit is not an integer or is less than or equal to 0.
            ValueError: If the lastEvaluatedKey is not a dictionary.
            Exception: If the underlying database query fails.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        labels: List[ReceiptWordLabel] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_WORD_LABEL"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(labels)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(labels) >= limit:
                    labels = labels[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return labels, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt word labels from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt word labels from the database: {e}"
                ) from e

    def get_receipt_word_labels_by_label(
        self,
        label: str,
        limit: Optional[int] = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict | None]:
        """
        Retrieve receipt word labels by label type using GSI1.

        Args:
            label (str): The label type to search for
            limit (int, optional): The maximum number of labels to return
            lastEvaluatedKey (dict, optional): The key to start the query from

        Returns:
            tuple[list[ReceiptWordLabel], dict | None]: A tuple containing:
                - List of ReceiptWordLabel objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            ValueError: If the label is invalid or if pagination parameters are invalid
            Exception: For underlying DynamoDB errors
        """
        if not isinstance(label, str) or not label:
            raise ValueError("Label must be a non-empty string")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        labels: List[ReceiptWordLabel] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {
                        "S": f"LABEL#{label.upper()}{'_' * (40 - len('LABEL#') - len(label.upper()))}"
                    }
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(labels)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(labels) >= limit:
                    labels = labels[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return labels, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt word labels by label type: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt word labels by label type: {e}"
                ) from e

    def get_receipt_word_labels_for_word(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        limit: int | None = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict | None]:
        """
        Fetch every ReceiptWordLabel attached to a single word.

        Returns (labels, lastEvaluatedKey) so you can page through results.
        """
        # ---------- validation ----------
        assert_valid_uuid(image_id)
        for val, name in [
            (receipt_id, "receipt_id"),
            (line_id, "line_id"),
            (word_id, "word_id"),
        ]:
            if not isinstance(val, int) or val <= 0:
                raise ValueError(f"{name} must be a positive int")

        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("limit must be a positive integer")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("lastEvaluatedKey must be a dict")
            validate_last_evaluated_key(lastEvaluatedKey)

        # ---------- DynamoDB query ----------
        pk = f"IMAGE#{image_id}"
        sk_prefix = (
            f"RECEIPT#{receipt_id:05d}"
            f"#LINE#{line_id:05d}"
            f"#WORD#{word_id:05d}"
            "#LABEL#"
        )

        items: list[ReceiptWordLabel] = []
        params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": {
                ":pk": {"S": pk},
                ":sk": {"S": sk_prefix},
            },
        }
        if lastEvaluatedKey:
            params["ExclusiveStartKey"] = lastEvaluatedKey

        while True:
            if limit is not None:
                params["Limit"] = limit - len(items)
            resp = self._client.query(**params)
            items.extend(item_to_receipt_word_label(i) for i in resp["Items"])

            if limit is not None and len(items) >= limit:
                return items[:limit], resp.get("LastEvaluatedKey")

            if "LastEvaluatedKey" in resp:
                params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            else:
                return items, None

    def get_receipt_word_labels_by_validation_status(
        self,
        validation_status: str,
        limit: Optional[int] = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict | None]:
        """
        Retrieve receipt word labels by validation status using GSI3.

        Args:
            validation_status (str): The validation status to search for
            limit (int, optional): The maximum number of labels to return
            lastEvaluatedKey (dict, optional): The key to start the query from

        Returns:
            tuple[list[ReceiptWordLabel], dict | None]: A tuple containing:
                - List of ReceiptWordLabel objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            ValueError: If the validation status is invalid or if pagination parameters are invalid
            Exception: For underlying DynamoDB errors
        """
        if not isinstance(validation_status, str) or not validation_status:
            raise ValueError("Validation status must be a non-empty string")
        # Check if validation_status is a valid status by comparing against enum values
        valid_statuses = [status.value for status in ValidationStatus]
        if validation_status not in valid_statuses:
            raise ValueError(
                "Validation status must be one of the following: "
                + ", ".join(valid_statuses)
                + f" but got {validation_status}"
            )
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        labels: List[ReceiptWordLabel] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "GSI3PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {
                        "S": f"VALIDATION_STATUS#{validation_status.upper()}"
                    }
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(labels)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(labels) >= limit:
                    labels = labels[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return labels, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt word labels by validation status: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt word labels by validation status: {e}"
                ) from e
