from typing import Optional, List, Tuple, Dict, Union
from dynamo import (
    Image,
    Line,
    Letter,
    Word,
    Receipt,
    itemToReceipt,
    itemToReceiptLine,
    itemToReceiptWord,
    itemToReceiptLetter,
    itemToImage,
    itemToLine,
    itemToWord,
    itemToLetter,
)
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


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

    def addImages(self, images: List[Image]):
        """Adds a list of images to the database

        Args:
            images (list[Image]): The images to add to the database

        Raises:
            ValueError: When an image with the same ID already exists
        """
        try:
            for i in range(0, len(images), CHUNK_SIZE):
                chunk = images[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": image.to_item()}} for image in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error adding images: {e}")

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

    def getImageDetails(self, image_id: int) -> tuple[
        Image,
        list[Line],
        list[Word],
        list[Letter],
        list[Dict[str, Union[Receipt, list[Line], list[Word], list[Letter]]]],
    ]:
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
            receipts = []

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
                elif item["TYPE"]["S"] == "RECEIPT":
                    receipts.append(
                        {
                            "receipt": itemToReceipt(item),
                            "lines": [],
                            "words": [],
                            "letters": [],
                        }
                    )
                elif item["TYPE"]["S"] == "RECEIPT_LINE":
                    this_line = itemToReceiptLine(dict(item))
                    receipts[this_line.receipt_id - 1]["lines"].append(this_line)
                elif item["TYPE"]["S"] == "RECEIPT_WORD":
                    this_word = itemToReceiptWord(item)
                    receipts[this_word.receipt_id - 1]["words"].append(this_word)
                elif item["TYPE"]["S"] == "RECEIPT_LETTER":
                    this_letter = itemToReceiptLetter(item)
                    receipts[this_letter.receipt_id - 1]["letters"].append(this_letter)

            return image, lines, words, letters, receipts

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

    def deleteImages(self, images: list[Image]):
        """ "Deletes a list of images"""
        try:
            for i in range(0, len(images), CHUNK_SIZE):
                chunk = images[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": image.key()}} for image in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete images from the database") from e

    def listImageDetails(
        self, limit: Optional[int] = None, last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[
        Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]], Optional[Dict]
    ]:
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
            tuple: (payload, last_evaluated_key)
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
            payload = {}
            for item in all_items:
                if item["SK"]["S"] == "IMAGE":
                    image = itemToImage(item)
                    payload[image.id] = {"image": image}
                elif item["SK"]["S"].startswith("RECEIPT"):
                    receipt = itemToReceipt(item)
                    if receipt.image_id in payload:
                        if "receipts" in payload[receipt.image_id]:
                            payload[receipt.image_id]["receipts"].append(receipt)
                        else:
                            payload[receipt.image_id]["receipts"] = [receipt]
                elif item["SK"]["S"].startswith("LINE"):
                    line = itemToLine(item)
                    if line.image_id in payload:
                        if "lines" in payload[line.image_id]:
                            payload[line.image_id]["lines"].append(line)
                        else:
                            payload[line.image_id]["lines"] = [line]

            return payload, None

        # Otherwise, do a 'single-page' query for pagination
        try:

            payload = {}
            images_found = 0
            lek_to_return = None

            next_key = last_evaluated_key
            # Keep fetching chunks from Dynamo until we have `limit` images or run out
            while True:
                query_params = {
                    "TableName": self.table_name,
                    "IndexName": "GSI1",
                    "KeyConditionExpression": "#pk = :pk_val",
                    "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                    "ExpressionAttributeValues": {":pk_val": {"S": "IMAGE"}},
                    "Limit": 100,  # fetch up to 100 items at a time
                }
                if next_key:
                    query_params["ExclusiveStartKey"] = next_key

                response = self._client.query(**query_params)
                items = response.get("Items", [])
                next_key = response.get("LastEvaluatedKey")  # for the next chunk

                # Walk through these items in order
                for i, item in enumerate(items):
                    sk = item["SK"]["S"]
                    item_type = item["TYPE"]["S"]

                    # If this item is a new image
                    if sk == "IMAGE":
                        image = itemToImage(item)
                        # Are we about to exceed the user's limit of distinct images?
                        if images_found == limit:
                            # We just encountered the (limit+1)-th image; stop here.
                            # We'll return this item as part of the next page.
                            # So we need to build a LEK that points to THIS item.
                            image.id = int(image.id) - 1
                            lek_to_return = {**image.key(), **image.gsi1_key()}
                            return payload, lek_to_return

                        # Otherwise, this is the next image we want to include.
                        payload[image.id] = {"image": image}
                        images_found += 1

                    # If it's a line or receipt for an image we've already included
                    elif item_type == "LINE":
                        line = itemToLine(item)
                        if line.image_id in payload:  # belongs to a currently included image
                            payload[line.image_id].setdefault("lines", []).append(line)

                    elif item_type == "RECEIPT":
                        receipt = itemToReceipt(item)
                        if receipt.image_id in payload:
                            payload[receipt.image_id].setdefault("receipts", []).append(receipt)

                    # else: ignore other items or unknown SK patterns

                # If we processed the entire chunk and we STILL have fewer than `limit` images,
                # but we do have a next_key, we should fetch the next chunk.
                # If no next_key => no more data in the table.
                if images_found < limit and next_key:
                    continue  # go get the next chunk
                else:
                    # We either reached the limit, or we ran out of data
                    # If we haven't reached the limit but there's no next_key => done
                    lek_to_return = next_key  # might be None or an actual key
                    break

            return payload, lek_to_return
        except Exception as e:
            raise Exception(f"Error listing images: {e}")

    def listImages(self) -> List[Image]:
        """Lists all images in the database."""
        images = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "IMAGE"}},
            )
            images.extend([itemToImage(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "IMAGE"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                images.extend([itemToImage(item) for item in response["Items"]])

            return images
        except Exception as e:
            raise Exception(f"Error listing images: {e}")
