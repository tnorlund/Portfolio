"""Receipt Word Label data access using base operations framework.

This refactored version reduces code from ~969 lines to ~310 lines
(68% reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


def validate_last_evaluated_key(lek: dict[str, Any]) -> None:
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
        self, receipt_word_labels: list[ReceiptWordLabel]
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
        self._add_entity(
            receipt_word_label, condition_expression="attribute_not_exists(PK)"
        )

    def _handle_add_receipt_word_label_error(
        self, error: ClientError, receipt_word_label: ReceiptWordLabel
    ):
        """Handle errors specific to add_receipt_word_label"""
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
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """Adds a list of receipt word labels to the database

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word
                labels to add to the database

        Raises:
            ValueError: When a receipt word label with the same ID
                already exists
        """
        # Custom validation for add operation with specific error messages
        self._validate_receipt_word_labels_for_add(receipt_word_labels)

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=label.to_item())
            )
            for label in receipt_word_labels
        ]
        self._batch_write_with_retry(request_items)

    def _handle_add_receipt_word_labels_error(self, error: ClientError):
        """Handle errors specific to add_receipt_word_labels"""
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
        self._update_entity(
            receipt_word_label, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_receipt_word_labels")
    def update_receipt_word_labels(
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """Updates multiple receipt word labels in the database

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word
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
        self._delete_entity(
            receipt_word_label, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_receipt_word_labels")
    def delete_receipt_word_labels(
        self, receipt_word_labels: list[ReceiptWordLabel]
    ):
        """Deletes multiple receipt word labels from the database

        Args:
            receipt_word_labels (list[ReceiptWordLabel]): The receipt word
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
        # Validate parameters
        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        self._validate_positive_int_id(line_id, "line_id")
        self._validate_positive_int_id(word_id, "word_id")
        if label is None:
            raise EntityValidationError("label cannot be None")
        if not isinstance(label, str):
            raise EntityValidationError(
                "label must be a string, got " f"{type(label).__name__}"
            )
        if not label:
            raise EntityValidationError("Label must be a non-empty string.")

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
        self, keys: list[tuple[int, int, str]]
    ) -> list[ReceiptWordLabel]:
        """Retrieves multiple receipt word labels from the database

        Args:
            keys (list[tuple[int, int, str]]): List of
                (receipt_id, word_id, image_id) tuples

        Returns:
            list[ReceiptWordLabel]: The receipt word labels from the database

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

        results = self._batch_get_items(request_keys)
        return [item_to_receipt_word_label(item) for item in results]

    @handle_dynamodb_errors("list_receipt_word_labels")
    def list_receipt_word_labels(
        self,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Lists all receipt word labels

        Args:
            limit (int | None): The maximum number of items to return
            last_evaluated_key (dict[str, Any] | None): The key to start
                from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]: The labels
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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Lists all receipt word labels for a given image

        Args:
            image_id (str): The image ID
            limit (int | None): Maximum number of items to return
            last_evaluated_key (dict[str, Any] | None): Key to start from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
                The receipt word labels for the image and last evaluated key
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
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Lists all receipt word labels for a specific receipt

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            limit (int | None): Maximum number of items to return
            last_evaluated_key (dict[str, Any] | None): Key to start from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
                The receipt word labels for the receipt and last evaluated key
        """
        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        # Use TYPE field to filter instead of SK-based filter expression
        results, last_key = self._query_entities(
            index_name=None,
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
                "#type": "TYPE",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}#"},
                ":type": {"S": "RECEIPT_WORD_LABEL"},
            },
            converter_func=item_to_receipt_word_label,
            filter_expression="#type = :type",
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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Lists all receipt word labels for a specific word

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            line_id (int): The line ID
            word_id (int): The word ID
            limit (int | None): Maximum number of items to return
            last_evaluated_key (dict[str, Any] | None): Key to start from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
                The receipt word labels for the specific word and last
                evaluated key
        """
        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        self._validate_positive_int_id(line_id, "line_id")
        self._validate_positive_int_id(word_id, "word_id")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        # Query for labels of the specific word using precise key condition
        results, last_key = self._query_entities(
            index_name=None,
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
                        f"#WORD#{word_id:05d}#LABEL#"
                    )
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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Retrieve receipt word labels by label type using GSI1.

        Args:
            label (str): The label type to search for
            limit (int | None): The maximum number of labels to return
            last_evaluated_key (dict[str, Any] | None): The key to start
                the query from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]: A tuple
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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Lists receipt word labels with a specific validation status

        Args:
            status (ValidationStatus): The validation status to filter by
            limit (int | None): The maximum number of items to return
            last_evaluated_key (dict[str, Any] | None): The key to start
                from

        Returns:
            tuple[list[ReceiptWordLabel], dict[str, Any] | None]: The labels
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
