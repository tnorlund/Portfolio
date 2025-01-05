from dynamo import (
    Image,
    Line,
    Letter,
    Word,
    itemToImage,
    itemToLine,
    itemToWord,
    itemToLetter,
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
            # Parse all items in the response as either image or line
            image = None
            lines = []
            words = []
            letters = []
            for item in response["Items"]:
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

            return image, lines, words, letters
        except Exception as e:
            raise Exception(f"Error getting image details: {e}")
            # raise ValueError(f"Image with ID {image_id} not found")

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
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "SK": {
                    "AttributeValueList": [{"S": "IMAGE"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToImage(item) for item in response["Items"]]
