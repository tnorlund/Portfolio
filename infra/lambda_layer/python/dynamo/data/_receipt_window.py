from typing import List
from botocore.exceptions import ClientError
from dynamo.entities.receipt_window import ReceiptWindow


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
            raise ValueError(
                "receipt_window must be an instance of the ReceiptWindow class."
            )

        # Add the receipt window to the database.
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_window.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptWindow with Image ID {receipt_window.image_id}, "
                    f"Receipt ID {receipt_window.receipt_id}, "
                    f"and corner name {receipt_window.corner_name} already exists"
                ) from e
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
                request_items = [
                    {"PutRequest": {"Item": rw.to_item()}} for rw in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(f"One or more parameters given were invalid: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error adding receipt windows: {e}") from e



