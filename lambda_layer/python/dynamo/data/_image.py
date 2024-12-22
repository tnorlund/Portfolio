from dynamo.entities import Image
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
                ConditionExpression="attribute_not_exists(PK)"
            )
        except ClientError as e:
            raise ValueError(f"Image with ID {image.id} already exists")
