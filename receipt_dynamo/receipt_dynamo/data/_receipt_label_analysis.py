from typing import TYPE_CHECKING, List

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteTypeDef,
        PutRequestTypeDef,
        PutTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    DeleteTypeDef,
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
    ReceiptDynamoError,
)
from receipt_dynamo.entities.receipt_analysis import ReceiptAnalysis
from receipt_dynamo.entities.receipt_chatgpt_validation import (
    ReceiptChatGPTValidation,
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_label_analysis import (
    ReceiptLabelAnalysis,
    item_to_receipt_label_analysis,
)
from receipt_dynamo.entities.receipt_line_item_analysis import (
    ReceiptLineItemAnalysis,
    item_to_receipt_line_item_analysis,
)
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
    item_to_receipt_structure_analysis,
)
from receipt_dynamo.entities.receipt_validation_category import (
    ReceiptValidationCategory,
    item_to_receipt_validation_category,
)
from receipt_dynamo.entities.receipt_validation_result import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)
from receipt_dynamo.entities.receipt_validation_summary import (
    ReceiptValidationSummary,
    item_to_receipt_validation_summary,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _ReceiptLabelAnalysis(DynamoClientProtocol):
    def add_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Adds a receipt label analysis to the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label
                analysis to add to the database

        Raises:
            ValueError: When a receipt label analysis with the same ID already
                exists
        """
        if receipt_label_analysis is None:
            raise ValueError(
                "ReceiptLabelAnalysis parameter is required and cannot be "
                "None."
            )
        if not isinstance(receipt_label_analysis, ReceiptLabelAnalysis):
            raise ValueError(
                "receipt_label_analysis must be an instance of the "
                "ReceiptLabelAnalysis class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_label_analysis.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt label analysis for Image ID '{receipt_label_analysis.image_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add receipt label analysis to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not add receipt label analysis to DynamoDB: {e}"
                ) from e

    def add_receipt_label_analyses(
        self, receipt_label_analyses: list[ReceiptLabelAnalysis]
    ):
        """Adds a list of receipt label analyses to the database

        Args:
            receipt_label_analyses (list[ReceiptLabelAnalysis]): The receipt label analyses to add to the database

        Raises:
            ValueError: When a receipt label analysis with the same ID already
                exists
        """
        if receipt_label_analyses is None:
            raise ValueError(
                "ReceiptLabelAnalyses parameter is required and cannot be None."
            )
        if not isinstance(receipt_label_analyses, list):
            raise ValueError(
                "receipt_label_analyses must be a list of ReceiptLabelAnalysis instances."
            )
        if not all(
            isinstance(analysis, ReceiptLabelAnalysis)
            for analysis in receipt_label_analyses
        ):
            raise ValueError(
                "All receipt label analyses must be instances of the ReceiptLabelAnalysis class."
            )
        try:
            for i in range(0, len(receipt_label_analyses), 25):
                chunk = receipt_label_analyses[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=analysis.to_item())
                    )
                    for analysis in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
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
                raise ValueError(
                    f"Error adding receipt label analyses: {e}"
                ) from e

    def update_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Updates a receipt label analysis in the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label analysis to update in the database

        Raises:
            ValueError: When the receipt label analysis does not exist
        """
        if receipt_label_analysis is None:
            raise ValueError(
                "ReceiptLabelAnalysis parameter is required and cannot be "
                "None."
            )
        if not isinstance(receipt_label_analysis, ReceiptLabelAnalysis):
            raise ValueError(
                "receipt_label_analysis must be an instance of the "
                "ReceiptLabelAnalysis class."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_label_analysis.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt label analysis for Image ID '{receipt_label_analysis.image_id}' does not exist"
                )
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
                raise ValueError(
                    f"Error updating receipt label analysis: {e}"
                ) from e

    def update_receipt_label_analyses(
        self, receipt_label_analyses: list[ReceiptLabelAnalysis]
    ):
        """Updates a list of receipt label analyses in the database using transactions.
        Each receipt label analysis update is conditional upon the analysis already existing.

        Args:
            receipt_label_analyses (list[ReceiptLabelAnalysis]): The receipt label analyses to update in the database.

        Raises:
            ValueError: When given a bad parameter or if an analysis doesn't exist.
            Exception: For underlying DynamoDB errors.
        """
        if receipt_label_analyses is None:
            raise ValueError(
                "ReceiptLabelAnalyses parameter is required and cannot be None."
            )
        if not isinstance(receipt_label_analyses, list):
            raise ValueError(
                "receipt_label_analyses must be a list of ReceiptLabelAnalysis instances."
            )
        if not all(
            isinstance(analysis, ReceiptLabelAnalysis)
            for analysis in receipt_label_analyses
        ):
            raise ValueError(
                "All receipt label analyses must be instances of the ReceiptLabelAnalysis class."
            )

        # Process analyses in chunks of 25 because transact_write_items
        # supports a maximum of 25 operations.
        for i in range(0, len(receipt_label_analyses), 25):
            chunk = receipt_label_analyses[i : i + 25]
            transact_items = []
            for analysis in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Put=PutTypeDef(
                            TableName=self.table_name,
                            Item=analysis.to_item(),
                            ConditionExpression="attribute_exists(PK)",
                        )
                    )
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "One or more receipt label analyses do not exist"
                    ) from e
                elif error_code == "TransactionCanceledException":
                    # This is raised when conditions fail in a transaction
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more receipt label analyses do not exist"
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
                        f"Error updating receipt label analyses: {e}"
                    ) from e

    def delete_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Deletes a receipt label analysis from the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label analysis to delete from the database

        Raises:
            ValueError: When the receipt label analysis does not exist
        """
        if receipt_label_analysis is None:
            raise ValueError(
                "ReceiptLabelAnalysis parameter is required and cannot be "
                "None."
            )
        if not isinstance(receipt_label_analysis, ReceiptLabelAnalysis):
            raise ValueError(
                "receipt_label_analysis must be an instance of the "
                "ReceiptLabelAnalysis class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=receipt_label_analysis.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt label analysis for Image ID '{receipt_label_analysis.image_id}' does not exist"
                )
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
                raise ValueError(
                    f"Error deleting receipt label analysis: {e}"
                ) from e

    def delete_receipt_label_analyses(
        self, receipt_label_analyses: list[ReceiptLabelAnalysis]
    ):
        """Deletes a list of receipt label analyses from the database using transactions.
        Each delete operation is conditional upon the analysis existing.

        Args:
            receipt_label_analyses (list[ReceiptLabelAnalysis]): The receipt label analyses to delete from the database.

        Raises:
            ValueError: When a receipt label analysis does not exist or if another error occurs.
        """
        if receipt_label_analyses is None:
            raise ValueError(
                "ReceiptLabelAnalyses parameter is required and cannot be None."
            )
        if not isinstance(receipt_label_analyses, list):
            raise ValueError(
                "receipt_label_analyses must be a list of ReceiptLabelAnalysis instances."
            )
        if not all(
            isinstance(analysis, ReceiptLabelAnalysis)
            for analysis in receipt_label_analyses
        ):
            raise ValueError(
                "All receipt label analyses must be instances of the ReceiptLabelAnalysis class."
            )

        try:
            # Process analyses in chunks of 25 items (the maximum allowed per
            # transaction)
            for i in range(0, len(receipt_label_analyses), 25):
                chunk = receipt_label_analyses[i : i + 25]
                transact_items = []
                for analysis in chunk:
                    transact_items.append(
                        TransactWriteItemTypeDef(
                            Delete=DeleteTypeDef(
                                TableName=self.table_name,
                                Key=analysis.key(),
                                ConditionExpression="attribute_exists(PK)",
                            )
                        )
                    )
                # Execute the transaction for this chunk.
                self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "One or more receipt label analyses do not exist"
                ) from e
            elif error_code == "TransactionCanceledException":
                # This is raised when conditions fail in a transaction
                raise ValueError(
                    "One or more receipt label analyses do not exist"
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
                raise ValueError(
                    f"Error deleting receipt label analyses: {e}"
                ) from e

    def get_receipt_label_analysis(
        self, image_id: str, receipt_id: int
    ) -> ReceiptLabelAnalysis:
        """Retrieves a receipt label analysis from the database.

        Args:
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt.

        Returns:
            ReceiptLabelAnalysis: The receipt label analysis object.

        Raises:
            ValueError: If input parameters are invalid or if the analysis does not exist.
            Exception: For underlying DynamoDB errors.
        """
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if receipt_id is None:
            raise ValueError("Receipt ID is required and cannot be None.")

        # Validate image_id as a UUID and receipt_id as a positive integer
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS"},
                },
            )
            if "Item" in response:
                return item_to_receipt_label_analysis(response["Item"])
            else:
                raise ValueError(
                    f"Receipt label analysis for Image ID '{image_id}' and Receipt ID {receipt_id} does not exist."
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
                    f"Error getting receipt label analysis: {e}"
                ) from e

    def list_receipt_label_analyses(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLabelAnalysis], dict | None]:
        """Retrieve receipt label analysis records from the database with support for precise pagination.

        Parameters:
            limit (int, optional): The maximum number of receipt label analysis items to return.
            last_evaluated_key (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of ReceiptLabelAnalysis objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises:
            ValueError: If the limit is not an integer or is less than or equal to 0.
            ValueError: If the last_evaluated_key is not a dictionary.
            Exception: If the underlying database query fails.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        analyses: List[ReceiptLabelAnalysis] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_LABEL_ANALYSIS"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(analyses)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                analyses.extend(
                    [
                        item_to_receipt_label_analysis(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(analyses) >= limit:
                    analyses = analyses[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return analyses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt label analyses from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt label analyses from the database: {e}"
                ) from e

    def get_receipt_label_analyses_by_image(
        self,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLabelAnalysis], dict | None]:
        """Retrieve receipt label analyses for a specific image from the database with support for pagination.

        Parameters:
            image_id (str): The ID of the image to retrieve analyses for.
            limit (int, optional): The maximum number of receipt label analysis items to return.
            last_evaluated_key (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of ReceiptLabelAnalysis objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises:
            ValueError: If the image_id is not a string or not a valid UUID.
            ValueError: If the limit is not an integer or is less than or equal to 0.
            ValueError: If the last_evaluated_key is not a dictionary.
            Exception: If the underlying database query fails.
        """
        if image_id is None:
            raise ValueError("Image ID must be a string")
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")

        # Validate image_id as a UUID
        assert_valid_uuid(image_id)

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        analyses: List[ReceiptLabelAnalysis] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "PK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"IMAGE#{image_id}"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(analyses)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)

                # Filter items to only include receipt label analyses
                for item in response["Items"]:
                    if (
                        "SK" in item
                        and "S" in item["SK"]
                        and "#ANALYSIS#LABELS" in item["SK"]["S"]
                    ):
                        analyses.append(item_to_receipt_label_analysis(item))

                if limit is not None and len(analyses) >= limit:
                    analyses = analyses[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return analyses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ReceiptDynamoError(
                    f"Could not get receipt label analyses for image '{image_id}': {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise ReceiptDynamoError(
                    f"Could not get receipt label analyses for image '{image_id}': {e}"
                ) from e

    def get_receipt_label_analyses_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLabelAnalysis], dict | None]:
        """Retrieve receipt label analyses for a specific receipt from the database with support for pagination.

        Parameters:
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt to retrieve analyses for.
            limit (int, optional): The maximum number of receipt label analysis items to return.
            last_evaluated_key (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of ReceiptLabelAnalysis objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises:
            ValueError: If the image_id is not a string or not a valid UUID.
            ValueError: If the receipt_id is not a positive integer.
            ValueError: If the limit is not an integer or is less than or equal to 0.
            ValueError: If the last_evaluated_key is not a dictionary.
            Exception: If the underlying database query fails.
        """
        if image_id is None:
            raise ValueError("Image ID must be a string")
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")

        # Validate image_id as a UUID
        assert_valid_uuid(image_id)

        if (
            receipt_id is None
            or not isinstance(receipt_id, int)
            or receipt_id <= 0
        ):
            raise ValueError("Receipt ID must be a positive integer")

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        analyses: List[ReceiptLabelAnalysis] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk_val AND #sk = :sk_val",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS"
                    },
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(analyses)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                analyses.extend(
                    [
                        item_to_receipt_label_analysis(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(analyses) >= limit:
                    analyses = analyses[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return analyses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ReceiptDynamoError(
                    f"Could not get receipt label analyses for image '{image_id}' and receipt '{receipt_id}': {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise ReceiptDynamoError(
                    f"Could not get receipt label analyses for image '{image_id}' and receipt '{receipt_id}': {e}"
                ) from e

    def get_receipt_analysis(
        self,
        image_id: str,
        receipt_id: int,
    ) -> ReceiptAnalysis:
        """Retrieve receipt analysis for a specific receipt from the database.

        Args:
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt to retrieve analysis for.

        Returns:
            ReceiptAnalysis: The receipt analysis for the specified receipt, containing
                             all available analysis types including label analysis, structure analysis,
                             line item analysis, validation summary, validation categories,
                             validation results, and ChatGPT validations.

        Raises:
            ValueError: If the image_id is not a string or not a valid UUID.
            ValueError: If the receipt_id is not a positive integer.
            Exception: If the underlying database query fails.
        """
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if receipt_id is None:
            raise ValueError("Receipt ID is required and cannot be None.")

        # Validate image_id as a UUID and receipt_id as a positive integer
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")

        # Create a ReceiptAnalysis object to store all analyses
        receipt_analysis = ReceiptAnalysis(
            image_id=image_id, receipt_id=receipt_id
        )

        try:
            # Query for all analysis items for this receipt in a single operation
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_prefix)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS"},
                },
            )

            # Process each item based on its TYPE attribute
            for item in response.get("Items", []):
                # Get the item's type
                item_type = item.get("TYPE", {}).get("S", "")

                # Assign the item to the appropriate field based on its TYPE
                try:
                    if item_type == "RECEIPT_LABEL_ANALYSIS":
                        receipt_analysis.label_analysis = (
                            item_to_receipt_label_analysis(item)
                        )
                    elif item_type == "RECEIPT_STRUCTURE_ANALYSIS":
                        receipt_analysis.structure_analysis = (
                            item_to_receipt_structure_analysis(item)
                        )
                    elif item_type == "RECEIPT_LINE_ITEM_ANALYSIS":
                        receipt_analysis.line_item_analysis = (
                            item_to_receipt_line_item_analysis(item)
                        )
                    elif item_type == "RECEIPT_VALIDATION_SUMMARY":
                        receipt_analysis.validation_summary = (
                            item_to_receipt_validation_summary(item)
                        )
                    elif item_type == "RECEIPT_VALIDATION_CATEGORY":
                        category = item_to_receipt_validation_category(item)
                        if receipt_analysis.validation_categories is None:
                            receipt_analysis.validation_categories = []
                        receipt_analysis.validation_categories.append(category)
                    elif item_type == "RECEIPT_VALIDATION_RESULT":
                        result = item_to_receipt_validation_result(item)
                        if receipt_analysis.validation_results is None:
                            receipt_analysis.validation_results = []
                        receipt_analysis.validation_results.append(result)
                    elif item_type == "RECEIPT_CHATGPT_VALIDATION":
                        chatgpt_validation = (
                            item_to_receipt_chat_gpt_validation(item)
                        )
                        if receipt_analysis.chatgpt_validations is None:
                            receipt_analysis.chatgpt_validations = []
                        receipt_analysis.chatgpt_validations.append(
                            chatgpt_validation
                        )
                except Exception as e:
                    # Skip if conversion fails, but log the error
                    print(f"Error converting {item_type}: {str(e)}")
                    pass

            return receipt_analysis

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
                    f"Error getting receipt analysis: {e}"
                ) from e
