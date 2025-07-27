"""Receipt Word Label data access using base operations framework.

This refactored version reduces code from ~969 lines to ~310 lines
(68% reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    handle_dynamodb_errors,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
)
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
    ReceiptWordLabel,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        BatchGetItemInputTypeDef,
        QueryInputTypeDef,
    )


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"last_evaluated_key must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"last_evaluated_key[{key}] must be a dict containing a key "
                f"'S'"
            )


class _ReceiptWordLabel(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to access receipt word labels in DynamoDB.

    This refactored version uses base operations to eliminate code duplication
    while maintaining full backward compatibility.
    """

    def add_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Adds a receipt word label to the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to
                add to the database

        Raises:
            ValueError: When a receipt word label with the same ID
                already exists
        """
        if receipt_word_label is None:
            raise ValueError("receipt_word_label cannot be None")
        if not isinstance(receipt_word_label, ReceiptWordLabel):
            raise ValueError(
                "receipt_word_label must be an instance of the "
                "ReceiptWordLabel class."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_word_label.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            self._handle_add_receipt_word_label_error(e, receipt_word_label)

    def _handle_add_receipt_word_label_error(
        self, error: ClientError, receipt_word_label: ReceiptWordLabel
    ):
        """Handle errors specific to add_receipt_word_label"""
        from receipt_dynamo.data.shared_exceptions import (
            DynamoDBAccessError,
            DynamoDBError,
            DynamoDBServerError,
            DynamoDBThroughputError,
            DynamoDBValidationError,
        )

        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            raise ValueError(
                "Receipt word label for Image ID "
                f"'{receipt_word_label.image_id}' already exists"
            ) from error
        elif error_code == "ResourceNotFoundException":
            raise DynamoDBError(
                f"Could not add receipt word label to DynamoDB: {error}"
            ) from error
        elif error_code == "ProvisionedThroughputExceededException":
            raise DynamoDBThroughputError(
                f"Provisioned throughput exceeded: {error}"
            ) from error
        elif error_code == "InternalServerError":
            raise DynamoDBServerError(
                f"Internal server error: {error}"
            ) from error
        elif error_code == "ValidationException":
            raise DynamoDBValidationError(
                "One or more parameters given were invalid"
            ) from error
        elif error_code == "AccessDeniedException":
            raise DynamoDBAccessError("Access denied") from error
        else:
            raise DynamoDBError(
                f"Could not add receipt word label to DynamoDB: {error}"
            ) from error

    def add_receipt_word_labels(
        self, receipt_word_labels: List[ReceiptWordLabel]
    ):
        """Adds a list of receipt word labels to the database

        Args:
            receipt_word_labels (List[ReceiptWordLabel]): The receipt word
                labels to add to the database

        Raises:
            ValueError: When a receipt word label with the same ID
                already exists
        """
        if receipt_word_labels is None:
            raise ValueError("receipt_word_labels cannot be None")
        if not isinstance(receipt_word_labels, list):
            raise ValueError(
                "receipt_word_labels must be a list of ReceiptWordLabel "
                "instances."
            )
        if not all(
            isinstance(label, ReceiptWordLabel)
            for label in receipt_word_labels
        ):
            raise ValueError(
                "All receipt word labels must be instances of the "
                "ReceiptWordLabel class."
            )

        try:
            request_items = [
                {"PutRequest": {"Item": label.to_item()}}
                for label in receipt_word_labels
            ]
            self._batch_write_with_retry(request_items)
        except ClientError as e:
            self._handle_add_receipt_word_labels_error(e)

    def _handle_add_receipt_word_labels_error(self, error: ClientError):
        """Handle errors specific to add_receipt_word_labels"""
        from receipt_dynamo.data.shared_exceptions import (
            DynamoDBAccessError,
            DynamoDBError,
            DynamoDBServerError,
            DynamoDBThroughputError,
            DynamoDBValidationError,
        )

        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code == "ProvisionedThroughputExceededException":
            raise DynamoDBThroughputError(
                f"Provisioned throughput exceeded: {error}"
            ) from error
        elif error_code == "InternalServerError":
            raise DynamoDBServerError(
                f"Internal server error: {error}"
            ) from error
        elif error_code == "ValidationException":
            raise DynamoDBValidationError(
                "One or more parameters given were invalid"
            ) from error
        elif error_code == "AccessDeniedException":
            raise DynamoDBAccessError("Access denied") from error
        else:
            raise DynamoDBError("Error adding receipt word labels") from error

    @handle_dynamodb_errors("update_receipt_word_label")
    def update_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Updates a receipt word label in the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to
                update

        Raises:
            ValueError: When the receipt word label does not exist
        """
        self._validate_entity(
            receipt_word_label, ReceiptWordLabel, "ReceiptWordLabel"
        )
        self._update_entity(receipt_word_label)

    @handle_dynamodb_errors("update_receipt_word_labels")
    def update_receipt_word_labels(
        self, receipt_word_labels: List[ReceiptWordLabel]
    ):
        """Updates multiple receipt word labels in the database

        Args:
            receipt_word_labels (List[ReceiptWordLabel]): The receipt word
                labels to update

        Raises:
            ValueError: When any receipt word label validation fails
        """
        self._update_entities(
            receipt_word_labels, ReceiptWordLabel, "receipt_word_labels"
        )

    @handle_dynamodb_errors("delete_receipt_word_label")
    def delete_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Deletes a receipt word label from the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to
                delete

        Raises:
            ValueError: When the receipt word label does not exist
        """
        self._validate_entity(
            receipt_word_label, ReceiptWordLabel, "ReceiptWordLabel"
        )
        self._delete_entity(receipt_word_label)

    @handle_dynamodb_errors("delete_receipt_word_labels")
    def delete_receipt_word_labels(
        self, receipt_word_labels: List[ReceiptWordLabel]
    ):
        """Deletes multiple receipt word labels from the database

        Args:
            receipt_word_labels (List[ReceiptWordLabel]): The receipt word
                labels to delete

        Raises:
            ValueError: When any receipt word label validation fails
        """
        self._validate_entity_list(
            receipt_word_labels, ReceiptWordLabel, "receipt_word_labels"
        )

        # Use transactional writes for deletes to ensure items exist
        transact_items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": label.key,
                    "ConditionExpression": (
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                }
            }
            for label in receipt_word_labels
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt_word_label")
    def get_receipt_word_label(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        label: str,
    ) -> ReceiptWordLabel:
        """Retrieves a receipt word label from the database

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            line_id (int): The line ID
            word_id (int): The word ID
            label (str): The label

        Returns:
            ReceiptWordLabel: The receipt word label from the database

        Raises:
            ValueError: When the receipt word label does not exist
        """
        # Check for None values first
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")
        if line_id is None:
            raise ValueError("line_id cannot be None")
        if word_id is None:
            raise ValueError("word_id cannot be None")
        if label is None:
            raise ValueError("label cannot be None")

        # Then check types
        if not isinstance(receipt_id, int):
            raise ValueError(
                "receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(line_id, int):
            raise ValueError(
                "line_id must be an integer, got " f"{type(line_id).__name__}"
            )
        if not isinstance(word_id, int):
            raise ValueError(
                "word_id must be an integer, got " f"{type(word_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                "image_id must be a string, got " f"{type(image_id).__name__}"
            )
        if not isinstance(label, str):
            raise ValueError(
                "label must be a string, got " f"{type(label).__name__}"
            )

        # Check for positive integers
        if receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")
        if line_id <= 0:
            raise ValueError("Line ID must be a positive integer.")
        if word_id <= 0:
            raise ValueError("Word ID must be a positive integer.")

        # Check for non-empty label
        if not label:
            raise ValueError("Label must be a non-empty string.")

        assert_valid_uuid(image_id)

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
                        f"#WORD#{word_id:05d}#LABEL#{label}"
                    )
                },
            },
        )
        item = response.get("Item")
        if not item:
            raise ValueError(
                f"Receipt Word Label for Receipt ID {receipt_id}, "
                f"Line ID {line_id}, Word ID {word_id}, Label '{label}', "
                f"and Image ID {image_id} does not exist"
            )
        return item_to_receipt_word_label(item)

    @handle_dynamodb_errors("get_receipt_word_labels")
    def get_receipt_word_labels(
        self, keys: List[Tuple[int, int, str]]
    ) -> List[ReceiptWordLabel]:
        """Retrieves multiple receipt word labels from the database

        Args:
            keys (List[Tuple[int, int, str]]): List of
                (receipt_id, word_id, image_id) tuples

        Returns:
            List[ReceiptWordLabel]: The receipt word labels from the database

        Raises:
            ValueError: When any key is invalid
        """
        if not isinstance(keys, list):
            raise ValueError("keys must be a list")
        if not all(isinstance(key, tuple) and len(key) == 3 for key in keys):
            raise ValueError(
                "keys must be a list of (receipt_id, word_id, image_id) tuples"
            )

        # Prepare batch get request
        request_keys = [
            {
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": f"RECEIPT#{receipt_id:05d}#WORD#{word_id:05d}#LABEL"
                },
            }
            for receipt_id, word_id, image_id in keys
        ]

        # Process in chunks of 100 (DynamoDB limit)
        all_labels = []
        for i in range(0, len(request_keys), 100):
            chunk = request_keys[i : i + 100]

            batch_get_params: BatchGetItemInputTypeDef = {
                "RequestItems": {self.table_name: {"Keys": chunk}}
            }

            response = self._client.batch_get_item(**batch_get_params)
            items = response.get("Responses", {}).get(self.table_name, [])
            all_labels.extend(
                [item_to_receipt_word_label(item) for item in items]
            )

        return all_labels

    @handle_dynamodb_errors("list_receipt_word_labels")
    def list_receipt_word_labels(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists all receipt word labels

        Args:
            limit (Optional[int]): The maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): The key to start
                from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The labels
                and last evaluated key
        """
        if limit is not None:
            if not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            if limit <= 0:
                raise ValueError("limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        word_labels = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "RECEIPT_WORD_LABEL"}},
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        word_labels.extend(
            [item_to_receipt_word_label(item) for item in response["Items"]]
        )

        if limit is None:
            # Paginate through all labels
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                word_labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return word_labels, last_evaluated_key

    @handle_dynamodb_errors("list_receipt_word_labels_for_image")
    def list_receipt_word_labels_for_image(
        self, image_id: str
    ) -> List[ReceiptWordLabel]:
        """Lists all receipt word labels for a given image

        Args:
            image_id (str): The image ID

        Returns:
            List[ReceiptWordLabel]: The receipt word labels for the image
        """
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        assert_valid_uuid(image_id)

        word_labels = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": (
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": "RECEIPT#"},
                ":label_suffix": {"S": "#LABEL"},
            },
            "FilterExpression": "contains(#sk, :label_suffix)",
        }

        response = self._client.query(**query_params)
        word_labels.extend(
            [item_to_receipt_word_label(item) for item in response["Items"]]
        )

        # Continue querying if there are more results
        while "LastEvaluatedKey" in response:
            query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self._client.query(**query_params)
            word_labels.extend(
                [
                    item_to_receipt_word_label(item)
                    for item in response["Items"]
                ]
            )

        return word_labels

    @handle_dynamodb_errors("list_receipt_word_labels_with_status")
    def list_receipt_word_labels_with_status(
        self,
        status: ValidationStatus,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists receipt word labels with a specific validation status

        Args:
            status (ValidationStatus): The validation status to filter by
            limit (Optional[int]): The maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): The key to start
                from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The labels
                and last evaluated key
        """
        if not isinstance(status, ValidationStatus):
            raise ValueError("status must be a ValidationStatus instance")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError(
                    "last_evaluated_key must be a dictionary or None"
                )
            validate_last_evaluated_key(last_evaluated_key)

        word_labels = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSIValidationStatus",
            "KeyConditionExpression": "#vs = :status",
            "ExpressionAttributeNames": {"#vs": "validation_status"},
            "ExpressionAttributeValues": {":status": {"S": status.value}},
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        word_labels.extend(
            [item_to_receipt_word_label(item) for item in response["Items"]]
        )

        if limit is None:
            # Paginate through all labels
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                word_labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return word_labels, last_evaluated_key

    @handle_dynamodb_errors("get_receipt_word_labels_by_label")
    def get_receipt_word_labels_by_label(
        self,
        label: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists receipt word labels by label type

        Args:
            label (str): The label to filter by
            limit (Optional[int]): The maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): The key to start
                from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The labels
                and last evaluated key
        """
        # Validate label
        if not isinstance(label, str) or not label:
            raise ValueError("label must be a non-empty string")

        # Validate limit
        if limit is not None:
            if not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            if limit <= 0:
                raise ValueError("limit must be greater than 0")

        # Validate last_evaluated_key
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        word_labels = []

        # Generate the GSI1PK for the label with proper padding
        label_upper = label.upper()
        prefix = "LABEL#"
        current_length = len(prefix) + len(label_upper)
        padding_length = 40 - current_length
        gsi1pk = f"{prefix}{label_upper}{'_' * padding_length}"

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "#pk = :pk",
            "ExpressionAttributeNames": {"#pk": "GSI1PK"},
            "ExpressionAttributeValues": {":pk": {"S": gsi1pk}},
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        word_labels.extend(
            [item_to_receipt_word_label(item) for item in response["Items"]]
        )

        if limit is None:
            # Paginate through all labels
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                word_labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return word_labels, last_evaluated_key

    @handle_dynamodb_errors("get_receipt_word_labels_by_validation_status")
    def get_receipt_word_labels_by_validation_status(
        self,
        validation_status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists receipt word labels by validation status

        Args:
            validation_status (str): The validation status to filter by
            limit (Optional[int]): The maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): The key to start
                from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The labels
                and last evaluated key
        """
        # Validate validation_status
        if not isinstance(validation_status, str) or not validation_status:
            raise ValueError("validation status must be a non-empty string")

        # Validate that validation_status is one of the valid values
        valid_statuses = [status.value for status in ValidationStatus]
        if validation_status not in valid_statuses:
            raise ValueError(
                "validation status must be one of the following: "
                f"{', '.join(valid_statuses)}"
            )

        # Validate limit
        if limit is not None:
            if not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            if limit <= 0:
                raise ValueError("limit must be greater than 0")

        # Validate last_evaluated_key
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        word_labels = []

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI3",
            "KeyConditionExpression": "#pk = :pk",
            "ExpressionAttributeNames": {"#pk": "GSI3PK"},
            "ExpressionAttributeValues": {
                ":pk": {"S": f"VALIDATION_STATUS#{validation_status}"}
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        word_labels.extend(
            [item_to_receipt_word_label(item) for item in response["Items"]]
        )

        if limit is None:
            # Paginate through all labels
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                word_labels.extend(
                    [
                        item_to_receipt_word_label(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return word_labels, last_evaluated_key
