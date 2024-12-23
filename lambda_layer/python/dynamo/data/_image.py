from dynamo import Image, Line, itemToImage, itemToLine
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

    def getImageDetails(self, image_id: int) ->  tuple[Image, list[Line]]:
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
            for item in response["Items"]:
                if item["SK"]["S"] == "IMAGE":
                    image = itemToImage(item)
                elif item["SK"]["S"].startswith("LINE"):
                    lines.append(itemToLine(item))
            
            return image, lines
        except Exception as e:
            raise Exception(f"Error getting image details: {e}")
            # raise ValueError(f"Image with ID {image_id} not found")
        

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
