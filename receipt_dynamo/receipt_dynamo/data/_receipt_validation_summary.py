from botocore.exceptions import ClientError

from receipt_dynamo import (
    ReceiptValidationSummary,
    itemToReceiptValidationSummary,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptValidationSummary:
    """
    A class used to access receipt validation summaries in DynamoDB.

    Methods
    -------
    addReceiptValidationSummary(summary: ReceiptValidationSummary)
        Adds a ReceiptValidationSummary to DynamoDB.
    updateReceiptValidationSummary(summary: ReceiptValidationSummary)
        Updates an existing ReceiptValidationSummary in the database.
    deleteReceiptValidationSummary(receipt_id: int, image_id: str)
        Deletes a ReceiptValidationSummary from DynamoDB.
    getReceiptValidationSummary(receipt_id: int, image_id: str) -> ReceiptValidationSummary
        Gets a ReceiptValidationSummary by receipt_id and image_id.
    listReceiptValidationSummaries(
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationSummary], dict | None]
        Lists all ReceiptValidationSummaries with pagination support.
    listReceiptValidationSummariesByStatus(
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationSummary], dict | None]
        Lists ReceiptValidationSummaries by status with pagination support.
    listReceiptValidationSummariesByReceiptId(
        receipt_id: int
    ) -> list[ReceiptValidationSummary]
        Lists all ReceiptValidationSummaries for a given receipt_id.
    listReceiptValidationSummariesByImageId(
        image_id: str
    ) -> list[ReceiptValidationSummary]
        Lists all ReceiptValidationSummaries for a given image_id.
    """

    def addReceiptValidationSummary(self, summary: ReceiptValidationSummary):
        """Adds a ReceiptValidationSummary to DynamoDB.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary to add.

        Raises:
            ValueError: If the summary is None or not an instance of ReceiptValidationSummary.
            Exception: If the summary cannot be added to DynamoDB.
        """
        if summary is None:
            raise ValueError(
                "summary parameter is required and cannot be None."
            )
        if not isinstance(summary, ReceiptValidationSummary):
            raise ValueError(
                "summary must be an instance of the ReceiptValidationSummary class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=summary.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationSummary for receipt {summary.receipt_id} and image {summary.image_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt validation summary to DynamoDB: {e}"
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
                    f"Could not add receipt validation summary to DynamoDB: {e}"
                ) from e

    def updateReceiptValidationSummary(
        self, summary: ReceiptValidationSummary
    ):
        """Updates an existing ReceiptValidationSummary in the database.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary to update.

        Raises:
            ValueError: If the summary is None or not an instance of ReceiptValidationSummary.
            Exception: If the summary cannot be updated in DynamoDB.
        """
        if summary is None:
            raise ValueError(
                "summary parameter is required and cannot be None."
            )
        if not isinstance(summary, ReceiptValidationSummary):
            raise ValueError(
                "summary must be an instance of the ReceiptValidationSummary class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=summary.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationSummary for receipt {summary.receipt_id} and image {summary.image_id} does not exist"
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
                    f"Could not update ReceiptValidationSummary in the database: {e}"
                ) from e

    def updateReceiptValidationSummaries(
        self, summaries: list[ReceiptValidationSummary]
    ):
        """Updates a list of ReceiptValidationSummaries in the database.

        Args:
            summaries (list[ReceiptValidationSummary]): The ReceiptValidationSummaries to update.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the summaries cannot be updated in DynamoDB.
        """
        if summaries is None:
            raise ValueError(
                "summaries parameter is required and cannot be None."
            )
        if not isinstance(summaries, list):
            raise ValueError("summaries must be a list.")
        if not all(
            isinstance(summary, ReceiptValidationSummary)
            for summary in summaries
        ):
            raise ValueError(
                "All summaries must be instances of the ReceiptValidationSummary class."
            )
        for i in range(0, len(summaries), 25):
            chunk = summaries[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": summary.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for summary in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptValidationSummaries do not exist"
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
                        f"Could not update ReceiptValidationSummaries in the database: {e}"
                    ) from e

    def deleteReceiptValidationSummary(
        self, summary: ReceiptValidationSummary
    ):
        """Deletes a ReceiptValidationSummary from DynamoDB.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary to delete.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the summary cannot be deleted from DynamoDB.
        """
        if summary is None:
            raise ValueError(
                "summary parameter is required and cannot be None."
            )
        if not isinstance(summary, ReceiptValidationSummary):
            raise ValueError(
                "summary must be an instance of the ReceiptValidationSummary class."
            )

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=summary.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationSummary for receipt {summary.receipt_id} and image {summary.image_id} does not exist"
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
                    f"Could not delete ReceiptValidationSummary from the database: {e}"
                ) from e

    def getReceiptValidationSummary(
        self, receipt_id: int, image_id: str
    ) -> ReceiptValidationSummary | None:
        """Gets a ReceiptValidationSummary by receipt_id and image_id.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the summary cannot be retrieved from DynamoDB.

        Returns:
            ReceiptValidationSummary | None: The retrieved receipt validation summary or None if not found.
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

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION"
                    },
                },
            )
            if "Item" in response:
                return itemToReceiptValidationSummary(response["Item"])
            else:
                return None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception("Provisioned throughput exceeded") from e
            elif error_code == "ValidationException":
                raise Exception(
                    "One or more parameters given were invalid"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception("Internal server error") from e
            elif error_code == "AccessDeniedException":
                raise Exception("Access denied") from e
            else:
                raise Exception(
                    "Could not retrieve ReceiptValidationSummary from the database"
                ) from e

    def listReceiptValidationSummaries(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationSummary], dict | None]:
        """Lists all ReceiptValidationSummaries with pagination support.

        Args:
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the validation summaries cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationSummary], dict | None]: A tuple containing a list of validation summaries and
                                                               the last evaluated key (or None if no more results).
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_summaries = []
        try:
            # Use GSITYPE to query all validation summaries
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_VALIDATION_SUMMARY"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_summaries.extend(
                [
                    itemToReceiptValidationSummary(item)
                    for item in response["Items"]
                    if not item["SK"]["S"].endswith("#CATEGORY")
                    and not "#RESULT#" in item["SK"]["S"]
                    and not "#CHATGPT#" in item["SK"]["S"]
                ]
            )

            if limit is None:
                # Paginate through all the validation summaries
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validation_summaries.extend(
                        [
                            itemToReceiptValidationSummary(item)
                            for item in response["Items"]
                            if not item["SK"]["S"].endswith("#CATEGORY")
                            and not "#RESULT#" in item["SK"]["S"]
                            and not "#CHATGPT#" in item["SK"]["S"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_summaries, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation summaries from DynamoDB: {e}"
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
                    f"Error listing receipt validation summaries: {e}"
                ) from e

    def listReceiptValidationSummariesByStatus(
        self,
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptValidationSummary], dict | None]:
        """Lists ReceiptValidationSummaries by status with pagination support.

        Args:
            status (str): The validation status to filter by.
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the validation summaries cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationSummary], dict | None]: A tuple containing a list of validation summaries and
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

        validation_summaries = []
        try:
            # Use GSI3 to query validation summaries by status
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
            validation_summaries.extend(
                [
                    itemToReceiptValidationSummary(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the validation summaries
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    validation_summaries.extend(
                        [
                            itemToReceiptValidationSummary(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_summaries, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation summaries from DynamoDB: {e}"
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
                    f"Error listing receipt validation summaries by status: {e}"
                ) from e
