from botocore.exceptions import ClientError

from receipt_dynamo import (
    ReceiptChatGPTValidation,
    itemToReceiptChatGPTValidation,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptChatGPTValidation:
    """
    A class used to access receipt ChatGPT validations in DynamoDB.

    Methods
    -------
    addReceiptChatGPTValidation(validation: ReceiptChatGPTValidation)
        Adds a ReceiptChatGPTValidation to DynamoDB.
    addReceiptChatGPTValidations(validations: list[ReceiptChatGPTValidation])
        Adds multiple ReceiptChatGPTValidations to DynamoDB in batches.
    updateReceiptChatGPTValidation(validation: ReceiptChatGPTValidation)
        Updates an existing ReceiptChatGPTValidation in the database.
    updateReceiptChatGPTValidations(validations: list[ReceiptChatGPTValidation])
        Updates multiple ReceiptChatGPTValidations in the database.
    deleteReceiptChatGPTValidation(validation: ReceiptChatGPTValidation)
        Deletes a single ReceiptChatGPTValidation.
    deleteReceiptChatGPTValidations(validations: list[ReceiptChatGPTValidation])
        Deletes multiple ReceiptChatGPTValidations in batch.
    getReceiptChatGPTValidation(receipt_id: int, image_id: str, timestamp: str) -> ReceiptChatGPTValidation
        Retrieves a single ReceiptChatGPTValidation by IDs.
    listReceiptChatGPTValidations(limit: int = None, lastEvaluatedKey: dict | None = None) -> tuple[list[ReceiptChatGPTValidation], dict | None]
        Returns all ReceiptChatGPTValidations and the last evaluated key.
    listReceiptChatGPTValidationsForReceipt(receipt_id: int, image_id: str) -> list[ReceiptChatGPTValidation]
        Returns all ReceiptChatGPTValidations for a given receipt.
    listReceiptChatGPTValidationsByStatus(status: str, limit: int = None, lastEvaluatedKey: dict | None = None) -> tuple[list[ReceiptChatGPTValidation], dict | None]
        Returns ReceiptChatGPTValidations with a specific status.
    """

    def addReceiptChatGPTValidation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Adds a ReceiptChatGPTValidation to DynamoDB.

        Args:
            validation (ReceiptChatGPTValidation): The ReceiptChatGPTValidation to add.

        Raises:
            ValueError: If the validation is None or not an instance of ReceiptChatGPTValidation.
            Exception: If the validation cannot be added to DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the ReceiptChatGPTValidation class."
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
                    f"ReceiptChatGPTValidation for receipt {validation.receipt_id} and timestamp {validation.timestamp} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt ChatGPT validation to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not add receipt ChatGPT validation to DynamoDB: {e}"
                ) from e

    def addReceiptChatGPTValidations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Adds multiple ReceiptChatGPTValidations to DynamoDB in batches.

        Args:
            validations (list[ReceiptChatGPTValidation]): The ReceiptChatGPTValidations to add.

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
                "validations must be a list of ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the ReceiptChatGPTValidation class."
            )
        try:
            for i in range(0, len(validations), 25):
                chunk = validations[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": val.to_item()}} for val in chunk
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
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not add ReceiptChatGPTValidations to the database: {e}"
                ) from e

    def updateReceiptChatGPTValidation(
        self, validation: ReceiptChatGPTValidation
    ):
        """Updates an existing ReceiptChatGPTValidation in the database.

        Args:
            validation (ReceiptChatGPTValidation): The ReceiptChatGPTValidation to update.

        Raises:
            ValueError: If the validation is None or not an instance of ReceiptChatGPTValidation.
            Exception: If the validation cannot be updated in DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the ReceiptChatGPTValidation class."
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
                    f"ReceiptChatGPTValidation for receipt {validation.receipt_id} and timestamp {validation.timestamp} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not update ReceiptChatGPTValidation in the database: {e}"
                ) from e

    def updateReceiptChatGPTValidations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Updates multiple ReceiptChatGPTValidations in the database.

        Args:
            validations (list[ReceiptChatGPTValidation]): The ReceiptChatGPTValidations to update.

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
                "validations must be a list of ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the ReceiptChatGPTValidation class."
            )
        for i in range(0, len(validations), 25):
            chunk = validations[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": val.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for val in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptChatGPTValidations do not exist"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise Exception(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise Exception(f"Access denied: {e}") from e
                else:
                    raise Exception(
                        f"Could not update ReceiptChatGPTValidations in the database: {e}"
                    ) from e

    def deleteReceiptChatGPTValidation(
        self,
        validation: ReceiptChatGPTValidation,
    ):
        """Deletes a single ReceiptChatGPTValidation.

        Args:
            validation (ReceiptChatGPTValidation): The ReceiptChatGPTValidation to delete.

        Raises:
            ValueError: If the validation is None or not an instance of ReceiptChatGPTValidation.
            Exception: If the validation cannot be deleted from DynamoDB.
        """
        if validation is None:
            raise ValueError(
                "validation parameter is required and cannot be None."
            )
        if not isinstance(validation, ReceiptChatGPTValidation):
            raise ValueError(
                "validation must be an instance of the ReceiptChatGPTValidation class."
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
                    f"ReceiptChatGPTValidation for receipt {validation.receipt_id} and timestamp {validation.timestamp} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptChatGPTValidation from the database: {e}"
                ) from e

    def deleteReceiptChatGPTValidations(
        self, validations: list[ReceiptChatGPTValidation]
    ):
        """Deletes multiple ReceiptChatGPTValidations in batch.

        Args:
            validations (list[ReceiptChatGPTValidation]): The ReceiptChatGPTValidations to delete.

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
                "validations must be a list of ReceiptChatGPTValidation instances."
            )
        if not all(
            isinstance(val, ReceiptChatGPTValidation) for val in validations
        ):
            raise ValueError(
                "All validations must be instances of the ReceiptChatGPTValidation class."
            )
        try:
            for i in range(0, len(validations), 25):
                chunk = validations[i : i + 25]
                request_items = [
                    {"DeleteRequest": {"Key": val.key}} for val in chunk
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
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptChatGPTValidations from the database: {e}"
                ) from e

    def getReceiptChatGPTValidation(
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
            Exception: If the receipt ChatGPT validation cannot be retrieved from DynamoDB.

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
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{timestamp}"
                    },
                },
            )
            if "Item" in response:
                return itemToReceiptChatGPTValidation(response["Item"])
            else:
                raise ValueError(
                    f"ReceiptChatGPTValidation with receipt ID {receipt_id}, image ID {image_id}, and timestamp {timestamp} not found"
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Error getting receipt ChatGPT validation: {e}"
                ) from e

    def listReceiptChatGPTValidations(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]:
        """Returns all ReceiptChatGPTValidations from the table.

        Args:
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptChatGPTValidation], dict | None]: A tuple containing a list of validations and
                                                               the last evaluated key (or None if no more results).
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validations = []
        try:
            # Use GSI1 to query all validations
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk_val AND begins_with(#sk, :sk_prefix)",
                "ExpressionAttributeNames": {"#pk": "GSI1PK", "#sk": "GSI1SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": "ANALYSIS_TYPE"},
                    ":sk_prefix": {"S": "VALIDATION_CHATGPT#"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validations.extend(
                [
                    itemToReceiptChatGPTValidation(item)
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
                            itemToReceiptChatGPTValidation(item)
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
                raise Exception(
                    f"Could not list receipt ChatGPT validations from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Error listing receipt ChatGPT validations: {e}"
                ) from e

    def listReceiptChatGPTValidationsForReceipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptChatGPTValidation]:
        """Returns all ReceiptChatGPTValidations for a given receipt.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be retrieved from DynamoDB.

        Returns:
            list[ReceiptChatGPTValidation]: A list of ChatGPT validations for the specified receipt.
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
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CHATGPT#"
                    },
                },
            )
            validations.extend(
                [
                    itemToReceiptChatGPTValidation(item)
                    for item in response["Items"]
                ]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {
                            "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CHATGPT#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                validations.extend(
                    [
                        itemToReceiptChatGPTValidation(item)
                        for item in response["Items"]
                    ]
                )
            return validations

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not list ReceiptChatGPTValidations from the database: {e}"
                ) from e

    def listReceiptChatGPTValidationsByStatus(
        self,
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptChatGPTValidation], dict | None]:
        """Returns all ReceiptChatGPTValidations with a specific status.

        Args:
            status (str): The status to filter by ("VALID", "INVALID", etc.).
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt ChatGPT validations cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptChatGPTValidation], dict | None]: A tuple containing a list of validations and
                                                               the last evaluated key (or None if no more results).
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
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validations = []
        try:
            # Use GSI3 to query validations by status
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI3PK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"VALIDATION_STATUS#{status}"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validations.extend(
                [
                    itemToReceiptChatGPTValidation(item)
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
                            itemToReceiptChatGPTValidation(item)
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
                raise Exception(
                    f"Could not list receipt ChatGPT validations from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Error listing receipt ChatGPT validations: {e}"
                ) from e
