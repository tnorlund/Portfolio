from typing import TYPE_CHECKING, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities import (
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_chatgpt_validation import (
    ReceiptChatGPTValidation,
)

if TYPE_CHECKING:
    from receipt_dynamo.data._base import QueryInputTypeDef

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
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptChatGPTValidation(DynamoClientProtocol):
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

    def add_receipt_chat_gpt_validation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Adds a ReceiptChatGPTValidation to DynamoDB.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to add.

        Raises:
            ValueError: If the validation is None or not an instance of
                ReceiptChatGPTValidation.
            Exception: If the validation cannot be added to DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the "
                "ReceiptChatGPTValidation class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=validation.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    (
                        "ReceiptChatGPTValidation for receipt "
                        f"{validation.receipt_id} and "
                        f"timestamp {validation.timestamp} already exists"
                    )
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    (
                        "Could not add receipt ChatGPT validation to "
                        f"DynamoDB: {e}"
                    )
                ) from e
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
                raise DynamoDBError(
                    (
                        "Could not add receipt ChatGPT validation to "
                        f"DynamoDB: {e}"
                    )
                ) from e

    def add_receipt_chatgpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Adds multiple ReceiptChatGPTValidations to DynamoDB in batches.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to add.

        Raises:
            ValueError: If the validations are None or not a list.
            Exception: If the validations cannot be added to DynamoDB.
        """
        if validations is None:
            raise ValueError(
                "validations parameter is required and cannot be None."
            )
        if not isinstance(validations, list):
            raise ValueError(
                "validations must be a list of "
                "ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the "
                "ReceiptChatGPTValidation class."
            )
        try:
            for i in range(0, len(validations), 25):
                chunk = validations[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=val.to_item())
                    )
                    for val in chunk
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
                raise DynamoDBError(
                    (
                        "Could not add ReceiptChatGPTValidations to the "
                        f"database: {e}"
                    )
                ) from e

    def update_receipt_chatgpt_validation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Updates an existing ReceiptChatGPTValidation in the database.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to update.

        Raises:
            ValueError: If the validation is None or not an instance of
                ReceiptChatGPTValidation.
            Exception: If the validation cannot be updated in DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the "
                "ReceiptChatGPTValidation class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=validation.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    (
                        "ReceiptChatGPTValidation for receipt "
                        f"{validation.receipt_id} and "
                        f"timestamp {validation.timestamp} does not exist"
                    )
                ) from e
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
                raise DynamoDBError(
                    (
                        "Could not update ReceiptChatGPTValidation in the "
                        f"database: {e}"
                    )
                ) from e

    def update_receipt_chatgpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Updates multiple ReceiptChatGPTValidations in the database.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to update.

        Raises:
            ValueError: If the validations are None or not a list.
            Exception: If the validations cannot be updated in DynamoDB.
        """
        if validations is None:
            raise ValueError(
                "validations parameter is required and cannot be None."
            )
        if not isinstance(validations, list):
            raise ValueError(
                "validations must be a list of "
                "ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the "
                "ReceiptChatGPTValidation class."
            )
        for i in range(0, len(validations), 25):
            chunk = validations[i : i + 25]
            transact_items = [
                TransactWriteItemTypeDef(
                    Put=PutTypeDef(
                        TableName=self.table_name,
                        Item=val.to_item(),
                        ConditionExpression="attribute_exists(PK)",
                    )
                )
                for val in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to
                    # conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptChatGPTValidations do not "
                            "exist"
                        ) from e
                    else:
                        raise DynamoDBError(
                            f"Transaction canceled: {e}"
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
                        (
                            "Could not update ReceiptChatGPTValidations "
                            "in the database: "
                            f"{e}"
                        )
                    ) from e

    def delete_receipt_chat_gpt_validation(
        self,
        validation: ReceiptChatGPTValidation,
    ):
        """Deletes a single ReceiptChatGPTValidation.

        Args:
            validation (ReceiptChatGPTValidation):
                The ReceiptChatGPTValidation to delete.

        Raises:
            ValueError: If the validation is None or not an instance of
                ReceiptChatGPTValidation.
            Exception: If the validation cannot be deleted from DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the "
                "ReceiptChatGPTValidation class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=validation.key,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    (
                        "ReceiptChatGPTValidation for receipt "
                        f"{validation.receipt_id} and "
                        f"timestamp {validation.timestamp} does not exist"
                    )
                ) from e
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
                raise DynamoDBError(
                    (
                        "Could not delete ReceiptChatGPTValidation from the "
                        "database"
                    )
                ) from e

    def delete_receipt_chat_gpt_validations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Deletes multiple ReceiptChatGPTValidations in batch.

        Args:
            validations (list[ReceiptChatGPTValidation]):
                The ReceiptChatGPTValidations to delete.

        Raises:
            ValueError: If the validations are None or not a list.
            Exception: If the validations cannot be deleted from DynamoDB.
        """
        if validations is None:
            raise ValueError(
                "validations parameter is required and cannot be None."
            )
        if not isinstance(validations, list):
            raise ValueError(
                "validations must be a list of "
                "ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the "
                "ReceiptChatGPTValidation class."
            )
        try:
            for i in range(0, len(validations), 25):
                chunk = validations[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=val.key)
                    )
                    for val in chunk
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
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise DynamoDBError(
                    (
                        "Could not delete ReceiptChatGPTValidations from the "
                        f"database: {e}"
                    )
                ) from e

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
        if timestamp is None:
            raise ValueError(
                "timestamp parameter is required and cannot be None."
            )
        if not isinstance(timestamp, str):
            raise ValueError("timestamp must be a string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": (
                            f"RECEIPT#{receipt_id:05d}#ANALYSIS#"
                            f"VALIDATION#CHATGPT#"
                            f"{timestamp}"
                        )
                    },
                },
            )
            if "Item" in response:
                return item_to_receipt_chat_gpt_validation(response["Item"])
            else:
                raise ValueError(
                    (
                        "ReceiptChatGPTValidation with receipt ID "
                        f"{receipt_id}, image ID {image_id}, and "
                        f"timestamp {timestamp} not found"
                    )
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
                    f"Error getting receipt ChatGPT validation: {e}"
                ) from e

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
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )

        validations = []
        try:
            # Use GSI1 to query all validations
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": (
                    "#pk = :pk_val AND begins_with(#sk, :sk_prefix)"
                ),
                "ExpressionAttributeNames": {"#pk": "GSI1PK", "#sk": "GSI1SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": "ANALYSIS_TYPE"},
                    ":sk_prefix": {"S": "VALIDATION_CHATGPT#"},
                },
            }

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validations.extend(
                [
                    item_to_receipt_chat_gpt_validation(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the validations
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validations.extend(
                        [
                            item_to_receipt_chat_gpt_validation(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validations, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    (
                        "Could not list receipt ChatGPT validations from "
                        "DynamoDB: "
                        f"{e}"
                    )
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
                    f"Error listing receipt ChatGPT validations: {e}"
                ) from e

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

        validations = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression=(
                    "PK = :pkVal AND begins_with(SK, :skPrefix)"
                ),
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": (
                            f"RECEIPT#{receipt_id:05d}#ANALYSIS#"
                            f"VALIDATION#CHATGPT#"
                        )
                    },
                },
            )
            validations.extend(
                [
                    item_to_receipt_chat_gpt_validation(item)
                    for item in response["Items"]
                ]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression=(
                        "PK = :pkVal AND begins_with(SK, :skPrefix)"
                    ),
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {
                            "S": (
                                f"RECEIPT#{receipt_id:05d}#ANALYSIS#"
                                f"VALIDATION#CHATGPT#"
                            )
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                validations.extend(
                    [
                        item_to_receipt_chat_gpt_validation(item)
                        for item in response["Items"]
                    ]
                )
            return validations

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise DynamoDBError(
                    (
                        "Could not list ReceiptChatGPTValidations from the "
                        f"database: {e}"
                    )
                ) from e

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
            raise ValueError(
                "status parameter is required and cannot be None."
            )
        if not isinstance(status, str):
            raise ValueError("status must be a string.")
        if not status:
            raise ValueError("status must not be empty.")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )

        validations = []
        try:
            # Use GSI3 to query validations by status
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI3PK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"VALIDATION_STATUS#{status}"},
                },
            }

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validations.extend(
                [
                    item_to_receipt_chat_gpt_validation(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the validations
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validations.extend(
                        [
                            item_to_receipt_chat_gpt_validation(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validations, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    (
                        "Could not list receipt ChatGPT validations from "
                        "DynamoDB: "
                        f"{e}"
                    )
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
                    f"Error listing receipt ChatGPT validations: {e}"
                ) from e
