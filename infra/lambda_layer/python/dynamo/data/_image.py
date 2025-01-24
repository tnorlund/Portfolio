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
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[
        Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]],
        Optional[Dict]
    ]:
        """
        Lists images using the GSI on GSI1PK='IMAGE'. When both 'limit' and
        'last_evaluated_key' are None, it returns *all* images. Otherwise, it
        returns exactly 'limit' images (with their lines/receipts) plus a
        LastEvaluatedKey (LEK) if there are more images remaining.

        Args:
            limit (Optional[int]): The maximum number of images to return.
            last_evaluated_key (Optional[Dict]): The DynamoDB key from where
                the next page should start (for pagination).

        Returns:
            A tuple:
            1) A dictionary of image_id -> { "image": Image, "lines": [...], "receipts": [...] }
            2) The LastEvaluatedKey dict if there are more images, otherwise None.
        """

        # --------
        # CASE 1: No limit + no starting key => return *all* images
        # --------
        if limit is None and last_evaluated_key is None:
            all_items = []
            response = None
            while True:
                if response is None:
                    response = self._client.query(
                        TableName=self.table_name,
                        IndexName="GSI1",
                        KeyConditionExpression="#pk = :pk_val",
                        ExpressionAttributeNames={"#pk": "GSI1PK"},
                        ExpressionAttributeValues={":pk_val": {"S": "IMAGE"}},
                        ScanIndexForward=True,
                    )
                else:
                    if not response.get("LastEvaluatedKey"):
                        break
                    response = self._client.query(
                        TableName=self.table_name,
                        IndexName="GSI1",
                        KeyConditionExpression="#pk = :pk_val",
                        ExpressionAttributeNames={"#pk": "GSI1PK"},
                        ExpressionAttributeValues={":pk_val": {"S": "IMAGE"}},
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                        ScanIndexForward=True,
                    )

                all_items.extend(response["Items"])
                if not response.get("LastEvaluatedKey"):
                    break

            # Build the final payload
            payload: Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]] = {}
            for item in all_items:
                sk = item["SK"]["S"]
                item_type = item["TYPE"]["S"]
                if sk == "IMAGE":
                    image = itemToImage(item)
                    payload[image.id] = {"image": image}
                elif sk.startswith("RECEIPT") and item_type == "RECEIPT":
                    receipt = itemToReceipt(item)
                    if receipt.image_id in payload:
                        payload[receipt.image_id].setdefault("receipts", []).append(receipt)
                elif sk.startswith("LINE") and item_type == "LINE":
                    line = itemToLine(item)
                    if line.image_id in payload:
                        payload[line.image_id].setdefault("lines", []).append(line)

            return payload, None

        # --------
        # CASE 2: Paginated approach => Return exactly 'limit' images (if available)
        # --------
        payload: Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]] = {}
        included_image_ids = set()
        images_found = 0
        lek_to_return = None

        next_key = last_evaluated_key

        while True:
            # Ask Dynamo for limit+1 so we can detect if there's an extra image left.
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                "ExpressionAttributeValues": {":pk_val": {"S": "IMAGE"}},
                "ScanIndexForward": True,
            }
            if limit is not None:
                query_params["Limit"] = limit + 1

            if next_key:
                query_params["ExclusiveStartKey"] = next_key

            leftover_image_id = None  # or a sentinel like -1
            response = self._client.query(**query_params)
            items = response.get("Items", [])
            next_key = response.get("LastEvaluatedKey")  # This might be None or another key

            # We'll track if we need to break out of the outer loop
            # (e.g. if we found the limit+1-th image in this chunk)
            break_out_early = False

            for item in items:
                item_type = item["TYPE"]["S"]

                if item_type == "IMAGE":
                    # Only increment our 'images_found' count for image items
                    if images_found < limit:
                        # We have capacity to add one more image to this page
                        image = itemToImage(item)
                        payload[image.id] = {"image": image}
                        included_image_ids.add(image.id)
                        images_found += 1
                        last_consumed_image_key = {
                            **image.key(),    # PK, SK
                            **image.gsi1_key()
                        }
                    else:
                        # We have encountered the (limit+1)-th image
                        # => we stop reading further,
                        # but the leftover key we give back is the last *consumed* item.
                        lek_to_return = last_consumed_image_key
                        break_out_early = True
                        break

                elif item_type == "LINE":
                    # Add lines only if they belong to an image we're including in this page
                    line = itemToLine(item)
                    if line.image_id in included_image_ids:
                        payload[line.image_id].setdefault("lines", []).append(line)

                elif item_type == "RECEIPT":
                    # Add receipts only if they belong to an image included in this page
                    receipt = itemToReceipt(item)
                    if receipt.image_id in included_image_ids:
                        payload[receipt.image_id].setdefault("receipts", []).append(receipt)

                # (Handle additional types like WORD, LETTER, etc. if needed.)

            if break_out_early:
                # We found the extra image in *this* chunk, so we must stop now.
                break

            # If we haven't yet reached 'limit' images in this chunk,
            # but next_key is None => no more data in the table => we are done.
            if images_found < limit:
                if next_key is None:
                    # No more data => done.
                    break
                else:
                    # More data is available => keep querying.
                    continue
            else:
                # We have exactly 'limit' images so far. We should check if there's a leftover
                # in the *next chunk* (i.e. if we do another query). If next_key is None, that means
                # there's no next chunk. So we are done. If next_key is not None, we do
                # one more pass to see if there's a leftover image.
                if next_key is None:
                    # We are done: exactly limit, and no leftover.
                    break
                # else, let's keep going to see if we encounter that leftover image
                # in the next chunk. So just continue the loop.
                continue

        # After we exit the while loop, we check:
        # If we have exactly 'limit' images but never assigned `lek_to_return` => 
        # it means we might have more data (next_key != None) but haven't seen the leftover image yet.
        # If next_key is not None, let's set that as the leftover key:
        if images_found == limit and lek_to_return is None and next_key:
            lek_to_return = next_key

        # Return the final dictionary + leftover key
        return payload, lek_to_return

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
