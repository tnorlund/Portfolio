from dynamo import ScaledImage, ItemToScaledImage
from botocore.exceptions import ClientError


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
