# _word_tag.py

from dynamo import WordTag, itemToWordTag
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can handle up to 25 items per call
CHUNK_SIZE = 25


class _WordTag:
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

    def addWordTag(self, word_tag: WordTag):
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
                raise Exception(f"Error adding WordTag: {e}")

    def addWordTags(self, word_tags: list[WordTag]):
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
                request_items = [{"PutRequest": {"Item": wt.to_item()}} for wt in chunk]
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

    def updateWordTag(self, word_tag: WordTag):
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
            raise Exception(f"Error updating WordTag: {e}")

    def deleteWordTag(self, image_id: int, line_id: int, word_id: int, tag: str):
        """
        Deletes a single WordTag from the database, ensuring it exists.

        Args:
            image_id (int): The image ID.
            tag (str): The tag string.
            word_id (int): The word ID.

        Raises:
            ValueError: If the item does not exist.
        """
        # Build the same PK/SK used by WordTag
        # Remember to underscore-pad the tag if your WordTag class does so in SK
        # Here, we'll replicate minimal logic to find the padded tag
        word_tag = WordTag(image_id, line_id, word_id, tag)

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
                raise Exception(f"Error deleting WordTag: {e}")

    def deleteWordTags(self, word_tags: list[WordTag]):
        """
        Deletes multiple WordTag items (in batches of 25).

        Args:
            word_tags (list[WordTag]): The WordTag objects to delete.
        """
        try:
            for i in range(0, len(word_tags), CHUNK_SIZE):
                chunk = word_tags[i : i + CHUNK_SIZE]
                request_items = [{"DeleteRequest": {"Key": wt.key()}} for wt in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete WordTags from the database") from e

    def deleteWordTagsFromImage(self, image_id: int):
        """
        Deletes all WordTag items for a given image.
        Internally uses listWordTagsFromImage(...) then deleteWordTags(...).

        Args:
            image_id (int): The image ID.
        """
        tags = self.listWordTagsFromImage(image_id)
        self.deleteWordTags(tags)

    def getWordTag(
        self, image_id: int, line_id: int, word_id: int, tag: str,
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
        word_tag = WordTag(image_id, line_id, word_id, tag)

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=word_tag.key(),
            )
            return itemToWordTag(response["Item"])
        except KeyError:
            # Means response had no "Item" or missing fields
            raise ValueError(
                f"WordTag not found for image_id={image_id}, tag={tag}, word_id={word_id}"
            )
        except ClientError as e:
            raise Exception(f"Error getting WordTag: {e}")

    def listWordTags(self) -> list[WordTag]:
        """
        Lists all WordTag items by querying the GSITYPE index (assuming each WordTag has TYPE='WORD_TAG').

        Returns:
            list[WordTag]: A list of all WordTag objects in the table.
        """
        word_tags = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",  # or whatever index is used for "TYPE"
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "WORD_TAG"}},
            )
            word_tags.extend([itemToWordTag(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "WORD_TAG"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                word_tags.extend([itemToWordTag(item) for item in response["Items"]])
            return word_tags

        except ClientError as e:
            raise ValueError("Could not list WordTags from the database") from e

    def listWordTagsFromImage(self, image_id: int) -> list[WordTag]:
        """
        Lists all WordTag items for a given image by querying:
            PK = "IMAGE#<image_id>"
            AND begins_with(SK, "TAG#")

        Args:
            image_id (int): The ID of the image.

        Returns:
            list[WordTag]: A list of WordTag objects for the specified image.
        """
        word_tags = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id:05d}"},
                    ":sk_val": {"S": "TAG#"},
                },
            )
            word_tags.extend([itemToWordTag(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                    ExpressionAttributeValues={
                        ":pk_val": {"S": f"IMAGE#{image_id:05d}"},
                        ":sk_val": {"S": "TAG#"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                word_tags.extend([itemToWordTag(item) for item in response["Items"]])
            return word_tags
        except ClientError as e:
            raise ValueError("Could not list WordTags from the database") from e
