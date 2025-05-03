from botocore.exceptions import ClientError
from typing import List, Tuple

from receipt_dynamo.entities import ReceiptMetadata, itemToReceiptMetadata
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
)


class _ReceiptMetadata:

    def addReceiptMetadata(self, receipt_metadata: ReceiptMetadata):
        """
        Adds a single ReceiptMetadata record to DynamoDB.

        Args:
            receipt_metadata (ReceiptMetadata): The ReceiptMetadata instance to add.

        Raises:
            ValueError: If receipt_metadata is None, not a ReceiptMetadata, or if DynamoDB conditions fail.
        """
        if receipt_metadata is None:
            raise ValueError("receipt_metadata cannot be None")
        if not isinstance(receipt_metadata, ReceiptMetadata):
            raise ValueError("receipt_metadata must be a ReceiptMetadata")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_metadata.to_item(),
                ConditionExpression="attribute_not_exists(PK) and attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata already exists")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error adding receipt metadata: {e}")

    def addReceiptMetadatas(self, receipt_metadatas: List[ReceiptMetadata]):
        """
        Adds multiple ReceiptMetadata records to DynamoDB in batches.

        Args:
            receipt_metadatas (List[ReceiptMetadata]): A list of ReceiptMetadata instances to add.

        Raises:
            ValueError: If receipt_metadatas is None, not a list, or contains None or non-ReceiptMetadata items.
        """
        if receipt_metadatas is None:
            raise ValueError("receipt_metadatas cannot be None")
        if not isinstance(receipt_metadatas, list):
            raise ValueError("receipt_metadatas must be a list")
        if not all(
            isinstance(item, ReceiptMetadata) for item in receipt_metadatas
        ):
            raise ValueError(
                "receipt_metadatas must be a list of ReceiptMetadata"
            )

        try:
            for i in range(0, len(receipt_metadatas), 25):
                chunk = receipt_metadatas[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": item.to_item()}} for item in chunk
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
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata already exists")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error adding receipt metadata: {e}")

    def updateReceiptMetadata(self, receipt_metadata: ReceiptMetadata):
        """
        Updates an existing ReceiptMetadata record in DynamoDB.

        Args:
            receipt_metadata (ReceiptMetadata): The ReceiptMetadata instance to update.

        Raises:
            ValueError: If receipt_metadata is None, not a ReceiptMetadata, or if the record does not exist.
        """
        if receipt_metadata is None:
            raise ValueError("receipt_metadata cannot be None")
        if not isinstance(receipt_metadata, ReceiptMetadata):
            raise ValueError("receipt_metadata must be a ReceiptMetadata")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_metadata.to_item(),
                ConditionExpression="attribute_exists(PK) and attribute_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata does not exist")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error updating receipt metadata: {e}")

    def updateReceiptMetadatas(self, receipt_metadatas: List[ReceiptMetadata]):
        """
        Updates multiple ReceiptMetadata records in DynamoDB using transactions.

        Args:
            receipt_metadatas (List[ReceiptMetadata]): A list of ReceiptMetadata instances to update.

        Raises:
            ValueError: If receipt_metadatas is None, not a list, or contains None or non-ReceiptMetadata items.
        """
        if receipt_metadatas is None:
            raise ValueError("receipt_metadatas cannot be None")
        if not isinstance(receipt_metadatas, list):
            raise ValueError("receipt_metadatas must be a list")
        if not all(
            isinstance(item, ReceiptMetadata) for item in receipt_metadatas
        ):
            raise ValueError(
                "receipt_metadatas must be a list of ReceiptMetadata"
            )

        try:
            for i in range(0, len(receipt_metadatas), 25):
                chunk = receipt_metadatas[i : i + 25]
                transact_items = [
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": item.to_item(),
                            "ConditionExpression": "attribute_exists(PK) and attribute_exists(SK)",
                        }
                    }
                    for item in chunk
                ]
                response = self._client.transact_write_items(
                    TransactItems=transact_items
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.transact_write_items(
                        TransactItems=unprocessed[self.table_name]
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata does not exist")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error updating receipt metadata: {e}")

    def deleteReceiptMetadata(self, receipt_metadata: ReceiptMetadata):
        """
        Deletes a single ReceiptMetadata record from DynamoDB.

        Args:
            receipt_metadata (ReceiptMetadata): The ReceiptMetadata instance to delete.

        Raises:
            ValueError: If receipt_metadata is None, not a ReceiptMetadata, or if the record does not exist.
        """
        if receipt_metadata is None:
            raise ValueError("receipt_metadata cannot be None")
        if not isinstance(receipt_metadata, ReceiptMetadata):
            raise ValueError("receipt_metadata must be a ReceiptMetadata")

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=receipt_metadata.key(),
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata does not exist")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error deleting receipt metadata: {e}")

    def deleteReceiptMetadatas(self, receipt_metadatas: List[ReceiptMetadata]):
        """
        Deletes multiple ReceiptMetadata records from DynamoDB.

        Args:
            receipt_metadatas (List[ReceiptMetadata]): A list of ReceiptMetadata instances to delete.

        Raises:
            ValueError: If receipt_metadatas is None, not a list, or contains None or non-ReceiptMetadata items.
        """
        if receipt_metadatas is None:
            raise ValueError("receipt_metadatas cannot be None")
        if not isinstance(receipt_metadatas, list):
            raise ValueError("receipt_metadatas must be a list")
        if not all(
            isinstance(item, ReceiptMetadata) for item in receipt_metadatas
        ):
            raise ValueError(
                "receipt_metadatas must be a list of ReceiptMetadata"
            )

        try:
            for i in range(0, len(receipt_metadatas), 25):
                chunk = receipt_metadatas[i : i + 25]
                transact_items = [
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": item.key(),
                            "ConditionExpression": "attribute_exists(PK) and attribute_exists(SK)",
                        }
                    }
                    for item in chunk
                ]
                response = self._client.transact_write_items(
                    TransactItems=transact_items
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.transact_write_items(
                        Items=unprocessed[self.table_name]
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("receipt_metadata does not exist")
            elif error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error deleting receipt metadata: {e}")

    def getReceiptMetadata(
        self, image_id: str, receipt_id: int
    ) -> ReceiptMetadata:
        """
        Retrieves a single ReceiptMetadata record from DynamoDB by image_id and receipt_id.

        Args:
            image_id (str): The image_id of the ReceiptMetadata record to retrieve.
            receipt_id (int): The receipt_id of the ReceiptMetadata record to retrieve.

        Returns:
            ReceiptMetadata: The corresponding ReceiptMetadata instance.

        Raises:
            ValueError: If image_id is None, not a string, or receipt_id is None, not an integer.
        """
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        assert_valid_uuid(image_id)
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#METADATA"},
                },
            )
            item = response.get("Item")
            if item is None:
                raise ValueError("receipt_metadata does not exist")
            return itemToReceiptMetadata(item)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error getting receipt metadata: {e}")

    def getReceiptMetadatasByIndices(
        self, indices: list[tuple[str, int]]
    ) -> list[ReceiptMetadata]:
        """
        Retrieves a list of ReceiptMetadata records from DynamoDB by image_id and receipt_id.
        """
        if indices is None:
            raise ValueError("indices cannot be None")
        if not isinstance(indices, list):
            raise ValueError("indices must be a list")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("indices must be a list of tuples")
        if not all(
            isinstance(index[0], str) and isinstance(index[1], int)
            for index in indices
        ):
            raise ValueError(
                "indices must be a list of tuples of (image_id, receipt_id)"
            )
        if not all(index[1] > 0 for index in indices):
            raise ValueError("receipt_id must be positive")

        keys = [
            {
                "PK": {"S": f"IMAGE#{index[0]}"},
                "SK": {"S": f"RECEIPT#{index[1]:05d}#METADATA"},
            }
            for index in indices
        ]
        return self.getReceiptMetadatas(keys)

    def getReceiptMetadatas(self, keys: list[dict]) -> list[ReceiptMetadata]:
        """
        Retrieves a list of ReceiptMetadata records from DynamoDB using a list of keys.

        Args:
            keys (list[dict]): A list of keys to retrieve the ReceiptMetadata records by.

        Returns:
            list[ReceiptMetadata]: A list of ReceiptMetadata records.

        Raises:
            ValueError: If keys is None, not a list, or contains None or non-dict items.
        """
        if keys is None:
            raise ValueError("keys cannot be None")
        if not isinstance(keys, list):
            raise ValueError("keys must be a list")
        if not all(isinstance(key, dict) for key in keys):
            raise ValueError("keys must be a list of dictionaries")
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise ValueError("SK must start with 'RECEIPT#'")
            if not key["SK"]["S"].split("#")[-1] == "METADATA":
                raise ValueError("SK must contain 'METADATA'")
        results = []
        for i in range(0, len(keys), 25):
            chunk = keys[i : i + 25]
            response = self._client.batch_get_item(
                RequestItems={self.table_name: {"Keys": chunk}}
            )
            batch_items = response["Responses"].get(self.table_name, [])
            results.extend(batch_items)
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name):
                response = self._client.batch_get_item(
                    RequestItems=unprocessed
                )
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})
        return [itemToReceiptMetadata(result) for result in results]

    def listReceiptMetadatas(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Lists ReceiptMetadata records from DynamoDB with optional pagination.

        Args:
            limit (int, optional): Maximum number of records to retrieve.
            lastEvaluatedKey (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[ReceiptMetadata], dict | None]: A tuple containing the list of ReceiptMetadata records and the last evaluated key.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")

        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary")

        metadatas = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_METADATA"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            metadatas.extend(
                itemToReceiptMetadata(item)
                for item in response.get("Items", [])
            )
            last_evaluated_key = response.get("LastEvaluatedKey")
            return metadatas, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error listing receipt metadata: {e}")

    def getReceiptMetadatasByMerchant(
        self,
        merchant_name: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records from DynamoDB by merchant name with optional pagination.

        Args:
            merchant_name (str): The merchant name to filter by.
            limit (int, optional): Maximum number of records to retrieve.
            lastEvaluatedKey (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[ReceiptMetadata], dict | None]: A tuple containing the list of ReceiptMetadata records and the last evaluated key.
        """
        if merchant_name is None:
            raise ValueError("merchant_name cannot be None")
        if not isinstance(merchant_name, str):
            raise ValueError("merchant_name must be a string")
        normalized_merchant_name = merchant_name.upper().replace(" ", "_")
        gsi1_pk = f"MERCHANT#{normalized_merchant_name}"

        metadatas = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk",
                "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                "ExpressionAttributeValues": {":pk": {"S": gsi1_pk}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            metadatas.extend(
                itemToReceiptMetadata(item)
                for item in response.get("Items", [])
            )
            last_evaluated_key = response.get("LastEvaluatedKey")
            return metadatas, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error getting receipt metadata: {e}")

    def listReceiptMetadatasWithPlaceId(
        self,
        place_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records from DynamoDB that have a specific place_id.

        Uses GSI2 for efficient direct querying by place_id.

        Args:
            place_id (str): The place_id to query for.
            limit (int, optional): Maximum number of records to retrieve.
            lastEvaluatedKey (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[ReceiptMetadata], dict | None]: A tuple containing the list of ReceiptMetadata records and the last evaluated key.
        """
        if not place_id:
            raise ValueError("place_id cannot be empty")
        if not isinstance(place_id, str):
            raise ValueError("place_id must be a string")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary")

        metadatas = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"PLACE#{place_id}"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            metadatas.extend(
                itemToReceiptMetadata(item)
                for item in response.get("Items", [])
            )
            last_evaluated_key = response.get("LastEvaluatedKey")
            return metadatas, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error listing receipt metadata: {e}")

    def getReceiptMetadatasByConfidence(
        self,
        confidence: float,
        above: bool = True,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records from DynamoDB by confidence score with optional pagination.

        Args:
            confidence (float): The confidence score to filter by.
            above (bool, optional): Whether to filter above or below the confidence score.
            limit (int, optional): Maximum number of records to retrieve.
            lastEvaluatedKey (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[ReceiptMetadata], dict | None]: A tuple containing the list of ReceiptMetadata records and the last evaluated key.
        """
        if confidence is None:
            raise ValueError("confidence cannot be None")
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence < 0 or confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if above is not None and not isinstance(above, bool):
            raise ValueError("above must be a boolean")

        formatted_score = f"CONFIDENCE#{confidence:.4f}"

        if above:
            key_expr = "GSI2PK = :pk AND GSI2SK >= :sk"
        else:
            key_expr = "GSI2PK = :pk AND GSI2SK <= :sk"

        metadatas = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": key_expr,
                "ExpressionAttributeValues": {
                    ":pk": {"S": "MERCHANT_VALIDATION"},
                    ":sk": {"S": formatted_score},
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            metadatas.extend(
                itemToReceiptMetadata(item)
                for item in response.get("Items", [])
            )
            last_evaluated_key = response.get("LastEvaluatedKey")
            return metadatas, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    f"receipt_metadata contains invalid attributes or values: {e}"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error")
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded")
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found")
            else:
                raise ValueError(f"Error getting receipt metadata: {e}")
