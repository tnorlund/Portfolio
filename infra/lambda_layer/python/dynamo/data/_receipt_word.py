from dynamo import ReceiptWord, itemToReceiptWord
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptWord:
    """
    A class used to represent a ReceiptWord in the database.

    Methods
    -------
    addReceiptWord(word: ReceiptWord)
        Adds a single ReceiptWord.
    addReceiptWords(words: list[ReceiptWord])
        Adds multiple ReceiptWords.
    updateReceiptWord(word: ReceiptWord)
        Updates a ReceiptWord.
    deleteReceiptWord(receipt_id: int, image_id: str, line_id: int, word_id: int)
        Deletes a single ReceiptWord by IDs.
    deleteReceiptWords(words: list[ReceiptWord])
        Deletes multiple ReceiptWords.
    deleteReceiptWordsFromLine(receipt_id: int, image_id: str, line_id: int)
        Deletes all ReceiptWords from a given line within a receipt/image.
    getReceiptWord(receipt_id: int, image_id: str, line_id: int, word_id: int) -> ReceiptWord
        Retrieves a single ReceiptWord by IDs.
    listReceiptWords() -> list[ReceiptWord]
        Returns all ReceiptWords from the table.
    listReceiptWordsFromLine(receipt_id: int, image_id: str, line_id: int) -> list[ReceiptWord]
        Returns all ReceiptWords that match the given receipt/image/line IDs.
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
                raise Exception(f"Could not add ReceiptWord to the database: {e}")

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
            raise ValueError(f"Could not add ReceiptWords to the database: {e}")

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
                raise Exception(f"Could not update ReceiptWord in the database: {e}")
    
    def updateReceiptWords(self, words: list[ReceiptWord]):
        """Updates multiple existing ReceiptWords in DynamoDB."""
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
            raise ValueError(f"Could not update ReceiptWords in the database: {e}")

    def deleteReceiptWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ):
        """Deletes a single ReceiptWord by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
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
            raise ValueError(f"Could not delete ReceiptWords from the database: {e}")

    def deleteReceiptWordsFromLine(self, receipt_id: int, image_id: str, line_id: int):
        """Deletes all ReceiptWords from a given line within a receipt/image."""
        words = self.listReceiptWordsFromLine(receipt_id, image_id, line_id)
        self.deleteReceiptWords(words)

    def getReceiptWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> ReceiptWord:
        """Retrieves a single ReceiptWord by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
            )
            return itemToReceiptWord(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptWord with ID {word_id} not found")

    def listReceiptWords(self) -> list[ReceiptWord]:
        """Returns all ReceiptWords from the table."""
        receipt_words = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "RECEIPT_WORD"}},
            )
            receipt_words.extend(
                [itemToReceiptWord(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "RECEIPT_WORD"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_words.extend(
                    [itemToReceiptWord(item) for item in response["Items"]]
                )
            return receipt_words
        except ClientError as e:
            raise ValueError(f"Could not list ReceiptWords from the database {e}")

    def listReceiptWordsFromLine(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image/line IDs."""
        receipt_words = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                    },
                },
            )
            receipt_words.extend(
                [itemToReceiptWord(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                    ExpressionAttributeValues={
                        ":pk_val": {"S": f"IMAGE#{image_id}"},
                        ":sk_val": {
                            "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_words.extend(
                    [itemToReceiptWord(item) for item in response["Items"]]
                )
            return receipt_words
        except ClientError as e:
            raise ValueError(f"Could not list ReceiptWords from the database: {e}")
