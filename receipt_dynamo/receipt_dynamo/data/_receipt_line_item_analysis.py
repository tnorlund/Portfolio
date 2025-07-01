from typing import TYPE_CHECKING, Any, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo import (
    ReceiptLineItemAnalysis,
    item_to_receipt_line_item_analysis,
)
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        QueryInputTypeDef,
        PutRequestTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
        DeleteRequestTypeDef,
        PutTypeDef,
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


class _ReceiptLineItemAnalysis(DynamoClientProtocol):
    """
    A class used to access receipt line item analyses in DynamoDB.

    Methods
    -------
    add_receipt_line_item_analysis(analysis: ReceiptLineItemAnalysis)
        Adds a ReceiptLineItemAnalysis to DynamoDB.
    add_receipt_line_item_analyses(analyses: list[ReceiptLineItemAnalysis])
        Adds multiple ReceiptLineItemAnalyses to DynamoDB in batches.
    update_receipt_line_item_analysis(analysis: ReceiptLineItemAnalysis)
        Updates an existing ReceiptLineItemAnalysis in the database.
    update_receipt_line_item_analyses(analyses: list[ReceiptLineItemAnalysis])
        Updates multiple ReceiptLineItemAnalyses in the database.
    delete_receipt_line_item_analysis(image_id: str, receipt_id: int)
        Deletes a single ReceiptLineItemAnalysis.
    delete_receipt_line_item_analyses(keys: list[tuple[str, int]])
        Deletes multiple ReceiptLineItemAnalyses in batch.
    get_receipt_line_item_analysis(receipt_id: int, image_id: str) -> ReceiptLineItemAnalysis
        Retrieves a single ReceiptLineItemAnalysis by IDs.
    list_receipt_line_item_analyses(limit: Optional[int] = None, lastEvaluatedKey: dict | None = None) -> tuple[list[ReceiptLineItemAnalysis], dict | None]
        Returns ReceiptLineItemAnalyses and the last evaluated key.
    list_receipt_line_item_analyses_for_image(image_id: str) -> list[ReceiptLineItemAnalysis]
        Returns all ReceiptLineItemAnalyses for a given image.
    """

    def add_receipt_line_item_analysis(
        self, analysis: ReceiptLineItemAnalysis
    ):
        """Adds a ReceiptLineItemAnalysis to DynamoDB.

        Args:
            analysis (ReceiptLineItemAnalysis): The ReceiptLineItemAnalysis to add.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptLineItemAnalysis.
            Exception: If the analysis cannot be added to DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptLineItemAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptLineItemAnalysis class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=analysis.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLineItemAnalysis for receipt ID {analysis.receipt_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add receipt line item analysis to DynamoDB: {e}"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not add receipt line item analysis to DynamoDB: {e}"
                )

    def add_receipt_line_item_analyses(
        self, analyses: list[ReceiptLineItemAnalysis]
    ):
        """Adds multiple ReceiptLineItemAnalyses to DynamoDB in batches.

        Args:
            analyses (list[ReceiptLineItemAnalysis]): The ReceiptLineItemAnalyses to add.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be added to DynamoDB.
        """
        if analyses is None:
            raise ValueError(
                "analyses parameter is required and cannot be None."
            )
        if not isinstance(analyses, list):
            raise ValueError(
                "analyses must be a list of ReceiptLineItemAnalysis instances."
            )
        if not all(isinstance(a, ReceiptLineItemAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptLineItemAnalysis class."
            )
        try:
            for i in range(0, len(analyses), 25):
                chunk = analyses[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=a.to_item())
                    )
                    for a in chunk
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
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not add ReceiptLineItemAnalyses to the database: {e}"
                )

    def update_receipt_line_item_analysis(
        self, analysis: ReceiptLineItemAnalysis
    ):
        """Updates an existing ReceiptLineItemAnalysis in the database.

        Args:
            analysis (ReceiptLineItemAnalysis): The ReceiptLineItemAnalysis to update.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptLineItemAnalysis.
            Exception: If the analysis cannot be updated in DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptLineItemAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptLineItemAnalysis class.",
                f"\nGot type: {type(analysis)}",
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=analysis.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLineItemAnalysis for receipt ID {analysis.receipt_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not update ReceiptLineItemAnalysis in the database: {e}"
                )

    def update_receipt_line_item_analyses(
        self, analyses: list[ReceiptLineItemAnalysis]
    ):
        """Updates multiple ReceiptLineItemAnalyses in the database.

        Args:
            analyses (list[ReceiptLineItemAnalysis]): The ReceiptLineItemAnalyses to update.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be updated in DynamoDB.
        """
        if analyses is None:
            raise ValueError(
                "analyses parameter is required and cannot be None."
            )
        if not isinstance(analyses, list):
            raise ValueError(
                "analyses must be a list of ReceiptLineItemAnalysis instances."
            )
        if not all(isinstance(a, ReceiptLineItemAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptLineItemAnalysis class."
            )
        for i in range(0, len(analyses), 25):
            chunk = analyses[i : i + 25]
            transact_items = [
                TransactWriteItemTypeDef(
                    Put=PutTypeDef(
                        TableName=self.table_name,
                        Item=a.to_item(),
                        ConditionExpression="attribute_exists(PK)",
                    )
                )
                for a in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptLineItemAnalyses do not exist"
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
                        f"Could not update ReceiptLineItemAnalyses in the database: {e}"
                    ) from e

    def delete_receipt_line_item_analysis(
        self,
        analysis: ReceiptLineItemAnalysis,
    ):
        """Deletes a single ReceiptLineItemAnalysis.

        Args:
            analysis (ReceiptLineItemAnalysis): The ReceiptLineItemAnalysis to delete.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptLineItemAnalysis.
            Exception: If the analysis cannot be deleted from DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptLineItemAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptLineItemAnalysis class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=analysis.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLineItemAnalysis for receipt ID {analysis.receipt_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not delete ReceiptLineItemAnalysis from the database: {e}"
                )

    def delete_receipt_line_item_analyses(
        self, analyses: list[ReceiptLineItemAnalysis]
    ):
        """Deletes multiple ReceiptLineItemAnalyses in batch.

        Args:
            analyses (list[ReceiptLineItemAnalysis]): The ReceiptLineItemAnalyses to delete.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be deleted from DynamoDB.
        """
        if analyses is None:
            raise ValueError(
                "analyses parameter is required and cannot be None."
            )
        if not isinstance(analyses, list):
            raise ValueError(
                "analyses must be a list of ReceiptLineItemAnalysis instances."
            )
        if not all(isinstance(a, ReceiptLineItemAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptLineItemAnalysis class."
            )
        try:
            for i in range(0, len(analyses), 25):
                chunk = analyses[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=a.key())
                    )
                    for a in chunk
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
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise DynamoDBError(
                    f"Could not delete ReceiptLineItemAnalyses from the database: {e}"
                )

    def get_receipt_line_item_analysis(
        self,
        receipt_id: int,
        image_id: str,
    ) -> ReceiptLineItemAnalysis:
        """Retrieves a single ReceiptLineItemAnalysis by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.

        Raises:
            ValueError: If the receipt ID is None or not an integer.
            ValueError: If the image ID is None or not a valid UUID.
            Exception: If the receipt line item analysis cannot be retrieved from DynamoDB.
        """
        if receipt_id is None:
            raise ValueError(
                "receipt_id parameter is required and cannot be None."
            )
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be greater than 0")
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
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LINE_ITEMS"
                    },
                },
            )
            if "Item" in response:
                return item_to_receipt_line_item_analysis(response["Item"])
            else:
                raise ValueError(
                    f"Receipt Line Item Analysis for Image ID {image_id} and Receipt ID {receipt_id} does not exist"
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                )
            elif error_code == "ValidationException":
                raise OperationError(f"Validation error: {e}")
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}")
            else:
                raise OperationError(
                    f"Error getting receipt line item analysis: {e}"
                )

    def list_receipt_line_item_analyses(
        self, limit: Optional[int] = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptLineItemAnalysis], dict | None]:
        """Returns all ReceiptLineItemAnalyses from the table.

        Args:
            limit (int, optional): Maximum number of items to return. Defaults to None.
            lastEvaluatedKey (dict | None, optional): Last evaluated key for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptLineItemAnalysis], dict | None]: List of analyses and the last evaluated key.

        Raises:
            ValueError: If limit is not an integer or None.
            ValueError: If lastEvaluatedKey is not a dictionary or None.
            Exception: If analyses cannot be retrieved from DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        analyses = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            analyses.extend(
                [
                    item_to_receipt_line_item_analysis(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the analyses.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    analyses.extend(
                        [
                            item_to_receipt_line_item_analysis(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return analyses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt line item analyses from DynamoDB: {e}"
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
                raise OperationError(
                    f"Error listing receipt line item analyses: {e}"
                )

    def list_receipt_line_item_analyses_for_image(
        self, image_id: str
    ) -> list[ReceiptLineItemAnalysis]:
        """Returns all ReceiptLineItemAnalyses for a given image.

        Args:
            image_id (str): The image ID.

        Returns:
            list[ReceiptLineItemAnalysis]: List of analyses for the image.

        Raises:
            ValueError: If image_id is None or not a valid UUID.
            Exception: If analyses cannot be retrieved from DynamoDB.
        """
        if image_id is None:
            raise ValueError(
                "image_id parameter is required and cannot be None."
            )
        assert_valid_uuid(image_id)

        analyses = []
        try:
            # Query using just the partition key without a filter expression
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {"S": "RECEIPT#"},
                },
            )

            # Filter the results in memory to only include LINE_ITEMS analyses
            for item in response["Items"]:
                if "#ANALYSIS#LINE_ITEMS" in item["SK"]["S"]:
                    analyses.append(item_to_receipt_line_item_analysis(item))

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {"S": "RECEIPT#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )

                # Filter the results in memory to only include LINE_ITEMS analyses
                for item in response["Items"]:
                    if "#ANALYSIS#LINE_ITEMS" in item["SK"]["S"]:
                        analyses.append(
                            item_to_receipt_line_item_analysis(item)
                        )

            return analyses
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list ReceiptLineItemAnalyses from the database: {error_message}"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    f"Internal server error: {error_message}"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {error_message}"
                )
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {error_message}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {error_message}")
            else:
                raise DynamoDBError(
                    f"Could not list ReceiptLineItemAnalyses from the database: {error_message}"
                )
        except Exception as e:
            raise DynamoDBError(
                f"Could not list ReceiptLineItemAnalyses from the database: {str(e)}"
            ) from e
