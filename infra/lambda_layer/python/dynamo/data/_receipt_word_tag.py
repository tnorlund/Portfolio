# _receipt_word_tag.py

from dynamo import ReceiptWordTag, itemToReceiptWordTag
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptWordTag:
    """
    Provides methods for accessing ReceiptWordTag items in DynamoDB.

    Table schema for ReceiptWordTag (simplified):
      PK = "IMAGE#<image_id>"
      SK = "TAG#<tag_upper_padded>#RECEIPT#<receipt_id>#WORD#<word_id>"
      TYPE = "RECEIPT_WORD_TAG"
      GSI1PK = "TAG#<tag_upper_padded>"
      GSI1SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>"
    """

    def __init__(self, client, table_name: str):
        """
        Args:
            client: A boto3 DynamoDB client (e.g. boto3.client("dynamodb")).
            table_name (str): The name of your DynamoDB table.
        """
        self._client = client
        self.table_name = table_name

    def addReceiptWordTag(self, receipt_word_tag: ReceiptWordTag):
        """
        Adds a ReceiptWordTag to the database with a conditional check
        that it does not already exist.

        Args:
            receipt_word_tag (ReceiptWordTag): The object to add.

        Raises:
            ValueError: If a ReceiptWordTag with the same PK/SK already exists.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_word_tag.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptWordTag already exists for "
                    f"image_id={receipt_word_tag.image_id}, "
                    f"receipt_id={receipt_word_tag.receipt_id}, "
                    f"word_id={receipt_word_tag.word_id}, "
                    f"tag={receipt_word_tag.tag}"
                ) from e
            else:
                raise Exception(f"Error adding ReceiptWordTag: {e}")

    def addReceiptWordTags(self, receipt_word_tags: list[ReceiptWordTag]):
        """
        Adds multiple ReceiptWordTag items in batches (up to 25 at a time).

        Args:
            receipt_word_tags (list[ReceiptWordTag]): The objects to add.

        Note:
            This method does NOT use a conditional expression,
            so it may overwrite existing items if duplicates exist.
        """
        try:
            for i in range(0, len(receipt_word_tags), CHUNK_SIZE):
                chunk = receipt_word_tags[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": rwt.to_item()}} for rwt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(
                "Could not add ReceiptWordTags to the database"
            ) from e

    def updateReceiptWordTag(self, receipt_word_tag: ReceiptWordTag):
        """
        Updates an existing ReceiptWordTag in DynamoDB.
        (Currently does NOT check if the item exists.)

        Args:
            receipt_word_tag (ReceiptWordTag): The object to update.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=receipt_word_tag.to_item(),
            )
        except ClientError as e:
            raise Exception(f"Error updating ReceiptWordTag: {e}")

    def deleteReceiptWordTag(
        self,
        image_id: int,
        receipt_id: int,
        line_id: int,
        word_id: int,
        tag: str
    ):
        """
        Deletes a single ReceiptWordTag from DynamoDB with a conditional check
        that it exists (attribute_exists).

        Args:
            image_id (int): The image ID.
            receipt_id (int): The receipt ID.
            line_id (int): The line ID.
            word_id (int): The word ID.
            tag (str): The tag.

        Raises:
            ValueError: If the item does not exist.
        """
        rwt = ReceiptWordTag(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            tag=tag,
        )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=rwt.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptWordTag not found for image_id={image_id}, "
                    f"receipt_id={receipt_id}, line_id={line_id}, "
                    f"word_id={word_id}, tag={tag}"
                ) from e
            else:
                raise Exception(f"Error deleting ReceiptWordTag: {e}")

    def deleteReceiptWordTags(self, receipt_word_tags: list[ReceiptWordTag]):
        """
        Deletes multiple ReceiptWordTag items in batches (up to 25).

        Args:
            receipt_word_tags (list[ReceiptWordTag]): The objects to delete.
        """
        try:
            for i in range(0, len(receipt_word_tags), CHUNK_SIZE):
                chunk = receipt_word_tags[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": rwt.key()}} for rwt in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(
                "Could not delete ReceiptWordTags from the database"
            ) from e

    def deleteReceiptWordTagsFromImage(self, image_id: int):
        """
        Deletes all ReceiptWordTag items for a given image by first listing them
        and then calling deleteReceiptWordTags.

        Args:
            image_id (int): The image ID.
        """
        tags = self.listReceiptWordTagsFromImage(image_id)
        self.deleteReceiptWordTags(tags)

    def getReceiptWordTag(
        self,
        image_id: int,
        receipt_id: int,
        line_id: int,
        word_id: int,
        tag: str
    ) -> ReceiptWordTag:
        """
        Retrieves a single ReceiptWordTag from DynamoDB by its key.

        Args:
            image_id (int)
            receipt_id (int)
            line_id (int)
            word_id (int)
            tag (str)

        Returns:
            ReceiptWordTag

        Raises:
            ValueError: If the item does not exist.
        """
        rwt = ReceiptWordTag(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            tag=tag,
        )
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=rwt.key(),
            )
            return itemToReceiptWordTag(response["Item"])
        except KeyError:
            # No "Item" or missing fields
            raise ValueError(
                f"ReceiptWordTag not found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}, "
                f"word_id={word_id}, tag={tag}"
            )
        except ClientError as e:
            raise Exception(f"Error getting ReceiptWordTag: {e}")
        
    def getReceiptWordTags(self, tag: str) -> list[ReceiptWordTag]:
        """
        Retrieves all ReceiptWordTag items with a given tag from the database,
        using GSI1 (where GSI1PK = "TAG#<tag_underscore_padded>").
        """
        # Match how ReceiptWordTag.gsi1_key() pads the tag:
        # (If your code also uses .upper(), do that here.)
        spaced_tag = f"{tag:_>20}"

        receipt_tags = []
        try:
            # 1) Query the GSI
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",  # Ensure this matches your actual GSI name
                KeyConditionExpression="GSI1PK = :gsi1pk",
                ExpressionAttributeValues={
                    ":gsi1pk": {"S": f"TAG#{spaced_tag}"}
                },
            )
            # 2) Convert each DynamoDB item to a ReceiptWordTag
            receipt_tags.extend([itemToReceiptWordTag(item) for item in response["Items"]])

            # 3) Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI1",
                    KeyConditionExpression="GSI1PK = :gsi1pk",
                    ExpressionAttributeValues={
                        ":gsi1pk": {"S": f"TAG#{spaced_tag}"}
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_tags.extend([itemToReceiptWordTag(item) for item in response["Items"]])

            return receipt_tags

        except ClientError as e:
            raise ValueError("Could not list ReceiptWordTags from the database") from e

    def listReceiptWordTags(self) -> list[ReceiptWordTag]:
        """
        Lists all ReceiptWordTag items in the table by querying an index (e.g. GSITYPE)
        where TYPE = 'RECEIPT_WORD_TAG'.

        Returns:
            list[ReceiptWordTag]
        """
        receipt_word_tags = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",  # or name of your secondary index
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "RECEIPT_WORD_TAG"}},
            )
            for item in response.get("Items", []):
                receipt_word_tags.append(itemToReceiptWordTag(item))

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "RECEIPT_WORD_TAG"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                for item in response.get("Items", []):
                    receipt_word_tags.append(itemToReceiptWordTag(item))

            return receipt_word_tags

        except ClientError as e:
            raise ValueError(
                "Could not list ReceiptWordTags from the database"
            ) from e

    def listReceiptWordTagsFromImage(self, image_id: int) -> list[ReceiptWordTag]:
        """
        Lists all ReceiptWordTag items for a given image by querying:
            PK = "IMAGE#<image_id>"
            AND begins_with(SK, "TAG#")

        Args:
            image_id (int): The ID of the image.

        Returns:
            list[ReceiptWordTag]
        """
        receipt_word_tags = []
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
            for item in response.get("Items", []):
                receipt_word_tags.append(itemToReceiptWordTag(item))

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
                for item in response.get("Items", []):
                    receipt_word_tags.append(itemToReceiptWordTag(item))

            return receipt_word_tags

        except ClientError as e:
            raise ValueError(
                "Could not list ReceiptWordTags from the database"
            ) from e