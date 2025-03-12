from botocore.exceptions import ClientError
from typing import List, Optional, Dict, Tuple, Any

from receipt_dynamo import (
    ReceiptStructureAnalysis,
    itemToReceiptStructureAnalysis,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptStructureAnalysis:
    """
    A class used to access receipt structure analyses in DynamoDB.

    Methods
    -------
    addReceiptStructureAnalysis(analysis: ReceiptStructureAnalysis)
        Adds a ReceiptStructureAnalysis to DynamoDB.
    addReceiptStructureAnalyses(analyses: list[ReceiptStructureAnalysis])
        Adds multiple ReceiptStructureAnalyses to DynamoDB in batches.
    updateReceiptStructureAnalysis(analysis: ReceiptStructureAnalysis)
        Updates an existing ReceiptStructureAnalysis in the database.
    updateReceiptStructureAnalyses(analyses: list[ReceiptStructureAnalysis])
        Updates multiple ReceiptStructureAnalyses in the database.
    deleteReceiptStructureAnalysis(analysis: ReceiptStructureAnalysis)
        Deletes a single ReceiptStructureAnalysis by IDs.
    deleteReceiptStructureAnalyses(analyses: list[ReceiptStructureAnalysis])
        Deletes multiple ReceiptStructureAnalyses in batch.
    getReceiptStructureAnalysis(
        receipt_id: int,
        image_id: str
    ) -> ReceiptStructureAnalysis:
        Retrieves a single ReceiptStructureAnalysis by IDs.
    listReceiptStructureAnalyses(
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptStructureAnalysis], dict | None]:
        Returns ReceiptStructureAnalyses and the last evaluated key.
    listReceiptStructureAnalysesFromReceipt(
        receipt_id: int,
        image_id: str
    ) -> list[ReceiptStructureAnalysis]:
        Returns all ReceiptStructureAnalyses for a given receipt.
    """

    def addReceiptStructureAnalysis(self, analysis: ReceiptStructureAnalysis):
        """Adds a ReceiptStructureAnalysis to DynamoDB.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis to add.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptStructureAnalysis.
            Exception: If the analysis cannot be added to DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptStructureAnalysis class."
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
                    f"ReceiptStructureAnalysis for receipt {analysis.receipt_id} and image {analysis.image_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt structure analysis to DynamoDB: {e}"
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
                    f"Could not add receipt structure analysis to DynamoDB: {e}"
                ) from e

    def addReceiptStructureAnalyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Adds multiple ReceiptStructureAnalyses to DynamoDB in batches.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The ReceiptStructureAnalyses to add.

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
                "analyses must be a list of ReceiptStructureAnalysis instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptStructureAnalysis class."
            )
        try:
            for i in range(0, len(analyses), 25):
                chunk = analyses[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": a.to_item()}} for a in chunk
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
                    f"Could not add ReceiptStructureAnalyses to the database: {e}"
                ) from e

    def updateReceiptStructureAnalysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Updates an existing ReceiptStructureAnalysis in the database.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis to update.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptStructureAnalysis.
            Exception: If the analysis cannot be updated in DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptStructureAnalysis class."
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
                    f"ReceiptStructureAnalysis for receipt {analysis.receipt_id} and image {analysis.image_id} does not exist"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception("Provisioned throughput exceeded")
            elif error_code == "InternalServerError":
                raise Exception("Internal server error")
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    "Could not update ReceiptStructureAnalysis in the database"
                )
            elif error_code == "ValidationException":
                raise Exception("One or more parameters given were invalid")
            elif error_code == "AccessDeniedException":
                raise Exception("Access denied")
            else:
                raise Exception(
                    "Could not update ReceiptStructureAnalysis in the database"
                )

    def updateReceiptStructureAnalyses(
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
                "analyses must be a list of ReceiptStructureAnalysis instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptStructureAnalysis class."
            )
        for i in range(0, len(analyses), 25):
            chunk = analyses[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": a.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
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
                            "Error updating receipt structure analyses: "
                            + str(e)
                        )
                elif error_code == "ResourceNotFoundException":
                    raise Exception(
                        "Could not update ReceiptStructureAnalyses in the database"
                    )
                elif error_code == "InternalServerError":
                    raise Exception("Internal server error")
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception("Provisioned throughput exceeded")
                elif error_code == "ValidationException":
                    raise Exception(
                        "One or more parameters given were invalid"
                    )
                elif error_code == "AccessDeniedException":
                    raise Exception("Access denied")
                else:
                    raise Exception(
                        "Could not update ReceiptStructureAnalyses in the database"
                    )

    def deleteReceiptStructureAnalysis(
        self,
        analysis: ReceiptStructureAnalysis,
    ):
        """Deletes a single ReceiptStructureAnalysis by IDs.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis to delete.

        Raises:
            ValueError: If the analysis is None or not an instance of ReceiptStructureAnalysis.
            Exception: If the analysis cannot be deleted from DynamoDB.
        """
        if analysis is None:
            raise ValueError(
                "analysis parameter is required and cannot be None."
            )
        if not isinstance(analysis, ReceiptStructureAnalysis):
            raise ValueError(
                "analysis must be an instance of the ReceiptStructureAnalysis class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{analysis.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptStructureAnalysis for receipt {analysis.receipt_id} and image {analysis.image_id} does not exist"
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
                    f"Could not delete ReceiptStructureAnalysis from the database: {e}"
                ) from e

    def deleteReceiptStructureAnalyses(
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
                "analyses must be a list of ReceiptStructureAnalysis instances."
            )
        if not all(isinstance(a, ReceiptStructureAnalysis) for a in analyses):
            raise ValueError(
                "All analyses must be instances of the ReceiptStructureAnalysis class."
            )
        for i in range(0, len(analyses), 25):
            chunk = analyses[i : i + 25]
            transact_items = [
                {
                    "Delete": {
                        "TableName": self.table_name,
                        "Key": {
                            "PK": {"S": f"IMAGE#{a.image_id}"},
                            "SK": {
                                "S": f"RECEIPT#{a.receipt_id}#ANALYSIS#STRUCTURE#{a.version}"
                            },
                        },
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
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
                            "Error deleting receipt structure analyses: "
                            + str(e)
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
                        f"Could not delete ReceiptStructureAnalyses from the database: {e}"
                    ) from e

    def getReceiptStructureAnalysis(
        self,
        receipt_id: int,
        image_id: str,
    ) -> ReceiptStructureAnalysis:
        """Retrieves a single ReceiptStructureAnalysis by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.

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

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :sk_prefix)",
                "ExpressionAttributeNames": {
                    "#pk": "PK",
                    "#sk": "SK",
                },
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#STRUCTURE"
                    },
                },
                "Limit": 1,  # We only need one result
            }

            response = self._client.query(**query_params)
            items = response.get("Items", [])

            if not items:
                raise ValueError(
                    f"No ReceiptStructureAnalysis found for receipt {receipt_id} and image {image_id}"
                )

            return itemToReceiptStructureAnalysis(items[0])
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
                    f"Could not get ReceiptStructureAnalysis from the database: {e}"
                ) from e

    def listReceiptStructureAnalyses(
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
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#g1pk = :val",
                "ExpressionAttributeNames": {"#g1pk": "GSI1PK"},
                "ExpressionAttributeValues": {":val": {"S": "ANALYSIS_TYPE"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            structure_analyses.extend(
                [
                    itemToReceiptStructureAnalysis(item)
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
                            itemToReceiptStructureAnalysis(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return structure_analyses, last_evaluated_key
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
                    f"Could not list ReceiptStructureAnalyses from the database: {e}"
                ) from e

    def listReceiptStructureAnalysesFromReceipt(
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
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "#g2pk = :g2pk AND begins_with(#g2sk, :g2sk_prefix)",
                "ExpressionAttributeNames": {
                    "#g2pk": "GSI2PK",
                    "#g2sk": "GSI2SK",
                },
                "ExpressionAttributeValues": {
                    ":g2pk": {"S": "RECEIPT"},
                    ":g2sk_prefix": {
                        "S": f"IMAGE#{image_id}#RECEIPT#{receipt_id}"
                    },
                },
            }

            response = self._client.query(**query_params)
            analyses = [
                itemToReceiptStructureAnalysis(item)
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
                        itemToReceiptStructureAnalysis(item)
                        for item in response["Items"]
                    ]
                )

            return analyses
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
                    f"Could not list ReceiptStructureAnalyses for the receipt: {e}"
                ) from e
