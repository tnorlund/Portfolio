from dynamo.entities import Image
from botocore.exceptions import ClientError


class _Image:
    def addImage(self, image: Image):
        """Adds an image to the database

        Args:
            image (Image): The image to add to the database
        """
        print(f"Image: {image}")
        try:
            self._table.put_item(
                Item=image.to_item(), ConditionExpression="attribute_not_exists(PK)"
            )
        except ClientError as e:
            print(f"Error adding image: {e}")
        pass
