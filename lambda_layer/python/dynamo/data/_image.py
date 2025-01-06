from dynamo import (
    Image,
    Line,
    Letter,
    Word,
    ScaledImage,
    itemToImage,
    itemToLine,
    itemToWord,
    itemToLetter,
    ItemToScaledImage,
)
from botocore.exceptions import ClientError


class _Image:
    """
    A class used to represent an Image in the database.

    Methods
    -------
    addImage(image: Image)
        Adds an image to the database.

    """

    def addImage(self, image: Image):
        """Adds an image to the database

        Args:
            image (Image): The image to add to the database

        Raises:
            ValueError: When an image with the same ID already
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=image.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Image with ID {image.id} already exists")

    def getImage(self, image_id: int) -> Image:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id:05d}"}, "SK": {"S": "IMAGE"}},
            )
            return itemToImage(response["Item"])
        except KeyError:
            raise ValueError(f"Image with ID {image_id} not found")

    def getImageDetails(
        self, image_id: int
    ) -> tuple[Image, list[Line], list[Word], list[Letter]]:
        """Gets the details of an image from the database. This includes all lines associated with the image."""
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_value",
                ExpressionAttributeNames={
                    "#pk": "PK"  # Replace 'PK' with your actual Partition Key attribute name
                },
                ExpressionAttributeValues={
                    ":pk_value": {"S": f"IMAGE#{image_id:05d}"},
                },
            )
            items = response["Items"]
            # Keep querying until there is no LastEvaluatedKey
            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_value",
                    ExpressionAttributeNames={"#pk": "PK"},
                    ExpressionAttributeValues={
                        ":pk_value": {"S": f"IMAGE#{image_id:05d}"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items += response["Items"]  # Accumulate items

            # Parse all items in the response as either image or line
            image = None
            lines = []
            words = []
            letters = []
            scaled_images = []
            for item in items:
                if item["SK"]["S"] == "IMAGE":
                    image = itemToImage(item)
                elif (
                    item["SK"]["S"].startswith("LINE") and "WORD" not in item["SK"]["S"]
                ):
                    lines.append(itemToLine(item))
                elif (
                    item["SK"]["S"].startswith("LINE")
                    and "WORD" in item["SK"]["S"]
                    and "LETTER" not in item["SK"]["S"]
                ):
                    words.append(itemToWord(item))
                elif (
                    item["SK"]["S"].startswith("LINE")
                    and "WORD" in item["SK"]["S"]
                    and "LETTER" in item["SK"]["S"]
                ):
                    letters.append(itemToLetter(item))
                elif item["SK"]["S"].startswith("IMAGE_SCALE"):
                    scaled_images.append(ItemToScaledImage(item))

            return image, lines, words, letters, scaled_images
        except Exception as e:
            raise Exception(f"Error getting image details: {e}")

    def deleteImage(self, image_id: int):
        """Deletes an image from the database

        Args:
            image_id (int): The ID of the image to delete
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id:05d}"}, "SK": {"S": "IMAGE"}},
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Image with ID {image_id} not found")
            else:
                raise Exception(f"Error deleting image: {e}")

    def listImages(self) -> list[Image]:
        try:
            all_items = []

            # Initial scan
            response = self._client.scan(
                TableName=self.table_name,
                ScanFilter={
                    "SK": {
                        "AttributeValueList": [{"S": "IMAGE"}],
                        "ComparisonOperator": "EQ",
                    }
                },
            )
            all_items.extend(response["Items"])

            # Keep scanning while there's more data
            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                response = self._client.scan(
                    TableName=self.table_name,
                    ScanFilter={
                        "SK": {
                            "AttributeValueList": [{"S": "IMAGE"}],
                            "ComparisonOperator": "EQ",
                        }
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                all_items.extend(response["Items"])

            # Convert items to Image objects
            return [itemToImage(item) for item in all_items]

        except Exception as e:
            raise Exception(f"Error listing images: {e}")
