from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo import (
    ReceiptStructureAnalysis,
    item_to_receipt_structure_analysis,
)
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )

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


class _ReceiptStructureAnalysis(DynamoClientProtocol):
    """
    A class used to access receipt structure analyses in DynamoDB.

    Methods
    -------
    add_receipt_structure_analysis(analysis: ReceiptStructureAnalysis)
        Adds a ReceiptStructureAnalysis to DynamoDB.
    add_receipt_structure_analyses(analyses: list[ReceiptStructureAnalysis])
        Adds multiple ReceiptStructureAnalyses to DynamoDB in batches.
    update_receipt_structure_analysis(analysis: ReceiptStructureAnalysis)
        Updates an existing ReceiptStructureAnalysis in the database.
    update_receipt_structure_analyses(analyses: list[ReceiptStructureAnalysis])
        Updates multiple ReceiptStructureAnalyses in the database.
    delete_receipt_structure_analysis(analysis: ReceiptStructureAnalysis)
        Deletes a single ReceiptStructureAnalysis by IDs.
    delete_receipt_structure_analyses(analyses: list[ReceiptStructureAnalysis])
        Deletes multiple ReceiptStructureAnalyses in batch.
    get_receipt_structure_analysis(
        receipt_id: int,
        image_id: str,
        version: Optional[str] = None
    ) -> ReceiptStructureAnalysis:
        Retrieves a single ReceiptStructureAnalysis by IDs.
    list_receipt_structure_analyses(
        limit: Optional[int] = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptStructureAnalysis], dict | None]:
        Returns ReceiptStructureAnalyses and the last evaluated key.
    list_receipt_structure_analyses_from_receipt(
        receipt_id: int,
        image_id: str
    ) -> list[ReceiptStructureAnalysis]:
        Returns all ReceiptStructureAnalyses for a given receipt.
    """

    def add_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Adds a ReceiptStructureAnalysis to DynamoDB.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis
                to add.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be added to DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the "
                "ReceiptStructureAnalysis class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=analysis.to_item(),
                ConditionExpression=(
                    "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                ),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "ReceiptStructureAnalysis for receipt "
                    "{analysis.receipt_id} and image {analysis.image_id} "
                    "already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not add receipt structure analysis to DynamoDB: "
                    "Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not add receipt structure analysis to "
                    f"DynamoDB: {e}"
                )

    def add_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Adds multiple ReceiptStructureAnalyses to DynamoDB in batches.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The
                ReceiptStructureAnalyses to add.

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
                "analyses must be a list of ReceiptStructureAnalysis "
                "instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the "
                "ReceiptStructureAnalysis class."
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
                retry_count = 0
                while unprocessed.get(self.table_name) and retry_count < 3:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                    retry_count += 1
                if unprocessed.get(self.table_name):
                    raise DynamoDBError(
                        "Failed to process all items after multiple retries"
                    )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not add receipt structure analyses to DynamoDB: Table not found"
                )
            elif error_code == "TransactionCanceledException":
                raise OperationError("Error adding receipt structure analyses")
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not add receipt structure analyses to DynamoDB: {e}"
                )

    def update_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Updates an existing ReceiptStructureAnalysis in the database.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis to update.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be updated in DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the "
                "ReceiptStructureAnalysis class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=analysis.to_item(),
                ConditionExpression=(
                    "attribute_exists(PK) AND attribute_exists(SK)"
                ),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "ReceiptStructureAnalysis for receipt "
                    "{analysis.receipt_id} and image {analysis.image_id} "
                    "does not exist"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not add receipt structure analysis to DynamoDB: "
                    "Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not update receipt structure analysis in the database: {e}"
                )

    def update_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Updates multiple ReceiptStructureAnalyses in the database.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The ReceiptStructureAnalyses to update.

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
                "analyses must be a list of ReceiptStructureAnalysis "
                "instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the "
                "ReceiptStructureAnalysis class."
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
                retry_count = 0
                while unprocessed.get(self.table_name) and retry_count < 3:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                    retry_count += 1
                if unprocessed.get(self.table_name):
                    raise DynamoDBError(
                        "Failed to process all items after multiple retries"
                    )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not update receipt structure analyses in DynamoDB: Table not found"
                )
            elif error_code == "TransactionCanceledException":
                raise OperationError(
                    "Error updating receipt structure analyses"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                # Extract and log more detailed error information
                error_message = e.response.get("Error", {}).get(
                    "Message", "Unknown validation error"
                )

                # If there's duplicate items, try to identify them
                if "contains duplicates" in error_message:
                    # Create a dictionary to find duplicates
                    keys_seen: Dict[str, Any] = {}
                    duplicate_keys = []

                    for i, a in enumerate(analyses):
                        key_str = "PK: IMAGE#{a.image_id}, SK: RECEIPT#{a.receipt_id:05d}#ANALYSIS#STRUCTURE#{a.version}"
                        if key_str in keys_seen:
                            duplicate_keys.append(
                                "Duplicate at indexes {keys_seen[key_str]} and {i}: {key_str}"
                            )
                        else:
                            keys_seen[key_str] = i

                    if duplicate_keys:
                        detailed_error = f"Validation error with duplicate keys: {error_message}\nDuplicate keys: {duplicate_keys}"
                    else:
                        detailed_error = f"Validation error possibly with duplicate keys: {error_message}"
                else:
                    detailed_error = f"Validation error: {error_message}"

                # TODO: Use proper logging instead of print
                pass
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {detailed_error}"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not update receipt structure analyses in DynamoDB: {e}"
                )

    def delete_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Deletes a single ReceiptStructureAnalysis by IDs.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis to delete.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be deleted from DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the "
                "ReceiptStructureAnalysis class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": "IMAGE#{analysis.image_id}"},
                    "SK": {
                        "S": "RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#STRUCTURE#{analysis.version}"
                    },
                },
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "ReceiptStructureAnalysis for receipt "
                    "{analysis.receipt_id} and image {analysis.image_id} "
                    "does not exist"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not add receipt structure analysis to DynamoDB: "
                    "Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not delete receipt structure analysis from the database: {e}"
                )

    def delete_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Deletes multiple ReceiptStructureAnalyses in batch.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The ReceiptStructureAnalyses to delete.

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
                "analyses must be a list of ReceiptStructureAnalysis "
                "instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the "
                "ReceiptStructureAnalysis class."
            )
        try:
            for i in range(0, len(analyses), 25):
                chunk = analyses[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(
                            Key={
                                "PK": {"S": f"IMAGE#{a.image_id}"},
                                "SK": {
                                    "S": f"RECEIPT#{a.receipt_id:05d}#ANALYSIS#STRUCTURE#{a.version}"
                                },
                            }
                        )
                    )
                    for a in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                retry_count = 0
                while unprocessed.get(self.table_name) and retry_count < 3:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                    retry_count += 1
                if unprocessed.get(self.table_name):
                    raise DynamoDBError(
                        "Failed to process all items after multiple retries"
                    )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not delete receipt structure analyses from DynamoDB: Table not found"
                )
            elif error_code == "TransactionCanceledException":
                raise OperationError(
                    "Error deleting receipt structure analyses"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not delete receipt structure analyses from DynamoDB: {e}"
                )

    def get_receipt_structure_analysis(
        self,
        receipt_id: int,
        image_id: str,
        version: Optional[str] = None,
    ) -> ReceiptStructureAnalysis:
        """Retrieves a single ReceiptStructureAnalysis by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            version (Optional[str]): The version of the analysis. If None, returns the first analysis found.

        Returns:
            ReceiptStructureAnalysis: The retrieved ReceiptStructureAnalysis.

        Raises:
            ValueError: If the receipt_id or image_id are invalid.
            Exception: If the ReceiptStructureAnalysis cannot be retrieved from DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise ValueError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if version is not None and not isinstance(version, str):
            raise ValueError(
                "version must be a string or None, got {type(version).__name__}"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        try:
            if version:
                # If version is provided, get the exact item
                response = self._client.get_item(
                    TableName=self.table_name,
                    Key={
                        "PK": {"S": "IMAGE#{image_id}"},
                        "SK": {
                            "S": "RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE#{version}"
                        },
                    },
                )
                item = response.get("Item")
                if not item:
                    raise ValueError(
                        f"No ReceiptStructureAnalysis found for receipt {receipt_id}, image {image_id}, and version {version}"
                    )
                return item_to_receipt_structure_analysis(item)
            else:
                # If no version is provided, query for all analyses and return the first one
                query_params: QueryInputTypeDef = {
                    "TableName": self.table_name,
                    "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :sk_prefix)",
                    "ExpressionAttributeNames": {
                        "#pk": "PK",
                        "#sk": "SK",
                    },
                    "ExpressionAttributeValues": {
                        ":pk": {"S": "IMAGE#{image_id}"},
                        ":sk_prefix": {
                            "S": "RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE"
                        },
                    },
                    "Limit": 1,  # We only need one result
                }

                query_response = self._client.query(**query_params)
                items = query_response.get("Items", [])

                if not items:
                    raise ValueError(
                        "Receipt Structure Analysis for Image ID {image_id} and Receipt ID {receipt_id} does not exist"
                    )

                return item_to_receipt_structure_analysis(items[0])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not get receipt structure analysis from DynamoDB: Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    f"Could not get receipt structure analysis from the database: {e}"
                )

    def list_receipt_structure_analyses(
        self,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptStructureAnalysis], Optional[Dict[str, Any]]]:
        """Lists all ReceiptStructureAnalyses.

        Args:
            limit (Optional[int], optional): The maximum number of items to return. Defaults to None.
            lastEvaluatedKey (Optional[Dict[str, Any]], optional): The key to start from for pagination. Defaults to None.

        Returns:
            Tuple[List[ReceiptStructureAnalysis], Optional[Dict[str, Any]]]: A tuple containing the list of ReceiptStructureAnalyses and the last evaluated key for pagination.

        Raises:
            ValueError: If the limit or lastEvaluatedKey are invalid.
            Exception: If the ReceiptStructureAnalyses cannot be retrieved from DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None")

        structure_analyses = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_STRUCTURE_ANALYSIS"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            structure_analyses.extend(
                [
                    item_to_receipt_structure_analysis(item)
                    for item in response["Items"]
                ]
            )

            if limit is None:
                # Paginate through all the structure analyses
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    structure_analyses.extend(
                        [
                            item_to_receipt_structure_analysis(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return structure_analyses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not list receipt structure analyses from DynamoDB: Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    "Could not list receipt structure analyses from the database"
                )

    def list_receipt_structure_analyses_from_receipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptStructureAnalysis]:
        """Returns all ReceiptStructureAnalyses for a given receipt.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.

        Returns:
            list[ReceiptStructureAnalysis]: A list of ReceiptStructureAnalyses.

        Raises:
            ValueError: If the receipt_id or image_id are invalid.
            Exception: If the ReceiptStructureAnalyses cannot be retrieved from DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise ValueError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :sk_prefix)",
                "ExpressionAttributeNames": {
                    "#pk": "PK",
                    "#sk": "SK",
                },
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE#"
                    },
                },
            }

            response = self._client.query(**query_params)
            analyses = [
                item_to_receipt_structure_analysis(item)
                for item in response["Items"]
            ]

            # Continue querying if there are more results
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                analyses.extend(
                    [
                        item_to_receipt_structure_analysis(item)
                        for item in response["Items"]
                    ]
                )

            return analyses
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not list receipt structure analyses from DynamoDB: Table not found"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                )
            elif error_code == "InternalServerError":
                raise DynamoDBServerError("Internal server error")
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                )
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied")
            else:
                raise DynamoDBError(
                    "Could not list receipt structure analyses for the receipt"
                )
