from typing import TYPE_CHECKING, Optional

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import (
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_chatgpt_validation import (
    ReceiptChatGPTValidation,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


class _ReceiptChatGPTValidation(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    """
    A class used to access receipt ChatGPT validations in DynamoDB.

    Methods
    -------
    add_receipt_chat_gpt_validation(validation: ReceiptChatGPTValidation)
        Adds a ReceiptChatGPTValidation to DynamoDB.
    add_receipt_chat_gpt_validations(
        validations: list[ReceiptChatGPTValidation],
    )
        Adds multiple ReceiptChatGPTValidations to DynamoDB in batches.
    update_receipt_chat_gpt_validation(validation: ReceiptChatGPTValidation)
        Updates an existing ReceiptChatGPTValidation in the database.
    update_receipt_chat_gpt_validations(
        validations: list[ReceiptChatGPTValidation],
    )
        Updates multiple ReceiptChatGPTValidations in the database.
    delete_receipt_chat_gpt_validation(validation: ReceiptChatGPTValidation)
        Deletes a single ReceiptChatGPTValidation.
    delete_receipt_chat_gpt_validations(
        validations: list[ReceiptChatGPTValidation],
    )
        Deletes multiple ReceiptChatGPTValidations in batch.
    get_receipt_chat_gpt_validation(
        receipt_id: int,
        image_id: str,
        timestamp: str,
    ) -> ReceiptChatGPTValidation
        Retrieves a single ReceiptChatGPTValidation by IDs.
    list_receipt_chat_gpt_validations(
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]
        Returns all ReceiptChatGPTValidations and the last evaluated key.
    list_receipt_chat_gpt_validations_for_receipt(
        receipt_id: int,
        image_id: str,
    ) -> list[ReceiptChatGPTValidation]
        Returns all ReceiptChatGPTValidations for a given receipt.
    list_receipt_chat_gpt_validations_by_status(
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]
        Returns ReceiptChatGPTValidations with a specific status."""

    @handle_dynamodb_errors("add_receipt_chat_gpt_validation")
    def add_receipt_chat_gpt_validation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Adds a ReceiptChatGPTValidation to DynamoDB.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to add.

        Raises:
            EntityAlreadyExistsError: If the validation already exists.
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity(
            validation, ReceiptChatGPTValidation, "validation"
        )
        self._add_entity(
            validation, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipt_chatgpt_validations")
    def add_receipt_chatgpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Adds multiple ReceiptChatGPTValidations to DynamoDB in batches.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to add.

        Raises:
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity_list(
            validations, ReceiptChatGPTValidation, "validations"
        )
        # Create write request items for batch operation
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=validation.to_item())
            )
            for validation in validations
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_chatgpt_validation")
    def update_receipt_chatgpt_validation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Updates an existing ReceiptChatGPTValidation in the database.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to update.

        Raises:
            EntityNotFoundError: If the validation does not exist.
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity(
            validation, ReceiptChatGPTValidation, "validation"
        )
        self._update_entity(
            validation, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_receipt_chatgpt_validations")
    def update_receipt_chatgpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Updates multiple ReceiptChatGPTValidations in the database.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to update.

        Raises:
            EntityNotFoundError: If one or more validations do not exist.
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity_list(
            validations, ReceiptChatGPTValidation, "validations"
        )
        # Create transactional update items
        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=validation.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for validation in validations
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_chat_gpt_validation")
    def delete_receipt_chat_gpt_validation(
        self,
        validation: ReceiptChatGPTValidation,
    ):
        """Deletes a single ReceiptChatGPTValidation.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to delete.

        Raises:
            EntityNotFoundError: If the validation does not exist.
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity(
            validation, ReceiptChatGPTValidation, "validation"
        )
        self._delete_entity(
            validation, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_receipt_chat_gpt_validations")
    def delete_receipt_chat_gpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Deletes multiple ReceiptChatGPTValidations in batch.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to delete.

        Raises:
            EntityValidationError: If validation parameters are invalid.
        """
        self._validate_entity_list(
            validations, ReceiptChatGPTValidation, "validations"
        )
        # Create delete request items for batch operation
        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=validation.key)
            )
            for validation in validations
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_chat_gpt_validation")
    def get_receipt_chat_gpt_validation(
        self,
        receipt_id: int,
        image_id: str,
        timestamp: str,
    ) -> ReceiptChatGPTValidation:
        """Retrieves a single ReceiptChatGPTValidation by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            timestamp (str): The validation timestamp.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validation cannot be
                retrieved from DynamoDB.

        Returns:
            ReceiptChatGPTValidation: The retrieved receipt ChatGPT validation.
        """
        self._validate_receipt_id(receipt_id)
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer.")
        self._validate_image_id(image_id)
        if timestamp is None:
            raise EntityValidationError("timestamp cannot be None")
        if not isinstance(timestamp, str):
            raise EntityValidationError("timestamp must be a string.")

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#"
                f"CHATGPT#{timestamp}"
            ),
            entity_class=ReceiptChatGPTValidation,
            converter_func=item_to_receipt_chat_gpt_validation,
        )

        if result is None:
            raise EntityNotFoundError(
                (
                    "ReceiptChatGPTValidation with receipt ID "
                    f"{receipt_id}, image ID {image_id}, and "
                    f"timestamp {timestamp} not found"
                )
            )

        return result

    @handle_dynamodb_errors("list_receipt_chat_gpt_validations")
    def list_receipt_chat_gpt_validations(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]:
        """Returns all ReceiptChatGPTValidations from the table.

        Args:
            limit (int, optional):
                The maximum number of results to return. Defaults to None.
            last_evaluated_key (dict, optional):
                The last evaluated key from a previous request.
                Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be
                retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptChatGPTValidation], dict | None]:
                A tuple containing a list of validations and the last
                evaluated key (or None if no more results).
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression=(
                "#pk = :pk_val AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names={"#pk": "GSI1PK", "#sk": "GSI1SK"},
            expression_attribute_values={
                ":pk_val": {"S": "ANALYSIS_TYPE"},
                ":sk_prefix": {"S": "VALIDATION_CHATGPT#"},
            },
            converter_func=item_to_receipt_chat_gpt_validation,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_chat_gpt_validations_for_receipt")
    def list_receipt_chat_gpt_validations_for_receipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptChatGPTValidation]:
        """Returns all ReceiptChatGPTValidations for a given receipt.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be
                retrieved from DynamoDB.

        Returns:
            list[ReceiptChatGPTValidation]:
                A list of ChatGPT validations for the specified receipt.
        """
        self._validate_receipt_id(receipt_id)
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer.")
        self._validate_image_id(image_id)

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression=(
                "PK = :pkVal AND begins_with(SK, :skPrefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#ANALYSIS#"
                        f"VALIDATION#CHATGPT#"
                    )
                },
            },
            converter_func=item_to_receipt_chat_gpt_validation,
        )
        return results

    @handle_dynamodb_errors("list_receipt_chat_gpt_validations_by_status")
    def list_receipt_chat_gpt_validations_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]:
        """Returns all ReceiptChatGPTValidations with a specific status.

        Args:
            status (str): The status to filter by ("VALID", "INVALID", etc.).
            limit (int, optional):
                The maximum number of results to return. Defaults to None.
            last_evaluated_key (dict, optional):
                The last evaluated key from a previous request.
                Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be
                retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptChatGPTValidation], dict | None]:
                A tuple containing a list of validations and the last
                evaluated key (or None if no more results).
        """
        if status is None:
            raise EntityValidationError("status cannot be None")
        if not isinstance(status, str):
            raise EntityValidationError("status must be a string.")
        if not status:
            raise EntityValidationError("status must not be empty.")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="#pk = :pk_val",
            expression_attribute_names={"#pk": "GSI3PK"},
            expression_attribute_values={
                ":pk_val": {"S": f"VALIDATION_STATUS#{status}"},
            },
            converter_func=item_to_receipt_chat_gpt_validation,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
