from typing import Dict, List, Optional, Tuple, Union
from dynamo import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
    itemToReceipt,
    itemToReceiptLine,
    itemToReceiptWord,
    itemToReceiptLetter,
    itemToReceiptWordTag,
)
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


def _parse_receipt_details(
    items: list[dict], receipt_index: int = 1
) -> Dict[int, Dict[str, Union[Receipt, List[ReceiptWord], List[ReceiptWordTag]]]]:
    payload = {}
    for item in items:
        if item["TYPE"]["S"] == "RECEIPT":
            receipt = itemToReceipt(item)
            payload[receipt_index] = {
                "receipt": receipt,
                "words": [],
            }
            receipt_index += 1
        elif item["TYPE"]["S"] == "RECEIPT_WORD":
            word = itemToReceiptWord(item)
            payload[receipt_index - 1]["words"].append(word)
    return payload


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

    def getReceiptDetails(self, image_id: int, receipt_id: int) -> Tuple[
        Receipt,
        list[ReceiptLine],
        list[ReceiptWord],
        list[ReceiptLetter],
        list[ReceiptWordTag],
    ]:
        """Get a receipt with its details

        Args:
            image_id (int): The ID of the image the receipt belongs to
            receipt_id (int): The ID of the receipt to get

        Returns:
            dict: A dictionary containing the receipt and its details
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id:05d}"},
                    ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            )
            receipt = None
            lines = []
            words = []
            letters = []
            tags = []
            for item in response["Items"]:
                if item["TYPE"]["S"] == "RECEIPT":
                    receipt = itemToReceipt(item)
                elif item["TYPE"]["S"] == "RECEIPT_LINE":
                    lines.append(itemToReceiptLine(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD":
                    words.append(itemToReceiptWord(item))
                elif item["TYPE"]["S"] == "RECEIPT_LETTER":
                    letters.append(itemToReceiptLetter(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD_TAG":
                    tags.append(itemToReceiptWordTag(item))
            return receipt, lines, words, letters, tags
        except ClientError as e:
            raise ValueError(f"Error getting receipt details: {e}")

    def listReceipts(self, limit: Optional[int] = None) -> list[Receipt]:
        """List all receipts from the table, optionally limiting the total number returned."""
        receipts = []

        try:
            # Initial query parameters
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT"}},
            }

            # First page
            response = self._client.query(**query_params)
            receipts.extend([itemToReceipt(item) for item in response["Items"]])

            # Keep going only if:
            #   1) There are more pages to read (LastEvaluatedKey exists), AND
            #   2) We haven't yet reached the user's limit (if specified).
            while "LastEvaluatedKey" in response and (limit is None or len(receipts) < limit):
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self._client.query(**query_params)
                receipts.extend([itemToReceipt(item) for item in response["Items"]])

            # If a limit is specified, slice the list down to that limit.
            if limit is not None:
                receipts = receipts[:limit]

            return receipts

        except ClientError as e:
            raise ValueError(f"Could not list receipts from the database: {e}")

    def getReceiptsFromImage(self, image_id: int) -> list[Receipt]:
        """List all receipts from an image using the GSI

        Args:
            image_id (int): The ID of the image to list receipts from

        Returns:
            list[Receipt]: A list of receipts from the image
        """
        receipts = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id:05d}"},
                    ":sk": {"S": "RECEIPT#"},
                },
            )
            receipts.extend([itemToReceipt(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"IMAGE#{image_id:05d}"},
                        ":sk": {"S": "RECEIPT#"},
                    },
                )
                receipts.extend([itemToReceipt(item) for item in response["Items"]])
            return receipts
        except ClientError as e:
            raise ValueError(f"Error listing receipts from image: {e}")

    def listReceiptDetails(
        self, limit: Optional[int] = None, last_evaluated_key: Optional[dict] = None
    ) -> Tuple[
        Dict[int, Dict[str, Union[Receipt, List[ReceiptWord], List[ReceiptWordTag]]]],
        Optional[Dict],
    ]:
        """List all receipts with their details

        Args:
            limit (Optional[int], optional): The number of receipts and respective ReceiptWords and ReceiptWordTags to return. Defaults to None.
            last_evaluated_key (Optional[dict], optional): The key to start the query from. Defaults to None.

        Returns:
            list[dict]: A list of receipts with their details
        """
        if limit is None and last_evaluated_key is None:
            all_items = []
            try:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI2",
                    KeyConditionExpression="#pk = :pk_value",
                    ExpressionAttributeNames={"#pk": "GSI2PK"},
                    ExpressionAttributeValues={
                        ":pk_value": {"S": f"RECEIPT"},
                    },
                    ScanIndexForward=True,  # This ensures the sorting by GSI2SK in ascending order
                )
                all_items.extend(response["Items"])
                while "LastEvaluatedKey" in response:
                    response = self._client.query(
                        TableName=self.table_name,
                        IndexName="GSI2",
                        KeyConditionExpression="#pk = :pk_value",
                        ExpressionAttributeNames={"#pk": "GSI2PK"},
                        ExpressionAttributeValues={
                            ":pk_value": {"S": f"RECEIPT"},
                        },
                        ScanIndexForward=True,
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                    )
                    all_items.extend(response["Items"])
                payload = _parse_receipt_details(all_items)
                return payload, None

            except ClientError as e:
                raise ValueError(
                    "Could not list receipt details from the database"
                ) from e
        else:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "#pk = :pk_value",
                "ExpressionAttributeNames": {"#pk": "GSI2PK"},
                "ExpressionAttributeValues": {":pk_value": {"S": f"RECEIPT"}},
                "ScanIndexForward": True,
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            response = self._client.query(**query_params)
            receipts = [
                itemToReceipt(item)
                for item in response["Items"]
                if item["TYPE"]["S"] == "RECEIPT"
            ]
            receipt_words = [
                itemToReceiptWord(item)
                for item in response["Items"]
                if item["TYPE"]["S"] == "RECEIPT_WORD"
            ]
            # sort Receipt Words by receipt_id and line_id and word_id
            receipt_words.sort(key=lambda x: (x.receipt_id, x.line_id, x.id))
            num_receipts = len(receipts)
            if num_receipts > limit and "LastEvaluatedKey" not in response:
                payload = {}
                for i, receipt in enumerate(receipts[:limit]):
                    payload[i + 1] = {
                        "receipt": receipt,
                        "words": [
                            word
                            for word in receipt_words
                            if word.receipt_id == receipt.id
                            and word.image_id == receipt.image_id
                        ],
                    }
                last_word = payload[limit]["words"][-1]
                last_evaluated_key = {**last_word.key(), **last_word.gsi2_key()}
                return payload, last_evaluated_key
            else:
                while num_receipts < limit and "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipts.extend(
                        [
                            itemToReceipt(item)
                            for item in response["Items"]
                            if item["TYPE"]["S"] == "RECEIPT"
                        ]
                    )
                    receipt_words.extend(
                        [
                            itemToReceiptWord(item)
                            for item in response["Items"]
                            if item["TYPE"]["S"] == "RECEIPT_WORD"
                        ]
                    )
                    receipt_words.sort(key=lambda x: (x.receipt_id, x.line_id, x.id))
                    num_receipts = len(receipts)
                payload = {}
                for i, receipt in enumerate(receipts[:limit]):
                    payload[i + 1] = {
                        "receipt": receipt,
                        "words": [
                            word
                            for word in receipt_words
                            if word.receipt_id == receipt.id
                            and word.image_id == receipt.image_id
                        ],
                    }
                last_word = payload[len(payload)]["words"][-1]
                last_evaluated_key = {**last_word.key(), **last_word.gsi2_key()}
                return payload, last_evaluated_key
