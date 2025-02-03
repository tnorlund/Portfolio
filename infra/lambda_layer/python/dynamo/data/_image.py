from typing import Optional, List, Tuple, Dict, Union
from dynamo import (
    Image,
    Line,
    Letter,
    Word,
    WordTag,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
    itemToReceipt,
    itemToReceiptLine,
    itemToReceiptWord,
    itemToReceiptWordTag,
    itemToReceiptLetter,
    itemToImage,
    itemToLine,
    itemToWord,
    itemToWordTag,
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
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Image with ID {image.image_id} already exists")
            else:
                raise Exception(f"Error updating image: {e}")

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
                Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": "IMAGE"}},
            )
            return itemToImage(response["Item"])
        except KeyError:
            raise ValueError(f"Image with ID {image_id} not found")
    
    def getMaxImageId(self) -> int:
        """Fetches the maximum image ID from the database."""
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",  # or whatever index you created
                KeyConditionExpression="#pk = :pk_val",
                ExpressionAttributeNames={"#pk": "GSI1PK"},
                ExpressionAttributeValues={":pk_val": {"S": "IMAGE"}},
                ScanIndexForward=False,  # Sort in descending order
                Limit=1,                 # Grab only the top (largest) item
            )
            items = response.get("Items", [])
            if not items:
                return 0  # no images found at all

            # Extract the image_id from the PK or SK, depending on your schema
            # Example: PK = "IMAGE#00042", so we split on '#'
            pk_str = items[0]["PK"]["S"]  # e.g. "IMAGE#00042"
            image_id_str = pk_str.split("#")[1]  # "00042"
            return int(image_id_str)
        except Exception as e:
            raise Exception(f"Error getting max image ID: {e}")

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
                raise ValueError(f"Image with ID {image.image_id} not found")
            else:
                raise Exception(f"Error updating image: {e}")

    def getImageDetails(self, image_id: int) -> tuple[
        Image,
        list[Line],
        list[Word],
        list[WordTag],
        list[Letter],
        list[Dict[str, Union[Receipt, list[ReceiptLine], list[Word], list[Letter]]]],
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
                ExpressionAttributeValues={":pk_value": {"S": f"IMAGE#{image_id}"}},
                ScanIndexForward=True,
            )
            items = response["Items"]

            # Keep querying while there is a LastEvaluatedKey
            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_value",
                    ExpressionAttributeNames={"#pk": "PK"},
                    ExpressionAttributeValues={
                        ":pk_value": {"S": f"IMAGE#{image_id}"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                    ScanIndexForward=True,
                )
                items += response["Items"]

            # Separate items by type
            image = None
            lines = []
            words = []
            word_tags = []
            letters = []
            receipts = []

            for item in items:
                sk_value = item["SK"]["S"]
                if item["TYPE"]["S"] == "IMAGE":
                    image = itemToImage(item)
                elif item["TYPE"]["S"] == "LINE":
                    lines.append(itemToLine(item))
                elif item["TYPE"]["S"] == "WORD":
                    words.append(itemToWord(item))
                elif item["TYPE"]["S"] == "WORD_TAG":
                    word_tags.append(itemToWordTag(item))
                elif item["TYPE"]["S"] == "LETTER":
                    letters.append(itemToLetter(item))
                elif item["TYPE"]["S"] == "RECEIPT":
                    receipts.append(
                        {
                            "receipt": itemToReceipt(item),
                            "lines": [],
                            "words": [],
                            "word_tags": [],
                            "letters": [],
                        }
                    )
                elif item["TYPE"]["S"] == "RECEIPT_LINE":
                    this_line = itemToReceiptLine(dict(item))
                    receipts[this_line.receipt_id - 1]["lines"].append(this_line)
                elif item["TYPE"]["S"] == "RECEIPT_WORD":
                    this_word = itemToReceiptWord(item)
                    receipts[this_word.receipt_id - 1]["words"].append(this_word)
                elif item["TYPE"]["S"] == "RECEIPT_WORD_TAG":
                    this_tag = itemToReceiptWordTag(item)
                    receipts[this_tag.receipt_id - 1]["word_tags"].append(this_tag)
                elif item["TYPE"]["S"] == "RECEIPT_LETTER":
                    this_letter = itemToReceiptLetter(item)
                    receipts[this_letter.receipt_id - 1]["letters"].append(this_letter)

            return image, lines, words, word_tags, letters, receipts

        except Exception as e:
            raise Exception(f"Error getting image details: {e}")

    def deleteImage(self, image_id: int):
        """Deletes an image from the database."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": "IMAGE"}},
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Image with ID {image_id} not found")
            else:
                raise Exception(f"Error deleting image: {e}")

    def deleteImages(self, images: list[Image]):
        """Deletes a list of images"""
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

        # -------------------------------------------------------------
        # CASE 1: No limit + no starting key => Return *all* images
        # -------------------------------------------------------------
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
                    # If there's no LastEvaluatedKey, we've paged through all items
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
                    img = itemToImage(item)
                    payload[img.image_id] = {"image": img}
                elif sk.startswith("RECEIPT") and item_type == "RECEIPT":
                    receipt = itemToReceipt(item)
                    if receipt.image_id in payload:
                        payload[receipt.image_id].setdefault("receipts", []).append(receipt)
                elif sk.startswith("LINE") and item_type == "LINE":
                    line = itemToLine(item)
                    if line.image_id in payload:
                        payload[line.image_id].setdefault("lines", []).append(line)

            return payload, None

        # -------------------------------------------------------------
        # CASE 2: Paginated => Return exactly 'limit' images (if available)
        # -------------------------------------------------------------
        payload: Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]] = {}
        included_image_ids = set()
        images_found = 0
        lek_to_return = None

        next_key = last_evaluated_key

        # We'll keep looping until we've either found our 'limit' images
        # OR run out of items in Dynamo, etc.
        while True:
            # Build query params, including the ExclusiveStartKey if we have one
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk_val",
                "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                "ExpressionAttributeValues": {":pk_val": {"S": "IMAGE"}},
                "ScanIndexForward": True,
            }
            # We ask for limit+1 so we can detect a leftover image
            if limit is not None:
                query_params["Limit"] = limit + 1

            if next_key:
                query_params["ExclusiveStartKey"] = next_key

            response = self._client.query(**query_params)
            items = response.get("Items", [])
            next_key = response.get("LastEvaluatedKey")  # might be None or another dict

            # We keep track of leftover_image_id if we encounter a (limit+1)-th image
            leftover_image_id = None
            leftover_image_key = None

            # We'll also store the last image we actually included:
            last_consumed_image_key = None

            # Process all items in this batch
            for item in items:
                sk = item["SK"]["S"]
                item_type = item["TYPE"]["S"]

                # -- 1) IMAGE Items --
                if sk == "IMAGE":
                    if images_found < limit:
                        # Include this image in the current page
                        img = itemToImage(item)
                        payload[img.image_id] = {"image": img}
                        included_image_ids.add(img.image_id)
                        images_found += 1

                        # Track the key of the last consumed image
                        last_consumed_image_key = {
                            **img.key(),      # PK, SK
                            **img.gsi1_key()  # GSI1PK, GSI1SK
                        }

                    else:
                        # This is the (limit+1)-th image => leftover
                        leftover_img = itemToImage(item)
                        leftover_image_id = leftover_img.image_id
                        leftover_image_key = {
                            **leftover_img.key(),
                            **leftover_img.gsi1_key()
                        }
                        # Do NOT "consume" it. We don't break right away because
                        # we still want to process lines/receipts for previously included images.
                
                # -- 2) LINE / RECEIPT Items --
                elif item_type in ("LINE", "RECEIPT"):
                    # If we've identified a leftover image, skip lines/receipts for it
                    # but still attach lines/receipts for any included images in the batch.
                    if leftover_image_id:
                        # We need to check if this belongs to an included image or the leftover image
                        if item_type == "LINE":
                            ln = itemToLine(item)
                            if ln.image_id in included_image_ids:
                                payload[ln.image_id].setdefault("lines", []).append(ln)
                            # else belongs to leftover_image_id => skip
                        else:
                            rcpt = itemToReceipt(item)
                            if rcpt.image_id in included_image_ids:
                                payload[rcpt.image_id].setdefault("receipts", []).append(rcpt)
                            # else skip
                    else:
                        # Normal case: No leftover identified yet, so if it belongs
                        # to an included image, attach it
                        if item_type == "LINE":
                            ln = itemToLine(item)
                            if ln.image_id in included_image_ids:
                                payload[ln.image_id].setdefault("lines", []).append(ln)
                        else:
                            rcpt = itemToReceipt(item)
                            if rcpt.image_id in included_image_ids:
                                payload[rcpt.image_id].setdefault("receipts", []).append(rcpt)

                # (If you have other types like WORD, LETTER, etc., handle them similarly.)

            # End of for-loop: we've consumed all items in this batch.

            # If we found a leftover image (the (limit+1)-th), that means we are done
            # for this page. We set the LEK to the *last consumed* image (the limit-th).
            if leftover_image_id is not None:
                lek_to_return = last_consumed_image_key
                break  # Done with this page

            # If leftover_image_id is not set, then we haven't exceeded 'limit' yet in this batch
            # or we exactly reached it but no leftover was discovered.
            if images_found < limit:
                # We still need more images if there's any left. If next_key is None => no more data.
                if next_key is None:
                    # No more data in Dynamo => done
                    break
                else:
                    # More data in Dynamo => keep reading next batch
                    continue
            else:
                # We have exactly 'limit' images. If next_key is None => done entirely. 
                # If next_key is not None => we can set LEK to next_key or last_consumed_image_key
                # Typically, if we haven't seen a leftover, we just set LEK = next_key
                # so the next page starts after the last consumed item.
                if next_key is not None:
                    lek_to_return = next_key
                break

        # Return the final payload plus leftover key (if any)
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
