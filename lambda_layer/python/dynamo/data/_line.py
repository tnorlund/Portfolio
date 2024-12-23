from dynamo import Line, itemToLine
from botocore.exceptions import ClientError


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

    def getLine(self, image_id: int, line_id: int) -> Line:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id:05d}"}, "SK": {"S": f"LINE#{line_id:05d}"}},
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
