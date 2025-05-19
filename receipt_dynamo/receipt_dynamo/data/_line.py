from typing import Dict, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo import Line, itemToLine

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
        except ClientError:
            raise ValueError(f"Line with ID {line.line_id} already exists")

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
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError:
            raise ValueError("Could not add lines to the database")

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
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(f"Line with ID {line.line_id} not found")
            else:
                raise Exception(f"Error updating line: {e}")

    def deleteLine(self, image_id: str, line_id: int):
        """Deletes a line from the database

        Args:
            image_id (str): The UUID of the image the line belongs to
            line_id (int): The ID of the line to delete
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"LINE#{line_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError:
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
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError:
            raise ValueError("Could not delete lines from the database")

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
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"LINE#{line_id:05d}"},
                },
            )
            return itemToLine(response["Item"])
        except KeyError:
            raise ValueError(f"Line with ID {line_id} not found")

    def listLines(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[list[Line], Optional[Dict]]:
        """Lists all lines in the database"""
        lines = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "LINE"}},
                "ScanIndexForward": True,  # Sorts the results in ascending order by PK
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            lines.extend([itemToLine(item) for item in response["Items"]])

            if limit is None:
                # If no limit is provided, paginate until all items are
                # retrieved
                while (
                    "LastEvaluatedKey" in response
                    and response["LastEvaluatedKey"]
                ):
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    lines.extend(
                        [itemToLine(item) for item in response["Items"]]
                    )
                last_evaluated_key = None
            else:
                # If a limit is provided, capture the LastEvaluatedKey (if any)
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return lines, last_evaluated_key

        except ClientError as e:
            raise ValueError("Could not list lines from the database") from e

    def listLinesFromImage(self, image_id: int) -> list[Line]:
        """Lists all lines from an image"""
        lines = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                ExpressionAttributeNames={"#pk": "GSI1PK", "#sk": "GSI1SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": "IMAGE"},
                    ":sk_val": {"S": f"IMAGE#{image_id}#LINE#"},
                },
            )
            lines.extend([itemToLine(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI1",
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    ExpressionAttributeNames={
                        "#pk": "GSI1PK",
                        "#sk": "GSI1SK",
                    },
                    ExpressionAttributeValues={
                        ":pk_val": {"S": "IMAGE"},
                        ":sk_val": {"S": f"IMAGE#{image_id}#LINE#"},
                    },
                    ExclusiveStartKey=response.get("LastEvaluatedKey", None),
                )
                lines.extend([itemToLine(item) for item in response["Items"]])
            return lines
        except ClientError as e:
            raise ValueError("Could not list lines from the database") from e
