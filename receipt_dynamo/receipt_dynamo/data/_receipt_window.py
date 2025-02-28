# infra/lambda_layer/python/dynamo/data/_receipt_window.py
from typing import List

from botocore.exceptions import ClientError

from receipt_dynamo.entities.receipt_window import (ReceiptWindow,
    itemToReceiptWindow, )


class _ReceiptWindow:
    """
    A class providing methods to interact with "ReceiptWindow" entities in DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    receipt window records.
    """

    def addReceiptWindow(self, receipt_window: ReceiptWindow):
        """
        Adds a ReceiptWindow item to the database.

        Uses a conditional put to ensure that the item does not overwrite
        an existing receipt window with the same ID.
        """
        # Validate the receipt window parameter.
        if receipt_window is None:
            raise ValueError("ReceiptWindow parameter is required and cannot be None.")
        if not isinstance(receipt_window, ReceiptWindow):
            raise ValueError("receipt_window must be an instance of the ReceiptWindow class.")

        # Add the receipt window to the database.
        try:
            self._client.put_item(TableName=self.table_name,
                Item=receipt_window.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)", )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptWindow with Image ID {receipt_window.image_id}, "
                    f"Receipt ID {receipt_window.receipt_id}, "
                    f"and corner name {receipt_window.corner_name} already exists") from e
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add receipt window to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error adding receipt window: {e}") from e

    def addReceiptWindows(self, receipt_windows: List[ReceiptWindow]):
        """
        Adds multiple ReceiptWindow items to the database in batches of up to 25.

        This method validates that the provided parameter is a list of ReceiptWindow instances.
        It uses DynamoDB's batch_write_item operation, which can handle up to 25 items
        per batch. Any unprocessed items are automatically retried until no unprocessed
        items remain.
        """
        if receipt_windows is None:
            raise ValueError("receipt_windows parameter is required and cannot be None.")
        if not isinstance(receipt_windows, list):
            raise ValueError("receipt_windows must be provided as a list.")
        if not all(isinstance(rw, ReceiptWindow) for rw in receipt_windows):
            raise ValueError("All items in the receipt_windows list must be instances of the ReceiptWindow class.")

        try:
            for i in range(0, len(receipt_windows), 25):
                chunk = receipt_windows[i : i + 25]
                request_items = [{"PutRequest": {"Item": rw.to_item()}} for rw in chunk]
                response = self._client.batch_write_item(RequestItems={self.table_name: request_items})
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add receipt windows to DynamoDB: {e}") from e
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(f"One or more parameters given were invalid: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error adding receipt windows: {e}") from e

    def deleteReceiptWindows(self, receipt_windows: List[ReceiptWindow]) -> None:
        """
        Deletes multiple ReceiptWindow items from the database in batches of up to 25.

        Any unprocessed items are automatically retried until none remain.
        """
        if receipt_windows is None:
            raise ValueError("receipt_windows parameter is required and cannot be None.")
        if not isinstance(receipt_windows, list):
            raise ValueError("receipt_windows must be provided as a list.")
        if not all(isinstance(rw, ReceiptWindow) for rw in receipt_windows):
            raise ValueError("All items in the receipt_windows list must be instances of the ReceiptWindow class.")
        try:
            for i in range(0, len(receipt_windows), 25):
                chunk = receipt_windows[i : i + 25]
                request_items = [{"DeleteRequest": {"Key": rw.key()}} for rw in chunk]
                response = self._client.batch_write_item(RequestItems={self.table_name: request_items})
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not delete receipt windows from DynamoDB: {e}") from e
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(f"One or more parameters given were invalid: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error deleting receipt windows: {e}") from e

    def listReceiptWindows(self, limit: int = None, lastEvaluatedKey: dict = None) -> tuple[list[ReceiptWindow], dict | None]:
        """
        Lists all receipt windows in the database.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")
        receipt_windows = []
        try:
            query_params = {"TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_WINDOW"}}, }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_windows.extend([itemToReceiptWindow(item)
                    for item in response.get("Items", [])])

            if limit is None:
                # Paginate through all the receipt windows.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipt_windows.extend([itemToReceiptWindow(item)
                            for item in response.get("Items", [])])
                # No further pages left. LEK is None.
                last_evaluated_key = None
            else:
                # if limit is not None, we've reached the end of the paginated
                # results.
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_windows, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not list receipt windows from DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(f"One or more parameters given were invalid: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error listing receipt windows: {e}") from e
