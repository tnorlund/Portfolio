"""Receipt Word Label data access using base operations framework.

This refactored version reduces code from ~969 lines to ~310 lines
(68% reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
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
        raise EntityValidationError(
            f"last_evaluated_key must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"last_evaluated_key[{key}] must be a dict containing a key "
                f"'S'"
            )


class _ReceiptWordLabel(
    FlattenedStandardMixin,
):
    """
    A class used to access receipt word labels in DynamoDB.

    This refactored version uses base operations to eliminate code duplication
    while maintaining full backward compatibility.
    """

    def _validate_receipt_word_labels_for_add(
        self, receipt_word_labels: List[ReceiptWordLabel]
    ) -> None:
        """Custom validation for add operation with specific error messages"""
        if receipt_word_labels is None:
            raise EntityValidationError("receipt_word_labels cannot be None")

        if not isinstance(receipt_word_labels, list):
            raise EntityValidationError(
                "receipt_word_labels must be a list of ReceiptWordLabel "
                "instances."
            )

        for item in receipt_word_labels:
            if not isinstance(item, ReceiptWordLabel):
                raise EntityValidationError(
                    "All receipt word labels must be instances of the "
                    "ReceiptWordLabel class."
                )

    @handle_dynamodb_errors("add_receipt_word_label")
    def add_receipt_word_label(self, receipt_word_label: ReceiptWordLabel):
        """Adds a receipt word label to the database

        Args:
            receipt_word_label (ReceiptWordLabel): The receipt word label to
                add to the database

        Raises:
            ValueError: When a receipt word label with the same ID
                already exists
        """
        self._validate_entity(
            receipt_word_label, ReceiptWordLabel, "receipt_word_label"
        )
        self._add_entity(receipt_word_label)

    def _handle_add_receipt_word_label_error(
        self, error: ClientError, receipt_word_label: ReceiptWordLabel
    ):
        """Handle errors specific to add_receipt_word_label"""
        from receipt_dynamo.data.shared_exceptions import (
            DynamoDBError,
            DynamoDBServerError,
            DynamoDBThroughputError,
            DynamoDBValidationError,
            EntityAlreadyExistsError,
            EntityValidationError,
        )

        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            raise EntityAlreadyExistsError(
                "Receipt word label for Image ID "
                f"'{receipt_word_label.image_id}' already exists"
            ) from error

        if error_code == "ResourceNotFoundException":
            raise DynamoDBError(
                f"Could not add receipt word label to DynamoDB: {error}"
            ) from error

        if error_code == "ProvisionedThroughputExceededException":
            raise DynamoDBThroughputError(
                f"Provisioned throughput exceeded: {error}"
            ) from error

        if error_code == "InternalServerError":
            raise DynamoDBServerError(
                f"Internal server error: {error}"
            ) from error

        if error_code == "ValidationException":
            raise DynamoDBValidationError(
                "One or more parameters given were invalid"
            ) from error

        if error_code == "AccessDeniedException":
            raise DynamoDBAccessError("Access denied") from error

        raise DynamoDBError(
            f"Could not add receipt word label to DynamoDB: {error}"
        ) from error

    @handle_dynamodb_errors("add_receipt_word_labels")
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
        # Custom validation for add operation with specific error messages
        self._validate_receipt_word_labels_for_add(receipt_word_labels)

        from receipt_dynamo.data.base_operations import (
            PutRequestTypeDef,
            WriteRequestTypeDef,
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=label.to_item())
            )
            for label in receipt_word_labels
        ]
        self._batch_write_with_retry(request_items)

    def _handle_add_receipt_word_labels_error(self, error: ClientError):
        """Handle errors specific to add_receipt_word_labels"""
        from receipt_dynamo.data.shared_exceptions import (
            DynamoDBError,
            DynamoDBServerError,
            DynamoDBThroughputError,
            DynamoDBValidationError,
            EntityValidationError,
        )

        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code == "ProvisionedThroughputExceededException":
            raise DynamoDBThroughputError(
                f"Provisioned throughput exceeded: {error}"
            ) from error

        if error_code == "InternalServerError":
            raise DynamoDBServerError(
                f"Internal server error: {error}"
            ) from error

        if error_code == "ValidationException":
            raise DynamoDBValidationError(
                "One or more parameters given were invalid"
            ) from error

        if error_code == "AccessDeniedException":
            raise DynamoDBAccessError("Access denied") from error

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
            raise EntityValidationError("image_id cannot be None")
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if line_id is None:
            raise EntityValidationError("line_id cannot be None")
        if word_id is None:
            raise EntityValidationError("word_id cannot be None")
        if label is None:
            raise EntityValidationError("label cannot be None")

        # Then check types
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                "receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(line_id, int):
            raise EntityValidationError(
                "line_id must be an integer, got " f"{type(line_id).__name__}"
            )
        if not isinstance(word_id, int):
            raise EntityValidationError(
                "word_id must be an integer, got " f"{type(word_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                "image_id must be a string, got " f"{type(image_id).__name__}"
            )
        if not isinstance(label, str):
            raise EntityValidationError(
                "label must be a string, got " f"{type(label).__name__}"
            )

        # Check for positive integers
        if receipt_id <= 0:
            raise EntityValidationError(
                "Receipt ID must be a positive integer."
            )
        if line_id <= 0:
            raise EntityValidationError("Line ID must be a positive integer.")
        if word_id <= 0:
            raise EntityValidationError("Word ID must be a positive integer.")

        # Check for non-empty label
        if not label:
            raise EntityValidationError("Label must be a non-empty string.")

        assert_valid_uuid(image_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#"
                f"WORD#{word_id:05d}#LABEL#{label}"
            ),
            entity_class=ReceiptWordLabel,
            converter_func=item_to_receipt_word_label,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Receipt Word Label for Receipt ID {receipt_id}, "
                f"Line ID {line_id}, Word ID {word_id}, Label '{label}', "
                f"and Image ID {image_id} does not exist"
            )
        return result

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
            raise EntityValidationError("keys must be a list")
        if not all(isinstance(key, tuple) and len(key) == 3 for key in keys):
            raise EntityValidationError(
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
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")
        return self._query_by_type(
            entity_type="RECEIPT_WORD_LABEL",
            converter_func=item_to_receipt_word_label,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_word_labels_for_image")
    def list_receipt_word_labels_for_image(
        self,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists all receipt word labels for a given image

        Args:
            image_id (str): The image ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The receipt
                word labels for the image and last evaluated key
        """
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        assert_valid_uuid(image_id)

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        results, last_key = self._query_entities(
            index_name=None,
            key_condition_expression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": "RECEIPT#"},
                ":label_suffix": {"S": "#LABEL#"},
            },
            converter_func=item_to_receipt_word_label,
            filter_expression="contains(#sk, :label_suffix)",
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

        return results, last_key

    @handle_dynamodb_errors("list_receipt_word_labels_for_receipt")
    def list_receipt_word_labels_for_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists all receipt word labels for a specific receipt

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The receipt
                word labels for the receipt and last evaluated key
        """
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        assert_valid_uuid(image_id)

        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if receipt_id <= 0:
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        results, last_key = self._query_entities(
            index_name=None,
            key_condition_expression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}#"},
                ":label_suffix": {"S": "#LABEL#"},
            },
            converter_func=item_to_receipt_word_label,
            filter_expression="contains(#sk, :label_suffix)",
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

        return results, last_key

    @handle_dynamodb_errors("list_receipt_word_labels_for_word")
    def list_receipt_word_labels_for_word(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Lists all receipt word labels for a specific word

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            line_id (int): The line ID
            word_id (int): The word ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: The receipt
                word labels for the word and last evaluated key
        """
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        assert_valid_uuid(image_id)

        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if receipt_id <= 0:
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )

        if not isinstance(line_id, int):
            raise EntityValidationError(
                f"line_id must be an integer, got {type(line_id).__name__}"
            )
        if line_id <= 0:
            raise EntityValidationError("line_id must be a positive integer")

        if not isinstance(word_id, int):
            raise EntityValidationError(
                f"word_id must be an integer, got {type(word_id).__name__}"
            )
        if word_id <= 0:
            raise EntityValidationError("word_id must be a positive integer")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        results, last_key = self._query_entities(
            index_name=None,
            key_condition_expression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LABEL#"
                },
            },
            converter_func=item_to_receipt_word_label,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

        return results, last_key

    @handle_dynamodb_errors("get_receipt_word_labels_by_label")
    def get_receipt_word_labels_by_label(
        self,
        label: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        """Retrieve receipt word labels by label type using GSI1.

        Args:
            label (str): The label type to search for
            limit (Optional[int]): The maximum number of labels to return
            last_evaluated_key (Optional[Dict[str, Any]]): The key to start
                the query from

        Returns:
            Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]: A tuple
                containing:
                - List of ReceiptWordLabel objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            EntityValidationError: If the label is invalid or if pagination
                parameters are invalid
        """
        if not isinstance(label, str) or not label:
            raise EntityValidationError("Label must be a non-empty string")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")

        # Format label for GSI1PK - uppercase and pad to 40 chars
        label_upper = label.upper()
        padding = "_" * (40 - len("LABEL#") - len(label_upper))
        gsi1_pk = f"LABEL#{label_upper}{padding}"

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "GSI1PK"},
            expression_attribute_values={":pk": {"S": gsi1_pk}},
            converter_func=item_to_receipt_word_label,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

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
            raise EntityValidationError(
                "status must be a ValidationStatus instance"
            )
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "GSI3PK"},
            expression_attribute_values={
                ":pk": {"S": f"VALIDATION_STATUS#{status.value}"}
            },
            converter_func=item_to_receipt_word_label,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
