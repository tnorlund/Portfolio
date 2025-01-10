from dynamo import ScaledImage, ItemToScaledImage
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _ScaledImage:
    """
    A class used to represent a scaled image.
    """

    def addScaledImage(self, scaled_image: ScaledImage):
        """Adds a scaled image to the database

        Args:
            scaled_image (ScaledImage): The scaled image to add to the database

        Raises:
            ValueError: When a scaled image with the same ID already exists
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=scaled_image.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(
                f"Scaled image with ID {scaled_image.image_id} already exists"
            )
    
    def getScaledImage(self, image_id: int, quality: int) -> ScaledImage:
        try:
            formatted_pk = f"IMAGE#{image_id:05d}"
            formatted_sk = f"IMAGE_SCALE#{quality:05d}"
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": formatted_pk}, "SK": {"S": formatted_sk}},
            )
            return ItemToScaledImage(response["Item"])
        except KeyError:
            raise ValueError(f"Scaled image with ID {image_id} not found")
        
    def updateScaledImage(self, scaled_image: ScaledImage):
        """Updates a scaled image in the database."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=scaled_image.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Scaled Image with ID {scaled_image.id} not found")
            else:
                raise Exception(f"Error updating scaled image: {e}")

    def deleteScaledImage(self, image_id: int, quality: int):
        try:
            formatted_pk = f"IMAGE#{image_id:05d}"
            formatted_sk = f"IMAGE_SCALE#{quality:05d}"
            self._client.delete_item(
                TableName=self.table_name,
                Key={"PK": {"S": formatted_pk}, "SK": {"S": formatted_sk}},
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Could not delete scaled image with ID {image_id}")
    
    def deleteScaledImages(self, scaled_images: list[ScaledImage]):
        """Deletes a list of scaled images from the database"""
        try:
            for i in range(0, len(scaled_images), CHUNK_SIZE):
                chunk = scaled_images[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": scaled_image.key()}}
                    for scaled_image in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
        except ClientError as e:
            raise ValueError("Could not delete scaled images from the database")

    def deleteScaledImageFromImage(self, image_id: int):
        """Deletes all scaled images associated with an image"""
        scaled_images = self.listScaledImagesFromImage(image_id)
        self.deleteScaledImages(scaled_images)

    def getScaledImage(self, image_id: int, quality: int) -> ScaledImage:
        try:
            formatted_pk = f"IMAGE#{image_id:05d}"
            formatted_sk = f"IMAGE_SCALE#{quality:05d}"
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": formatted_pk}, "SK": {"S": formatted_sk}},
            )
            return ItemToScaledImage(response["Item"])
        except KeyError:
            raise ValueError(f"Scaled image with ID {image_id} not found")

    def listScaledImagesFromImage(self, image_id: int) -> list[ScaledImage]:
        formatted_pk = f"IMAGE#{image_id:05d}"
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": formatted_pk},
                    ":sk": {"S": "IMAGE_SCALE#"},  # Match all scaled images
                },
                ScanIndexForward=False,
            )
            return [ItemToScaledImage(item) for item in response["Items"]]
        except ClientError as e:
            raise ValueError(
                f"Could not list scaled images for image with ID {image_id}"
            )

    def listScaledImages(self) -> list[ScaledImage]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "IMAGE_SCALE"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [ItemToScaledImage(item) for item in response["Items"]]
