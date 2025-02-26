from receipt_dynamo import ReceiptLine, itemToReceiptLine
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
    deleteReceiptLine(receipt_id: int, image_id: str, line_id: int)
        Deletes a specific receipt-line by IDs.
    deleteReceiptLines(lines: list[ReceiptLine])
        Deletes multiple receipt-lines in batch.
    getReceiptLine(receipt_id: int, image_id: str, line_id: int) -> ReceiptLine
        Retrieves a single receipt-line by IDs.
    listReceiptLines() -> list[ReceiptLine]
        Returns all ReceiptLines from the table.
    listReceiptLinesFromReceipt(receipt_id: int, image_id: str) -> list[ReceiptLine]
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
                raise ValueError(f"ReceiptLine with ID {line.line_id} already exists")
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
                raise ValueError(f"ReceiptLine with ID {line.line_id} does not exist")
            else:
                raise

    def deleteReceiptLine(self, receipt_id: int, image_id: str, line_id: int):
        """Deletes a single ReceiptLine by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
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
        self, receipt_id: int, image_id: str, line_id: int
    ) -> ReceiptLine:
        """Retrieves a single ReceiptLine by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
                },
            )
            return itemToReceiptLine(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptLine with ID {line_id} not found")

    def listReceiptLines(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> list[ReceiptLine]:
        """Returns all ReceiptLines from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")
        receipt_lines = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_LINE"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_lines.extend(
                [itemToReceiptLine(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt lines.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipt_lines.extend(
                        [itemToReceiptLine(item) for item in response["Items"]]
                    )
                # No further pages left. LEK is None.
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_lines, last_evaluated_key
        
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt lines from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error listing receipt lines: {e}") from e

    def listReceiptLinesFromReceipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptLine]:
        """Returns all lines under a specific receipt/image."""
        receipt_lines = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
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
                        ":pk": {"S": f"IMAGE#{image_id}"},
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
