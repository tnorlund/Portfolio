from dynamo import Receipt, itemToReceipt
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Receipt:
    def addReceipt(self, receipt: Receipt):
        """Adds a receipt to the database

        Args:
            receipt (Receipt): The receipt to add to the database

        Raises:
            ValueError: When a receipt with the same ID already exists
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError:
            raise ValueError(f"Receipt with ID {receipt.id} already exists")

    def addReceipts(self, receipts: list[Receipt]):
        """Adds a list of receipts to the database

        Args:
            receipts (list[Receipt]): The receipts to add to the database

        Raises:
            ValueError: When a receipt with the same ID already exists
        """
        try:
            for i in range(0, len(receipts), CHUNK_SIZE):
                chunk = receipts[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": receipt.to_item()}} for receipt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error adding receipts: {e}")

    def updateReceipt(self, receipt: Receipt):
        """Updates a receipt in the database

        Args:
            receipt (Receipt): The receipt to update in the database

        Raises:
            ValueError: When the receipt does not exist
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Receipt with ID {receipt.id} does not exist")

    def updateReceipts(self, receipts: list[Receipt]):
        """Updates a list of receipts in the database

        Args:
            receipts (list[Receipt]): The receipts to update in the database

        Raises:
            ValueError: When a receipt does not exist
        """
        try:
            for i in range(0, len(receipts), CHUNK_SIZE):
                chunk = receipts[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": receipt.to_item()}} for receipt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error updating receipts: {e}")

    def deleteReceipt(self, receipt: Receipt):
        """Deletes a receipt from the database

        Args:
            receipt (Receipt): The receipt to delete from the database

        Raises:
            ValueError: When the receipt does not exist
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=receipt.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Receipt with ID {receipt.id} does not exist")

    def deleteReceipts(self, receipts: list[Receipt]):
        """Deletes a list of receipts from the database

        Args:
            receipts (list[Receipt]): The receipts to delete from the database

        Raises:
            ValueError: When a receipt does not exist
        """
        try:
            for i in range(0, len(receipts), CHUNK_SIZE):
                chunk = receipts[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": receipt.key()}} for receipt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error deleting receipts: {e}")

    def deleteReceiptsFromImage(self, image_id: int):
        """Deletes all receipts from an image

        Args:
            image_id (int): The ID of the image to delete receipts from

        Raises:
            ValueError: When there is an error deleting receipts from the image or no receipts found
        """
        try:
            receipts_from_image = self.getReceiptsFromImage(image_id)
            if not receipts_from_image:
                raise ValueError(f"No receipts found for image ID {image_id}")
            self.deleteReceipts(receipts_from_image)
        except ClientError as e:
            raise ValueError(f"Error deleting receipts from image: {e}")

    def getReceipt(self, image_id: int, receipt_id: int) -> Receipt:
        """Get a receipt from the database

        Args:
            image_id (int): The ID of the image the receipt belongs to
            receipt_id (int): The ID of the receipt to get

        Returns:
            Receipt: The receipt object
        """
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            )
            return itemToReceipt(response["Item"])
        except KeyError:
            raise ValueError(f"Receipt with ID {receipt_id} not found")

    def getReceiptsFromImage(self, image_id: int) -> list[Receipt]:
        """List all receipts from an image using the GSI

        Args:
            image_id (int): The ID of the image to list receipts from

        Returns:
            list[Receipt]: A list of receipts from the image
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id:05d}"},
                    ":sk": {"S": "RECEIPT#"},
                },
            )
            return [itemToReceipt(item) for item in response["Items"]]
        except ClientError as e:
            raise ValueError(f"Error listing receipts from image: {e}")
