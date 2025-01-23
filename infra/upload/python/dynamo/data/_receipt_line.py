from dynamo import ReceiptLine, itemToReceiptLine
from botocore.exceptions import ClientError

CHUNK_SIZE = 25


class _ReceiptLine:
    """
    A class used to represent a ReceiptLine in the database (similar to _line.py).

    Methods
    -------
    addReceiptLine(line: ReceiptLine)
        Adds a receipt-line to the database.
    addReceiptLines(lines: list[ReceiptLine])
        Adds multiple receipt-lines in batch.
    updateReceiptLine(line: ReceiptLine)
        Updates an existing receipt-line.
    deleteReceiptLine(receipt_id: int, image_id: int, line_id: int)
        Deletes a specific receipt-line by IDs.
    deleteReceiptLines(lines: list[ReceiptLine])
        Deletes multiple receipt-lines in batch.
    getReceiptLine(receipt_id: int, image_id: int, line_id: int) -> ReceiptLine
        Retrieves a single receipt-line by IDs.
    listReceiptLines() -> list[ReceiptLine]
        Returns all ReceiptLines from the table.
    listReceiptLinesFromReceipt(receipt_id: int, image_id: int) -> list[ReceiptLine]
        Returns all lines under a specific receipt/image.
    """

    def addReceiptLine(self, line: ReceiptLine):
        """Adds a single ReceiptLine to DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=line.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLine with ID {line.id} already exists")
            else:
                raise

    def addReceiptLines(self, lines: list[ReceiptLine]):
        """Adds multiple ReceiptLines to DynamoDB in batches of CHUNK_SIZE."""
        try:
            for i in range(0, len(lines), CHUNK_SIZE):
                chunk = lines[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": ln.to_item()}} for ln in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add ReceiptLines to the database") from e

    def updateReceiptLine(self, line: ReceiptLine):
        """Updates an existing ReceiptLine in DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=line.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLine with ID {line.id} does not exist")
            else:
                raise

    def deleteReceiptLine(self, receipt_id: int, image_id: int, line_id: int):
        """Deletes a single ReceiptLine by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"ReceiptLine with ID {line_id} not found")
            else:
                raise

    def deleteReceiptLines(self, lines: list[ReceiptLine]):
        """Deletes multiple ReceiptLines in batch."""
        try:
            for i in range(0, len(lines), CHUNK_SIZE):
                chunk = lines[i : i + CHUNK_SIZE]
                request_items = [{"DeleteRequest": {"Key": ln.key()}} for ln in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete ReceiptLines from the database") from e

    def getReceiptLine(
        self, receipt_id: int, image_id: int, line_id: int
    ) -> ReceiptLine:
        """Retrieves a single ReceiptLine by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
                },
            )
            return itemToReceiptLine(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptLine with ID {line_id} not found")

    def listReceiptLines(self) -> list[ReceiptLine]:
        """Returns all ReceiptLines from the table."""
        receipt_lines = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "RECEIPT_LINE"}},
            )
            receipt_lines.extend(
                [itemToReceiptLine(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "RECEIPT_LINE"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_lines.extend(
                    [itemToReceiptLine(item) for item in response["Items"]]
                )

            return receipt_lines
        except ClientError as e:
            raise ValueError("Could not list ReceiptLines from the database") from e

    def listReceiptLinesFromReceipt(
        self, receipt_id: int, image_id: int
    ) -> list[ReceiptLine]:
        """Returns all lines under a specific receipt/image."""
        receipt_lines = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id:05d}"},
                    ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                },
            )
            receipt_lines.extend(
                [itemToReceiptLine(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"IMAGE#{image_id:05d}"},
                        ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_lines.extend(
                    [itemToReceiptLine(item) for item in response["Items"]]
                )

            return receipt_lines
        except ClientError as e:
            raise ValueError("Could not list ReceiptLines from the database") from e
