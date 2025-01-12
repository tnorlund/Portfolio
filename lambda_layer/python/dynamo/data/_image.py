from typing import Optional, List, Tuple, Dict
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
        """Fetches a single Image item by its ID."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id:05d}"}, "SK": {"S": "IMAGE"}},
            )
            return itemToImage(response["Item"])
        except KeyError:
            raise ValueError(f"Image with ID {image_id} not found")
        
    def updateImage(self, image: Image):
        """Updates an image in the database."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=image.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Image with ID {image.id} not found")
            else:
                raise Exception(f"Error updating image: {e}")

    def getImageDetails(
        self, image_id: int
    ) -> tuple[Image, list[Line], list[Word], list[Letter]]:
        """
        Gets the details of an image from the database. This includes all lines,
        words, letters, and scaled images associated with the image.
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_value",
                ExpressionAttributeNames={"#pk": "PK"},
                ExpressionAttributeValues={":pk_value": {"S": f"IMAGE#{image_id:05d}"}},
            )
            items = response["Items"]

            # Keep querying while there is a LastEvaluatedKey
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
                items += response["Items"]

            # Separate items by type
            image = None
            lines = []
            words = []
            letters = []
            scaled_images = []

            for item in items:
                sk_value = item["SK"]["S"]
                if sk_value == "IMAGE":
                    image = itemToImage(item)
                elif sk_value.startswith("LINE") and "WORD" not in sk_value:
                    lines.append(itemToLine(item))
                elif (
                    sk_value.startswith("LINE")
                    and "WORD" in sk_value
                    and "LETTER" not in sk_value
                ):
                    words.append(itemToWord(item))
                elif (
                    sk_value.startswith("LINE")
                    and "WORD" in sk_value
                    and "LETTER" in sk_value
                ):
                    letters.append(itemToLetter(item))

            return image, lines, words, letters, scaled_images

        except Exception as e:
            raise Exception(f"Error getting image details: {e}")

    def deleteImage(self, image_id: int):
        """Deletes an image from the database."""
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

    def listImages(
        self, limit: Optional[int] = None, last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[List[Image], Optional[Dict]]:
        """
        Lists images using the GSI on GSI1PK='IMAGE'. When both 'limit' and
        'last_evaluated_key' are None, it will return *all* images (the current
        behavior). Otherwise, it returns a single 'page' of items plus a
        LastEvaluatedKey for further pagination.

        Args:
            limit (int, optional): Max number of images to fetch.
                                   Defaults to None (no max, return all).
            last_evaluated_key (dict, optional): Where to continue from.
                                   Defaults to None (start from the beginning).

        Returns:
            tuple: (list_of_images, last_evaluated_key)
        """
        # If no limit or key is given, return *all* images (old behavior)
        if limit is None and last_evaluated_key is None:
            all_items = []
            response = None
            while True:
                if response is None:
                    # first query
                    response = self._client.query(
                        TableName=self.table_name,
                        IndexName="GSI1",
                        KeyConditionExpression="#pk = :pk_val",
                        ExpressionAttributeNames={"#pk": "GSI1PK"},
                        ExpressionAttributeValues={":pk_val": {"S": "IMAGE"}},
                    )
                else:
                    if (
                        "LastEvaluatedKey" not in response
                        or not response["LastEvaluatedKey"]
                    ):
                        break
                    response = self._client.query(
                        TableName=self.table_name,
                        IndexName="GSI1",
                        KeyConditionExpression="#pk = :pk_val",
                        ExpressionAttributeNames={"#pk": "GSI1PK"},
                        ExpressionAttributeValues={":pk_val": {"S": "IMAGE"}},
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                    )

                all_items.extend(response["Items"])

                if (
                    "LastEvaluatedKey" not in response
                    or not response["LastEvaluatedKey"]
                ):
                    break

            return [itemToImage(item) for item in all_items], None

        # Otherwise, do a 'single-page' query for pagination
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                "ExpressionAttributeValues": {":pk_val": {"S": "IMAGE"}},
            }

            if limit is not None:
                query_params["Limit"] = limit

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            response = self._client.query(**query_params)
            items = response.get("Items", [])
            lek = response.get("LastEvaluatedKey", None)
            images = [itemToImage(item) for item in items]

            return images, lek

        except Exception as e:
            raise Exception(f"Error listing images with LastEvaluatedKey: {e}")
