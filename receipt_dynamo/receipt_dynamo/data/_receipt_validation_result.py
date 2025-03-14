from botocore.exceptions import ClientError

from receipt_dynamo import (
    ReceiptValidationResult,
    itemToReceiptValidationResult,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptValidationResult:
    """
    A class used to access receipt validation results in DynamoDB.

    Methods
    -------
    addReceiptValidationResult(result: ReceiptValidationResult)
        Adds a ReceiptValidationResult to DynamoDB.
    addReceiptValidationResults(results: list[ReceiptValidationResult])
        Adds multiple ReceiptValidationResults to DynamoDB in batches.
    updateReceiptValidationResult(result: ReceiptValidationResult)
        Updates an existing ReceiptValidationResult in the database.
    updateReceiptValidationResults(results: list[ReceiptValidationResult])
        Updates multiple ReceiptValidationResults in the database.
    deleteReceiptValidationResult(result: ReceiptValidationResult)
        Deletes a single ReceiptValidationResult.
    deleteReceiptValidationResults(results: list[ReceiptValidationResult])
        Deletes multiple ReceiptValidationResults in batch.
    getReceiptValidationResult(
        receipt_id: int,
        image_id: str,
        field_name: str,
        result_index: int
    ) -> ReceiptValidationResult:
        Retrieves a single ReceiptValidationResult by IDs.
    listReceiptValidationResults(
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        Returns ReceiptValidationResults and the last evaluated key.
    listReceiptValidationResultsForField(
        receipt_id: int,
        image_id: str,
        field_name: str
    ) -> list[ReceiptValidationResult]:
        Returns all ReceiptValidationResults for a given field.
    """

    def addReceiptValidationResult(self, result: ReceiptValidationResult):
        """Adds a ReceiptValidationResult to DynamoDB.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to add.

        Raises:
            ValueError: If the result is None or not an instance of ReceiptValidationResult.
            Exception: If the result cannot be added to DynamoDB.
        """
        if result is None:
            raise ValueError(
                "result parameter is required and cannot be None."
            )
        if not isinstance(result, ReceiptValidationResult):
            raise ValueError(
                "result must be an instance of the ReceiptValidationResult class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationResult with field {result.field_name} and index {result.result_index} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt validation result to DynamoDB: {e}"
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
                    f"Could not add receipt validation result to DynamoDB: {e}"
                ) from e

    def addReceiptValidationResults(
        self, results: list[ReceiptValidationResult]
    ):
        """Adds multiple ReceiptValidationResults to DynamoDB in batches.

        Args:
            results (list[ReceiptValidationResult]): The ReceiptValidationResults to add.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be added to DynamoDB.
        """
        if results is None:
            raise ValueError(
                "results parameter is required and cannot be None."
            )
        if not isinstance(results, list):
            raise ValueError(
                "results must be a list of ReceiptValidationResult instances."
            )
        if not all(
            isinstance(res, ReceiptValidationResult) for res in results
        ):
            raise ValueError(
                "All results must be instances of the ReceiptValidationResult class."
            )
        try:
            for i in range(0, len(results), 25):
                chunk = results[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": res.to_item()}} for res in chunk
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
                    f"Could not add ReceiptValidationResults to the database: {e}"
                ) from e

    def updateReceiptValidationResult(self, result: ReceiptValidationResult):
        """Updates an existing ReceiptValidationResult in the database.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to update.

        Raises:
            ValueError: If the result is None or not an instance of ReceiptValidationResult.
            Exception: If the result cannot be updated in DynamoDB.
        """
        if result is None:
            raise ValueError(
                "result parameter is required and cannot be None."
            )
        if not isinstance(result, ReceiptValidationResult):
            raise ValueError(
                "result must be an instance of the ReceiptValidationResult class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationResult with field {result.field_name} and index {result.result_index} does not exist"
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
                    f"Could not update ReceiptValidationResult in the database: {e}"
                ) from e

    def updateReceiptValidationResults(
        self, results: list[ReceiptValidationResult]
    ):
        """Updates multiple ReceiptValidationResults in the database.

        Args:
            results (list[ReceiptValidationResult]): The ReceiptValidationResults to update.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be updated in DynamoDB.
        """
        if results is None:
            raise ValueError(
                "results parameter is required and cannot be None."
            )
        if not isinstance(results, list):
            raise ValueError(
                "results must be a list of ReceiptValidationResult instances."
            )
        if not all(
            isinstance(res, ReceiptValidationResult) for res in results
        ):
            raise ValueError(
                "All results must be instances of the ReceiptValidationResult class."
            )
        for i in range(0, len(results), 25):
            chunk = results[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": res.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for res in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptValidationResults do not exist"
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
                        f"Could not update ReceiptValidationResults in the database: {e}"
                    ) from e

    def deleteReceiptValidationResult(
        self,
        result: ReceiptValidationResult,
    ):
        """Deletes a single ReceiptValidationResult.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to delete.

        Raises:
            ValueError: If the result is None or not an instance of ReceiptValidationResult.
            Exception: If the result cannot be deleted from DynamoDB.
        """
        if result is None:
            raise ValueError(
                "result parameter is required and cannot be None."
            )
        if not isinstance(result, ReceiptValidationResult):
            raise ValueError(
                "result must be an instance of the ReceiptValidationResult class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=result.key,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationResult with field {result.field_name} and index {result.result_index} does not exist"
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
                    f"Could not delete ReceiptValidationResult from the database: {e}"
                ) from e

    def deleteReceiptValidationResults(
        self, results: list[ReceiptValidationResult]
    ):
        """Deletes multiple ReceiptValidationResults in batch.

        Args:
            results (list[ReceiptValidationResult]): The ReceiptValidationResults to delete.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be deleted from DynamoDB.
        """
        if results is None:
            raise ValueError(
                "results parameter is required and cannot be None."
            )
        if not isinstance(results, list):
            raise ValueError(
                "results must be a list of ReceiptValidationResult instances."
            )
        if not all(
            isinstance(res, ReceiptValidationResult) for res in results
        ):
            raise ValueError(
                "All results must be instances of the ReceiptValidationResult class."
            )
        try:
            for i in range(0, len(results), 25):
                chunk = results[i : i + 25]
                request_items = [
                    {"DeleteRequest": {"Key": res.key}} for res in chunk
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
                    f"Could not delete ReceiptValidationResults from the database: {e}"
                ) from e

    def getReceiptValidationResult(
        self,
        receipt_id: int,
        image_id: str,
        field_name: str,
        result_index: int,
    ) -> ReceiptValidationResult:
        """Retrieves a single ReceiptValidationResult by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            field_name (str): The field name.
            result_index (int): The result index.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation result cannot be retrieved from DynamoDB.

        Returns:
            ReceiptValidationResult: The retrieved receipt validation result.
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
        if field_name is None:
            raise ValueError(
                "field_name parameter is required and cannot be None."
            )
        if not isinstance(field_name, str):
            raise ValueError("field_name must be a string.")
        if not field_name:
            raise ValueError("field_name must not be empty.")
        if result_index is None:
            raise ValueError(
                "result_index parameter is required and cannot be None."
            )
        if not isinstance(result_index, int):
            raise ValueError("result_index must be an integer.")
        if result_index < 0:
            raise ValueError("result_index must be non-negative.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#{result_index}"
                    },
                },
            )
            if "Item" in response:
                return itemToReceiptValidationResult(response["Item"])
            else:
                raise ValueError(
                    f"ReceiptValidationResult with field {field_name} and index {result_index} not found"
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
                    f"Error getting receipt validation result: {e}"
                ) from e

    def listReceiptValidationResults(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        """Returns all ReceiptValidationResults from the table.

        Args:
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation results cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationResult], dict | None]: A tuple containing a list of validation results and
                                                               the last evaluated key (or None if no more results).
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_results = []
        try:
            # Use GSI1 to query all validation results
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk_val AND begins_with(#sk, :sk_prefix)",
                "ExpressionAttributeNames": {"#pk": "GSI1PK", "#sk": "GSI1SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": "ANALYSIS_TYPE"},
                    ":sk_prefix": {"S": "VALIDATION#"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_results.extend(
                [
                    itemToReceiptValidationResult(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the validation results.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validation_results.extend(
                        [
                            itemToReceiptValidationResult(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_results, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation results from DynamoDB: {e}"
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
                    f"Error listing receipt validation results: {e}"
                ) from e

    def listReceiptValidationResultsForField(
        self, receipt_id: int, image_id: str, field_name: str
    ) -> list[ReceiptValidationResult]:
        """Returns all ReceiptValidationResults for a given field.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            field_name (str): The field name.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation results cannot be retrieved from DynamoDB.

        Returns:
            list[ReceiptValidationResult]: A list of validation results for the specified field.
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
        if field_name is None:
            raise ValueError(
                "field_name parameter is required and cannot be None."
            )
        if not isinstance(field_name, str):
            raise ValueError("field_name must be a string.")
        if not field_name:
            raise ValueError("field_name must not be empty.")

        validation_results = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#"
                    },
                },
            )
            validation_results.extend(
                [
                    itemToReceiptValidationResult(item)
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
                            "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                validation_results.extend(
                    [
                        itemToReceiptValidationResult(item)
                        for item in response["Items"]
                    ]
                )
            return validation_results

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
                    f"Could not list ReceiptValidationResults from the database: {e}"
                ) from e

    def listReceiptValidationResultsByType(
        self,
        result_type: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        """Returns all ReceiptValidationResults of a specific type.

        Args:
            result_type (str): The type of validation results to retrieve.
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation results cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationResult], dict | None]: A tuple containing a list of validation results and
                                                               the last evaluated key (or None if no more results).
        """
        if result_type is None:
            raise ValueError(
                "result_type parameter is required and cannot be None."
            )
        if not isinstance(result_type, str):
            raise ValueError("result_type must be a string.")
        if not result_type:
            raise ValueError("result_type must not be empty.")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_results = []
        try:
            # Use GSI3 to query validation results by type
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI3PK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"RESULT_TYPE#{result_type}"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_results.extend(
                [
                    itemToReceiptValidationResult(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the validation results.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validation_results.extend(
                        [
                            itemToReceiptValidationResult(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_results, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation results from DynamoDB: {e}"
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
                    f"Error listing receipt validation results: {e}"
                ) from e
