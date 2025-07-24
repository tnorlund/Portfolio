from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.entities import item_to_line
from receipt_dynamo.entities.line import Line

if TYPE_CHECKING:
    from receipt_dynamo.data._base import QueryInputTypeDef

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Line(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to represent a Line in the database.

    Methods
    -------
    add_line(line: Line)
        Adds a line to the database.
    add_lines(lines: list[Line])
        Adds multiple lines to the database.
    update_line(line: Line)
        Updates a line in the database.
    update_lines(lines: list[Line])
        Updates multiple lines in the database.
    delete_line(image_id: str, line_id: int)
        Deletes a line from the database.
    delete_lines(lines: list[Line])
        Deletes multiple lines from the database.
    get_line(image_id: str, line_id: int) -> Line
        Gets a line from the database.
    list_lines(limit: Optional[int] = None, last_evaluated_key: Optional[Dict]
        = None) -> Tuple[list[Line], Optional[Dict]]
        Lists all lines from the database.
    list_lines_from_image(image_id: str) -> list[Line]
        Lists all lines from a specific image.
    """

    @handle_dynamodb_errors("add_line")
    def add_line(self, line: Line):
        """Adds a line to the database

        Args:
            line (Line): The line to add to the database

        Raises:
            ValueError: When a line with the same ID already exists
        """
        self._validate_entity(line, Line, "line")
        self._add_entity(line)

    @handle_dynamodb_errors("add_lines")
    def add_lines(self, lines: List[Line]):
        """Adds a list of lines to the database

        Args:
            lines (list[Line]): The lines to add to the database

        Raises:
            ValueError: When validation fails or lines cannot be added
        """
        self._validate_entity_list(lines, Line, "lines")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=line.to_item())
            )
            for line in lines
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_line")
    def update_line(self, line: Line):
        """Updates a line in the database

        Args:
            line (Line): The line to update in the database

        Raises:
            ValueError: When a line with the same ID does not exist
        """
        self._validate_entity(line, Line, "line")
        self._update_entity(line)

    @handle_dynamodb_errors("update_lines")
    def update_lines(self, lines: List[Line]):
        """Updates a list of lines in the database

        Args:
            lines (list[Line]): The lines to update in the database

        Raises:
            ValueError: When validation fails or lines don't exist
        """
        self._validate_entity_list(lines, Line, "lines")

        transact_items = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": line.to_item(),
                    "ConditionExpression": "attribute_exists(PK)",
                }
            }
            for line in lines
        ]

        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_line")
    def delete_line(self, image_id: str, line_id: int):
        """Deletes a line from the database

        Args:
            image_id (str): The UUID of the image the line belongs to
            line_id (int): The ID of the line to delete
        """
        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"LINE#{line_id:05d}"},
            },
            ConditionExpression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("delete_lines")
    def delete_lines(self, lines: List[Line]):
        """Deletes a list of lines from the database

        Args:
            lines (list[Line]): The lines to delete
        """
        self._validate_entity_list(lines, Line, "lines")

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=line.key)
            )
            for line in lines
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_lines_from_image")
    def delete_lines_from_image(self, image_id: str):
        """Deletes all lines from an image

        Args:
            image_id (str): The ID of the image to delete lines from
        """
        lines = self.list_lines_from_image(image_id)
        if lines:
            self.delete_lines(lines)

    @handle_dynamodb_errors("get_line")
    def get_line(self, image_id: str, line_id: int) -> Line:
        """Gets a line from the database

        Args:
            image_id (str): The UUID of the image the line belongs to
            line_id (int): The ID of the line to get

        Returns:
            Line: The line object

        Raises:
            ValueError: When the line is not found
        """
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"LINE#{line_id:05d}"},
            },
        )
        if "Item" not in response:
            raise ValueError(f"Line with ID {line_id} not found")
        return item_to_line(response["Item"])

    @handle_dynamodb_errors("list_lines")
    def list_lines(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Line], Optional[Dict]]:
        """Lists all lines in the database

        Args:
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination

        Returns:
            Tuple of lines list and last evaluated key for pagination
        """
        lines = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "LINE"}},
            "ScanIndexForward": True,  # Sorts the results in ascending order
            # by PK
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        lines.extend([item_to_line(item) for item in response["Items"]])

        if limit is None:
            # If no limit is provided, paginate until all items are retrieved
            while (
                "LastEvaluatedKey" in response and response["LastEvaluatedKey"]
            ):
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                lines.extend(
                    [item_to_line(item) for item in response["Items"]]
                )
            last_evaluated_key = None
        else:
            # If a limit is provided, capture the LastEvaluatedKey (if any)
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return lines, last_evaluated_key

    @handle_dynamodb_errors("list_lines_from_image")
    def list_lines_from_image(self, image_id: str) -> List[Line]:
        """Lists all lines from an image

        Args:
            image_id: The UUID of the image

        Returns:
            List of Line objects from the specified image
        """
        lines = []
        response = self._client.query(
            TableName=self.table_name,
            IndexName="GSI1",
            KeyConditionExpression=(
                "#pk = :pk_val AND begins_with(#sk, :sk_val)"
            ),
            ExpressionAttributeNames={"#pk": "GSI1PK", "#sk": "GSI1SK"},
            ExpressionAttributeValues={
                ":pk_val": {"S": f"IMAGE#{image_id}"},
                ":sk_val": {"S": "LINE#"},
            },
        )
        lines.extend([item_to_line(item) for item in response["Items"]])

        while "LastEvaluatedKey" in response:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression=(
                    "#pk = :pk_val AND begins_with(#sk, :sk_val)"
                ),
                ExpressionAttributeNames={"#pk": "GSI1PK", "#sk": "GSI1SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {"S": "LINE#"},
                },
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            lines.extend([item_to_line(item) for item in response["Items"]])

        return lines
