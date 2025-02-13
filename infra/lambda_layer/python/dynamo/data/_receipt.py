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
    itemToGPTValidation,
    itemToGPTInitialTagging,
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
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValueError(f"Receipt with ID {receipt.receipt_id} and Image ID '{receipt.image_id}' already exists")
            else:
                raise e

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
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValueError(f"Receipt with ID {receipt.receipt_id} and Image ID '{receipt.image_id}' does not exist")
            else:
                raise e

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
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValueError(f"Receipt with ID {receipt.receipt_id} and Image ID '{receipt.image_id}' does not exists")
            else:
                raise e

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
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            )
            if "Item" in response:
                return itemToReceipt(response["Item"])
            else:
                raise ValueError(f"Receipt with ID {receipt_id} and Image ID '{image_id}' does not exist")

        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValueError(f"Receipt with ID {receipt_id} and Image ID '{image_id}' does not exist")
            else:
                raise e

    def getReceiptDetails(self, image_id: str, receipt_id: int) -> Tuple[
        Receipt,
        list[ReceiptLine],
        list[ReceiptWord],
        list[ReceiptLetter],
        list[ReceiptWordTag],
        list[dict],  # GPT validations
        list[dict],  # GPT initial taggings
    ]:
        """Get a receipt with its details

        Args:
            image_id (int): The ID of the image the receipt belongs to
            receipt_id (int): The ID of the receipt to get

        Returns:
            Tuple containing:
            - Receipt: The receipt object
            - list[ReceiptLine]: List of receipt lines
            - list[ReceiptWord]: List of receipt words
            - list[ReceiptLetter]: List of receipt letters
            - list[ReceiptWordTag]: List of receipt word tags
            - list[dict]: List of GPT validations
            - list[dict]: List of GPT initial taggings
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            )
            receipt = None
            lines = []
            words = []
            letters = []
            tags = []
            validations = []
            initial_taggings = []
            
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
                elif item["TYPE"]["S"] == "GPT_VALIDATION":
                    validations.append(itemToGPTValidation(item))
                elif item["TYPE"]["S"] == "GPT_INITIAL_TAGGING":
                    initial_taggings.append(itemToGPTInitialTagging(item))
                
            return receipt, lines, words, letters, tags, validations, initial_taggings
        except ClientError as e:
            raise ValueError(f"Error getting receipt details: {e}")

    def listReceipts(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[Receipt], dict | None]:
        """
        Lists receipts from the database.
        - If a limit is provided, performs one query (or page) and returns up to that many receipts plus the LastEvaluatedKey.
        - If no limit is provided, paginates until all receipts are returned.
        """
        receipts = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT"}},
            }
            # If a starting key is provided, add it.
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            
            # If a limit is provided, add it to the query parameters.
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            receipts.extend([itemToReceipt(item) for item in response["Items"]])

            if limit is None:
                # Paginate through all pages if no limit is provided.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipts.extend([itemToReceipt(item) for item in response["Items"]])
                # No further pages, so LEK is None.
                last_evaluated_key = None
            else:
                # If a limit was provided, return the LEK from the response (which will be None if this was the last page).
                last_evaluated_key = response.get("LastEvaluatedKey", None)
            
            return receipts, last_evaluated_key
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
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": "RECEIPT#"},
                },
            )
            receipts.extend([itemToReceipt(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"IMAGE#{image_id}"},
                        ":sk": {"S": "RECEIPT#"},
                    },
                )
                receipts.extend([itemToReceipt(item) for item in response["Items"]])
            return receipts
        except ClientError as e:
            raise ValueError(f"Error listing receipts from image: {e}")

    def listReceiptDetails(
        self, limit: Optional[int] = None, last_evaluated_key: Optional[dict] = None
    ) -> Tuple[Dict[str, Dict[str, Union[Receipt, List[ReceiptWord], List[ReceiptWordTag]]]], Optional[Dict]]:
        """List receipts with their words and word tags

        Args:
            limit (Optional[int], optional): The number of receipt details to return. Defaults to None.
            last_evaluated_key (Optional[dict], optional): The key to start the query from. Defaults to None.

        Returns:
            Tuple[Dict[str, Dict], Optional[Dict]]: A tuple containing:
                - Dictionary mapping "<image_id>_<receipt_id>" to receipt details 
                - Last evaluated key for pagination (None if no more pages)
        """
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "#pk = :pk_value",
                "ExpressionAttributeNames": {"#pk": "GSI2PK"},
                "ExpressionAttributeValues": {":pk_value": {"S": "RECEIPT"}},
                "ScanIndexForward": True,
            }
            
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            payload = {}
            current_receipt = None
            current_key = None
            receipt_count = 0

            while True:
                response = self._client.query(**query_params)
                
                for item in response["Items"]:
                    item_type = item["TYPE"]["S"]
                    
                    if item_type == "RECEIPT":
                        # If we've hit our limit, use this receipt's key as the LEK and stop
                        if limit is not None and receipt_count >= limit:
                            last_evaluated_key = {
                                "PK": item["PK"],
                                "SK": item["SK"],
                                "GSI2PK": item["GSI2PK"],
                                "GSI2SK": item["GSI2SK"],
                            }
                            return payload, last_evaluated_key
                        
                        receipt = itemToReceipt(item)
                        current_key = f"{receipt.image_id}_{receipt.receipt_id}"
                        payload[current_key] = {
                            "receipt": receipt,
                            "words": [],
                            "word_tags": []
                        }
                        current_receipt = receipt
                        receipt_count += 1
                    
                    elif item_type == "RECEIPT_WORD" and current_receipt:
                        word = itemToReceiptWord(item)
                        if word.image_id == current_receipt.image_id and word.receipt_id == current_receipt.receipt_id:
                            payload[current_key]["words"].append(word)
                    
                    elif item_type == "RECEIPT_WORD_TAG" and current_receipt:
                        tag = itemToReceiptWordTag(item)
                        if tag.image_id == current_receipt.image_id and tag.receipt_id == current_receipt.receipt_id:
                            payload[current_key]["word_tags"].append(tag)

                # If no more pages
                if "LastEvaluatedKey" not in response:
                    return payload, None
                    
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        except ClientError as e:
            raise ValueError("Could not list receipt details from the database") from e
