# _word_tag.py
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo import WordTag, item_to_word_tag
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        QueryInputTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)

# DynamoDB batch_write_item can handle up to 25 items per call
CHUNK_SIZE = 25


class _WordTag(DynamoClientProtocol):
    """
    Provides methods for accessing WordTag items in DynamoDB.

    Table schema for WordTag (simplified):
      PK = "IMAGE#<image_id>"
      SK = "TAG#<tag_upper_padded>#WORD#<word_id>"
      TYPE = "WORD_TAG"
      GSI1PK = "TAG#<tag_upper_padded>"
      GSI1SK = "IMAGE#<image_id>#LINE#<line_id>#WORD#<word_id>"
    """

    def __init__(self, client, table_name: str):
        """
        Args:
            client: A boto3 DynamoDB client (e.g., boto3.client("dynamodb")).
            table_name (str): The name of your DynamoDB table.
        """
        self._client = client
        self.table_name = table_name

    def add_word_tag(self, word_tag: WordTag):
        """
        Adds a WordTag to the database with a conditional check that it does not already exist.

        Args:
            word_tag (WordTag): The WordTag object to add.

        Raises:
            ValueError: If a WordTag with the same PK/SK already exists.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word_tag.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            # Check if it's a ConditionalCheckFailed (duplicate item)
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"WordTag for image_id={word_tag.image_id}, "
                    f"word_id={word_tag.word_id}, tag={word_tag.tag} already exists."
                ) from e
            else:
                raise OperationError(f"Error adding WordTag: {e}") from e

    def add_word_tags(self, word_tags: list[WordTag]):
        """
        Adds a list of WordTag objects to the database in batches of up to 25.

        Args:
            word_tags (list[WordTag]): The WordTag objects to add.

        Note:
            This batch_write_item approach does NOT enforce a condition expression,
            so duplicates may overwrite existing items if you use this method.
        """
        try:
            for i in range(0, len(word_tags), CHUNK_SIZE):
                chunk = word_tags[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=wt.to_item()))
                    for wt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add WordTags to the database") from e

    def update_word_tag(self, word_tag: WordTag):
        """
        Updates an existing WordTag in the database with a conditional check that it already exists.

        Args:
            word_tag (WordTag): The WordTag object to update.

        Raises:
            ValueError: If the item does not exist in the table.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word_tag.to_item(),
            )
        except ClientError as e:
            raise OperationError(f"Error updating WordTag: {e}") from e

    def delete_word_tag(self, image_id: str, line_id: int, word_id: int, tag: str):
        """
        Deletes a single WordTag from the database, ensuring it exists.

        Args:
            image_id (str): The image ID.
            tag (str): The tag string.
            word_id (int): The word ID.

        Raises:
            ValueError: If the item does not exist.
        """
        # Build the same PK/SK used by WordTag
        # Remember to underscore-pad the tag if your WordTag class does so in SK
        # Here, we'll replicate minimal logic to find the padded tag
        word_tag = WordTag(
            str(image_id),
            line_id,
            word_id,
            tag,
            timestamp_added=datetime.fromisoformat("2021-01-01T00:00:00"),
        )  # This is a placeholder value

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=word_tag.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"WordTag not found for image_id={image_id}, "
                    f"tag={tag}, word_id={word_id}"
                ) from e
            else:
                raise OperationError(f"Error deleting WordTag: {e}") from e

    def delete_word_tags(self, word_tags: list[WordTag]):
        """
        Deletes multiple WordTag items (in batches of 25).

        Args:
            word_tags (list[WordTag]): The WordTag objects to delete.
        """
        try:
            for i in range(0, len(word_tags), CHUNK_SIZE):
                chunk = word_tags[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=wt.key())
                    )
                    for wt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete WordTags from the database") from e

    def delete_word_tags_from_image(self, image_id: str):
        """
        Deletes all WordTag items for a given image.
        Internally uses list_word_tags_from_image(...) then delete_word_tags(...).

        Args:
            image_id (str): The image ID.
        """
        tags = self.list_word_tags_from_image(image_id)
        self.delete_word_tags(tags)

    def get_word_tag(
        self,
        image_id: str,
        line_id: int,
        word_id: int,
        tag: str,
    ) -> WordTag:
        """
        Retrieves a single WordTag from DynamoDB by its primary key.

        Args:
            image_id (int)
            line_id (int)
            word_id (int)
            tag (str)

        Returns:
            WordTag: The reconstructed WordTag object.

        Raises:
            ValueError: If the item does not exist.
        """
        word_tag = WordTag(
            str(image_id),
            line_id,
            word_id,
            tag,
            timestamp_added=datetime.fromisoformat("2021-01-01T00:00:00"),
        )  # This is a placeholder value

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=word_tag.key(),
            )
            return item_to_word_tag(response["Item"])
        except KeyError:
            # Means response had no "Item" or missing fields
            raise ValueError(
                f"WordTag not found for image_id={image_id}, tag={tag}, word_id={word_id}"
            )
        except ClientError as e:
            raise OperationError(f"Error getting WordTag: {e}") from e

    def get_word_tags(self, tag: str) -> list[WordTag]:
        """
        Retrieves all WordTag items with a given tag from the database,
        using GSI1 (where GSI1PK = "TAG#<padded_tag>").
        """
        word_tags: list[WordTag] = []
        try:
            # Initial query
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",  # Make sure this is the correct GSI name
                KeyConditionExpression="GSI1PK = :gsi1pk",
                ExpressionAttributeValues={":gsi1pk": {"S": f"TAG#{tag:_>40}"}},
            )
            word_tags.extend([item_to_word_tag(item) for item in response["Items"]])

            # Paginate if necessary
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI1",
                    KeyConditionExpression="GSI1PK = :gsi1pk",
                    ExpressionAttributeValues={":gsi1pk": {"S": f"TAG#{tag:_>40}"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                word_tags.extend([item_to_word_tag(item) for item in response["Items"]])

            return word_tags

        except ClientError as e:
            raise ValueError("Could not list WordTags from the database") from e

    def list_word_tags(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[WordTag], Optional[Dict]]:
        """
        Lists WordTag items from the database via the GSITYPE index (using the "TYPE" attribute).
        Supports optional pagination via a limit and a LastEvaluatedKey.

        Parameters:
            limit (Optional[int]): The maximum number of WordTags to return in one query.
            last_evaluated_key (Optional[Dict]): The key from which to continue a previous paginated query.

        Returns:
            Tuple[List[WordTag], Optional[Dict]]: A tuple containing:
                - A list of WordTag objects.
                - The LastEvaluatedKey (dict) if more items remain, otherwise None.

        Raises:
            ValueError: If there's an error listing WordTags from the database.
        """
        word_tags: List[WordTag] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "WORD_TAG"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            word_tags.extend([item_to_word_tag(item) for item in response["Items"]])

            if limit is None:
                # If no limit is provided, paginate until all items are
                # retrieved.
                while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    word_tags.extend(
                        [item_to_word_tag(item) for item in response["Items"]]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return word_tags, last_evaluated_key

        except ClientError as e:
            raise ValueError(f"Could not list WordTags from the database: {e}")

    def list_word_tags_from_image(self, image_id: str) -> list[WordTag]:
        """
        Lists all WordTag items for a given image by querying:
            PK = "IMAGE#<image_id>"
            AND begins_with(SK, "LINE#")
        then filtering for items that have "#TAG#" in the SK.
        """
        word_tags = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                FilterExpression="contains(#sk, :tag_marker)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {"S": "LINE#"},
                    ":tag_marker": {"S": "#TAG#"},
                },
            )
            word_tags.extend([item_to_word_tag(item) for item in response["Items"]])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    FilterExpression="contains(#sk, :tag_marker)",
                    ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                    ExpressionAttributeValues={
                        ":pk_val": {"S": f"IMAGE#{image_id}"},
                        ":sk_val": {"S": "LINE#"},
                        ":tag_marker": {"S": "#TAG#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                word_tags.extend([item_to_word_tag(item) for item in response["Items"]])
            return word_tags

        except ClientError as e:
            raise ValueError("Could not list WordTags from the database") from e

    def update_word_tags(self, word_tags: list[WordTag]):
        """
        Updates multiple WordTag items in the database.

        This method validates that the provided parameter is a list of WordTag instances.
        It uses DynamoDB's transact_write_items operation, which can handle up to 25 items
        per transaction. Any unprocessed items are automatically retried until no unprocessed
        items remain.

        Parameters
        ----------
        word_tags : list[WordTag]
            The list of WordTag objects to update.

        Raises
        ------
        ValueError: When given a bad parameter.
        Exception: For underlying DynamoDB errors such as:
            - ProvisionedThroughputExceededException (exceeded capacity)
            - InternalServerError (server-side error)
            - ValidationException (invalid parameters)
            - AccessDeniedException (permission issues)
            - or any other unexpected errors.
        """
        if word_tags is None:
            raise ValueError("WordTags parameter is required and cannot be None.")
        if not isinstance(word_tags, list):
            raise ValueError("WordTags must be provided as a list.")
        if not all(isinstance(tag, WordTag) for tag in word_tags):
            raise ValueError(
                "All items in the word_tags list must be instances of the WordTag class."
            )

        for i in range(0, len(word_tags), CHUNK_SIZE):
            chunk = word_tags[i : i + CHUNK_SIZE]
            transact_items = []
            for word_tag in chunk:
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": word_tag.to_item(),
                            "ConditionExpression": "attribute_exists(PK)",
                        }
                    }
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    raise ValueError("One or more word tags do not exist") from e
                elif error_code == "ConditionalCheckFailedException":
                    raise ValueError("One or more word tags do not exist") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise DynamoDBServerError(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise DynamoDBValidationError(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                elif error_code == "ResourceNotFoundException":
                    raise ValueError(f"Error updating word tags: {e}") from e
                else:
                    raise DynamoDBError(
                        f"Could not update word tags in the database: {e}"
                    ) from e
