from typing import Dict, List, Optional, Tuple, Union

from botocore.exceptions import ClientError

from receipt_dynamo.entities.receipt_field import (
    ReceiptField,
    itemToReceiptField,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: dict) -> None:
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


class _ReceiptField:
    def addReceiptField(self, receipt_field: ReceiptField):
        """Adds a receipt field to the database

        Args:
            receipt_field (ReceiptField): The receipt field to add to the database

        Raises:
            ValueError: When a receipt field with the same ID already exists
        """
        if receipt_field is None:
            raise ValueError(
                "ReceiptField parameter is required and cannot be None."
            )
        if not isinstance(receipt_field, ReceiptField):
            raise ValueError(
                "receipt_field must be an instance of the ReceiptField class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_field.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt field for Image ID '{receipt_field.image_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt field to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not add receipt field to DynamoDB: {e}"
                ) from e

    def addReceiptFields(self, receipt_fields: list[ReceiptField]):
        """Adds a list of receipt fields to the database

        Args:
            receipt_fields (list[ReceiptField]): The receipt fields to add to the database

        Raises:
            ValueError: When a receipt field with the same ID already exists
        """
        if receipt_fields is None:
            raise ValueError(
                "ReceiptFields parameter is required and cannot be None."
            )
        if not isinstance(receipt_fields, list):
            raise ValueError(
                "receipt_fields must be a list of ReceiptField instances."
            )
        if not all(
            isinstance(field, ReceiptField) for field in receipt_fields
        ):
            raise ValueError(
                "All receipt fields must be instances of the ReceiptField class."
            )
        try:
            for i in range(0, len(receipt_fields), 25):
                chunk = receipt_fields[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": field.to_item()}}
                    for field in chunk
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
                raise ValueError(f"Error adding receipt fields: {e}")

    def updateReceiptField(self, receipt_field: ReceiptField):
        """Updates a receipt field in the database

        Args:
            receipt_field (ReceiptField): The receipt field to update in the database

        Raises:
            ValueError: When the receipt field does not exist
        """
        if receipt_field is None:
            raise ValueError(
                "ReceiptField parameter is required and cannot be None."
            )
        if not isinstance(receipt_field, ReceiptField):
            raise ValueError(
                "receipt_field must be an instance of the ReceiptField class."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_field.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt field for Image ID '{receipt_field.image_id}' does not exist"
                )
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
                raise ValueError(f"Error updating receipt field: {e}")

    def updateReceiptFields(self, receipt_fields: list[ReceiptField]):
        """
        Updates a list of receipt fields in the database using transactions.
        Each receipt field update is conditional upon the field already existing.

        Args:
            receipt_fields (list[ReceiptField]): The receipt fields to update in the database.

        Raises:
            ValueError: When given a bad parameter or if a field doesn't exist.
            Exception: For underlying DynamoDB errors.
        """
        if receipt_fields is None:
            raise ValueError(
                "ReceiptFields parameter is required and cannot be None."
            )
        if not isinstance(receipt_fields, list):
            raise ValueError(
                "receipt_fields must be a list of ReceiptField instances."
            )
        if not all(
            isinstance(field, ReceiptField) for field in receipt_fields
        ):
            raise ValueError(
                "All receipt fields must be instances of the ReceiptField class."
            )

        # Process fields in chunks of 25 because transact_write_items
        # supports a maximum of 25 operations.
        for i in range(0, len(receipt_fields), 25):
            chunk = receipt_fields[i : i + 25]
            transact_items = []
            for field in chunk:
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": field.to_item(),
                            "ConditionExpression": "attribute_exists(PK)",
                        }
                    }
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "One or more receipt fields do not exist"
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
                    raise ValueError(
                        f"Error updating receipt fields: {e}"
                    ) from e

    def deleteReceiptField(self, receipt_field: ReceiptField):
        """Deletes a receipt field from the database

        Args:
            receipt_field (ReceiptField): The receipt field to delete from the database

        Raises:
            ValueError: When the receipt field does not exist
        """
        if receipt_field is None:
            raise ValueError(
                "ReceiptField parameter is required and cannot be None."
            )
        if not isinstance(receipt_field, ReceiptField):
            raise ValueError(
                "receipt_field must be an instance of the ReceiptField class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=receipt_field.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Receipt field for Image ID '{receipt_field.image_id}' does not exist"
                )
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
                raise ValueError(f"Error deleting receipt field: {e}") from e

    def deleteReceiptFields(self, receipt_fields: list[ReceiptField]):
        """
        Deletes a list of receipt fields from the database using transactions.
        Each delete operation is conditional upon the field existing.

        Args:
            receipt_fields (list[ReceiptField]): The receipt fields to delete from the database.

        Raises:
            ValueError: When a receipt field does not exist or if another error occurs.
        """
        if receipt_fields is None:
            raise ValueError(
                "ReceiptFields parameter is required and cannot be None."
            )
        if not isinstance(receipt_fields, list):
            raise ValueError(
                "receipt_fields must be a list of ReceiptField instances."
            )
        if not all(
            isinstance(field, ReceiptField) for field in receipt_fields
        ):
            raise ValueError(
                "All receipt fields must be instances of the ReceiptField class."
            )

        try:
            # Process fields in chunks of 25 items (the maximum allowed per
            # transaction)
            for i in range(0, len(receipt_fields), 25):
                chunk = receipt_fields[i : i + 25]
                transact_items = []
                for field in chunk:
                    transact_items.append(
                        {
                            "Delete": {
                                "TableName": self.table_name,
                                "Key": field.key(),
                                "ConditionExpression": "attribute_exists(PK)",
                            }
                        }
                    )
                # Execute the transaction for this chunk.
                self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    "One or more receipt fields do not exist"
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
                raise ValueError(f"Error deleting receipt fields: {e}") from e

    def getReceiptField(
        self, field_type: str, image_id: str, receipt_id: int
    ) -> ReceiptField:
        """
        Retrieves a receipt field from the database.

        Args:
            field_type (str): The type of field to retrieve.
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt.

        Returns:
            ReceiptField: The receipt field object.

        Raises:
            ValueError: If input parameters are invalid or if the field does not exist.
            Exception: For underlying DynamoDB errors.
        """
        if field_type is None:
            raise ValueError("Field type is required and cannot be None.")
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if receipt_id is None:
            raise ValueError("Receipt ID is required and cannot be None.")

        # Validate image_id as a UUID and receipt_id as a positive integer
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")
        if not isinstance(field_type, str) or not field_type:
            raise ValueError("Field type must be a non-empty string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"FIELD#{field_type.upper()}"},
                    "SK": {"S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"},
                },
            )
            if "Item" in response:
                return itemToReceiptField(response["Item"])
            else:
                raise ValueError(
                    f"Receipt field for Field Type '{field_type}', Image ID '{image_id}', and Receipt ID {receipt_id} does not exist."
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
                raise Exception(f"Error getting receipt field: {e}") from e

    def listReceiptFields(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt field records from the database with support for precise pagination.

        Parameters:
            limit (int, optional): The maximum number of receipt field items to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of ReceiptField objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises:
            ValueError: If the limit is not an integer or is less than or equal to 0.
            ValueError: If the lastEvaluatedKey is not a dictionary.
            Exception: If the underlying database query fails.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        fields = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_FIELD"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [itemToReceiptField(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt fields from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not list receipt fields from the database: {e}"
                ) from e

    def getReceiptFieldsByImage(
        self,
        image_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt fields by image ID using GSI1.

        Args:
            image_id (str): The image ID to search for
            limit (int, optional): The maximum number of fields to return
            lastEvaluatedKey (dict, optional): The key to start the query from

        Returns:
            tuple[list[ReceiptField], dict | None]: A tuple containing:
                - List of ReceiptField objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            ValueError: If the image_id is invalid or if pagination parameters are invalid
            Exception: For underlying DynamoDB errors
        """
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")
        assert_valid_uuid(image_id)
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        fields = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [itemToReceiptField(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt fields by image ID: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not list receipt fields by image ID: {e}"
                ) from e

    def getReceiptFieldsByReceipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt fields by receipt ID using GSI1.

        Args:
            image_id (str): The image ID to search for
            receipt_id (int): The receipt ID to search for
            limit (int, optional): The maximum number of fields to return
            lastEvaluatedKey (dict, optional): The key to start the query from

        Returns:
            tuple[list[ReceiptField], dict | None]: A tuple containing:
                - List of ReceiptField objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            ValueError: If the image_id or receipt_id is invalid or if pagination parameters are invalid
            Exception: For underlying DynamoDB errors
        """
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        fields = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk AND begins_with(GSI1SK, :sk_prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [itemToReceiptField(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt fields by receipt ID: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not list receipt fields by receipt ID: {e}"
                ) from e
