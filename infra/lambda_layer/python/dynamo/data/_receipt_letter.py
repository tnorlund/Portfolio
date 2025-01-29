from dynamo import ReceiptLetter, itemToReceiptLetter
from botocore.exceptions import ClientError

CHUNK_SIZE = 25


class _ReceiptLetter:
    """
    A class used to represent a ReceiptLetter in the database (similar to _letter.py).

    Methods
    -------
    addReceiptLetter(letter: ReceiptLetter)
        Adds a receipt-letter to the database.
    addReceiptLetters(letters: list[ReceiptLetter])
        Adds multiple receipt-letters in batch.
    updateReceiptLetter(letter: ReceiptLetter)
        Updates an existing receipt-letter.
    deleteReceiptLetter(receipt_id: int, image_id: str, line_id: int, word_id: int, letter_id: int)
        Deletes a specific receipt-letter by IDs.
    deleteReceiptLetters(letters: list[ReceiptLetter])
        Deletes multiple receipt-letters in batch.
    getReceiptLetter(receipt_id: int, image_id: str, line_id: int, word_id: int, letter_id: int) -> ReceiptLetter
        Retrieves a single receipt-letter by IDs.
    listReceiptLetters() -> list[ReceiptLetter]
        Returns all ReceiptLetters from the table.
    listReceiptLettersFromWord(receipt_id: int, image_id: str, line_id: int, word_id: int) -> list[ReceiptLetter]
        Returns all ReceiptLetters for a given word.
    """

    def addReceiptLetter(self, letter: ReceiptLetter):
        """Adds a single ReceiptLetter to DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLetter with ID {letter.id} already exists")
            else:
                raise

    def addReceiptLetters(self, letters: list[ReceiptLetter]):
        """Adds multiple ReceiptLetters to DynamoDB in batches."""
        try:
            for i in range(0, len(letters), CHUNK_SIZE):
                chunk = letters[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": lt.to_item()}} for lt in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add ReceiptLetters to the database") from e

    def updateReceiptLetter(self, letter: ReceiptLetter):
        """Updates an existing ReceiptLetter in DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLetter with ID {letter.id} does not exist")
            else:
                raise

    def deleteReceiptLetter(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int, letter_id: int
    ):
        """Deletes a single ReceiptLetter by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLetter with ID {letter_id} not found")
            else:
                raise

    def deleteReceiptLetters(self, letters: list[ReceiptLetter]):
        """Deletes multiple ReceiptLetters in batch."""
        try:
            for i in range(0, len(letters), CHUNK_SIZE):
                chunk = letters[i : i + CHUNK_SIZE]
                request_items = [{"DeleteRequest": {"Key": lt.key()}} for lt in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete ReceiptLetters from the database") from e

    def getReceiptLetter(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int, letter_id: int
    ) -> ReceiptLetter:
        """Retrieves a single ReceiptLetter by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                    },
                },
            )
            return itemToReceiptLetter(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptLetter with ID {letter_id} not found")

    def listReceiptLetters(self) -> list[ReceiptLetter]:
        """Returns all ReceiptLetters from the table."""
        receipt_letters = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "RECEIPT_LETTER"}},
            )
            receipt_letters.extend(
                [itemToReceiptLetter(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "RECEIPT_LETTER"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_letters.extend(
                    [itemToReceiptLetter(item) for item in response["Items"]]
                )

            return receipt_letters
        except ClientError as e:
            raise ValueError("Could not list ReceiptLetters from the database") from e

    def listReceiptLettersFromWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> list[ReceiptLetter]:
        """Returns all ReceiptLetters for a given word."""
        receipt_letters = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": (
                            f"RECEIPT#{receipt_id:05d}"
                            f"#LINE#{line_id:05d}"
                            f"#WORD#{word_id:05d}"
                            f"#LETTER#"
                        )
                    },
                },
            )
            receipt_letters.extend(
                [itemToReceiptLetter(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {
                            "S": (
                                f"RECEIPT#{receipt_id:05d}"
                                f"#LINE#{line_id:05d}"
                                f"#WORD#{word_id:05d}"
                                f"#LETTER#"
                            )
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_letters.extend(
                    [itemToReceiptLetter(item) for item in response["Items"]]
                )
            return receipt_letters

        except ClientError as e:
            raise ValueError("Could not list ReceiptLetters from the database") from e
