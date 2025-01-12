from dynamo import Line, itemToLine
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Line:
    """
    A class used to represent a Line in the database.

    Methods
    -------
    addLine(line: Line)
        Adds a line to the database.

    """

    def addLine(self, line: Line):
        """Adds a line to the database

        Args:
            line (Line): The line to add to the database

        Raises:
            ValueError: When a line with the same ID already
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=line.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Line with ID {line.id} already exists")

    def addLines(self, lines: list[Line]):
        """Adds a list of lines to the database

        Args:
            lines (list[Line]): The lines to add to the database

        Raises:
            ValueError: When a line with the same ID already
        """
        try:
            for i in range(0, len(lines), CHUNK_SIZE):
                chunk = lines[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": line.to_item()}} for line in chunk
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
            raise ValueError(f"Could not add lines to the database")

    def updateLine(self, line: Line):
        """Updates a line in the database

        Args:
            line (Line): The line to update in the database
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=line.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Line with ID {line.id} not found")
            else:
                raise Exception(f"Error updating line: {e}")

    def deleteLine(self, image_id: int, line_id: int):
        """Deletes a line from the database

        Args:
            image_id (int): The ID of the image the line belongs to
            line_id (int): The ID of the line to delete
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"LINE#{line_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Line with ID {line_id} not found")

    def deleteLines(self, lines: list[Line]):
        """Deletes a list of lines from the database

        Args:
            lines (list[Line]): The lines to delete
        """
        try:
            for i in range(0, len(lines), CHUNK_SIZE):
                chunk = lines[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": line.key()}} for line in chunk
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
            raise ValueError(f"Could not delete lines from the database")

    def deleteLinesFromImage(self, image_id: int):
        """Deletes all lines from an image

        Args:
            image_id (int): The ID of the image to delete lines from
        """
        lines = self.listLinesFromImage(image_id)
        self.deleteLines(lines)

    def getLine(self, image_id: int, line_id: int) -> Line:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"LINE#{line_id:05d}"},
                },
            )
            return itemToLine(response["Item"])
        except KeyError:
            raise ValueError(f"Line with ID {line_id} not found")

    def listLines(self) -> list[Line]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "LINE"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToLine(item) for item in response["Items"]]

    def listLinesFromImage(self, image_id: int) -> list[Line]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "PK": {
                    "AttributeValueList": [{"S": f"IMAGE#{image_id:05d}"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToLine(item) for item in response["Items"]]
