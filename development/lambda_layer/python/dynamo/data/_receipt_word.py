from dynamo import ReceiptWord, itemToReceiptWord
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptWord:
    """
    A class used to represent a ReceiptWord in the database (similar to _word.py).

    Methods
    -------
    addReceiptWord(word: ReceiptWord)
        Adds a receipt-word to the database.
    addReceiptWords(words: list[ReceiptWord])
        Adds multiple receipt-words in batch.
    updateReceiptWord(word: ReceiptWord)
        Updates an existing receipt-word.
    deleteReceiptWord(receipt_id: int, image_id: int, line_id: int, word_id: int)
        Deletes a specific receipt-word by IDs.
    deleteReceiptWords(words: list[ReceiptWord])
        Deletes multiple receipt-words in batch.
    deleteReceiptWordsFromLine(receipt_id: int, image_id: int, line_id: int)
        Deletes all words for the given receipt-line.
    getReceiptWord(receipt_id: int, image_id: int, line_id: int, word_id: int) -> ReceiptWord
        Retrieves a single receipt-word by IDs.
    listReceiptWords() -> list[ReceiptWord]
        Scans all receipt-words in the table.
    listReceiptWordsFromLine(receipt_id: int, image_id: int, line_id: int) -> list[ReceiptWord]
        Scans all words from a specific receipt-line.
    """

    def addReceiptWord(self, word: ReceiptWord):
        """Adds a single ReceiptWord to DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            # Check if it's a condition failure (duplicate key)
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptWord with ID {word.id} already exists")
            else:
                raise

    def addReceiptWords(self, words: list[ReceiptWord]):
        """Adds multiple ReceiptWords to DynamoDB in batches of CHUNK_SIZE."""
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": w.to_item()}} for w in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add ReceiptWords to the database") from e

    def updateReceiptWord(self, word: ReceiptWord):
        """Updates an existing ReceiptWord in DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptWord with ID {word.id} does not exist")
            else:
                raise

    def deleteReceiptWord(self, receipt_id: int, image_id: int, line_id: int, word_id: int):
        """Deletes a single ReceiptWord by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptWord with ID {word_id} not found")
            else:
                raise

    def deleteReceiptWords(self, words: list[ReceiptWord]):
        """Deletes multiple ReceiptWords in batch."""
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [{"DeleteRequest": {"Key": w.key()}} for w in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete ReceiptWords from the database") from e

    def deleteReceiptWordsFromLine(self, receipt_id: int, image_id: int, line_id: int):
        """Deletes all ReceiptWords from a given line within a receipt/image."""
        words = self.listReceiptWordsFromLine(receipt_id, image_id, line_id)
        self.deleteReceiptWords(words)

    def getReceiptWord(
        self, receipt_id: int, image_id: int, line_id: int, word_id: int
    ) -> ReceiptWord:
        """Retrieves a single ReceiptWord by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
            )
            return itemToReceiptWord(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptWord with ID {word_id} not found")

    def listReceiptWords(self) -> list[ReceiptWord]:
        """Returns all ReceiptWords from the table (scans for TYPE == 'RECEIPT_WORD')."""
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "TYPE": {
                    "AttributeValueList": [{"S": "RECEIPT_WORD"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToReceiptWord(item) for item in response["Items"]]

    def listReceiptWordsFromLine(
        self, receipt_id: int, image_id: int, line_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image/line IDs."""
        # Note: This uses a ScanFilter with PK=IMAGE#NNN and SK begins with RECEIPT#...LINE#...
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "PK": {
                    "AttributeValueList": [{"S": f"IMAGE#{image_id:05d}"}],
                    "ComparisonOperator": "EQ",
                },
                "SK": {
                    "AttributeValueList": [
                        {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"}
                    ],
                    "ComparisonOperator": "BEGINS_WITH",
                },
            },
        )
        return [itemToReceiptWord(item) for item in response["Items"]]
