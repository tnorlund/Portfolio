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
# So we chunk the items in groups of 25 for bulk operations.
CHUNK_SIZE = 25


class _Image:
    """
    A class providing methods to interact with "Image" entities in DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    image records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    addImage(image: Image):
        Adds a single Image item to the database, ensuring a unique ID.
    addImages(images: List[Image]):
        Adds multiple Image items to the database in chunks of up to 25 items.
    getImage(image_id: int) -> Image:
        Retrieves a single Image item by its ID.
    getMaxImageId() -> int:
        Retrieves the maximum image ID found in the database.
    updateImage(image: Image):
        Updates an existing Image item in the database.
    getImageDetails(image_id: int) -> tuple[Image,
                                            list[Line],
                                            list[Word],
                                            list[WordTag],
                                            list[Letter],
                                            list[Dict[str, Union[Receipt,
                                                                 list[ReceiptLine],
                                                                 list[Word],
                                                                 list[Letter]]]]]:
        Retrieves comprehensive details for an Image, including lines, words, letters,
        and receipt data (if any) associated with the Image.
    deleteImage(image_id: int):
        Deletes a single Image item from the database by its ID.
    deleteImages(images: list[Image]):
        Deletes multiple Image items in chunks of up to 25 items.
    listImageDetails(limit: Optional[int] = None,
                     last_evaluated_key: Optional[Dict] = None)
                     -> Tuple[Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]],
                              Optional[Dict]]:
        Lists images (via GSI) with optional pagination and returns their basic details.
    listImages(limit: Optional[int] = None,
               lastEvaluatedKey: Optional[Dict] = None) -> Tuple[List[Image], Optional[Dict]]:
        Lists images (via GSI) with optional pagination, returning Image objects directly.
    """

    def addImage(self, image: Image):
        """
        Adds an Image item to the database.

        Uses a conditional put to ensure that the item does not overwrite
        an existing image with the same ID.

        Parameters
        ----------
        image : Image
            The Image object to be added.

        Raises
        ------
        ValueError
            If an image with the same ID already exists.
        Exception
            If another error occurs while putting the item.
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
        """
        Adds multiple Image items to the database in batches of up to 25.

        DynamoDB's batch_write_item is used, which can handle up to 25 items
        per batch. Any unprocessed items are automatically retried until no
        unprocessed items remain.

        Parameters
        ----------
        images : list[Image]
            The list of Image objects to be added.

        Raises
        ------
        ValueError
            If an error occurs while adding the images in batch.
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
        """
        Retrieves a single Image item by its ID from the database.

        Parameters
        ----------
        image_id : int
            The ID of the image to retrieve.

        Returns
        -------
        Image
            The retrieved Image object.

        Raises
        ------
        ValueError
            If no image is found with the specified ID.
        Exception
            If another error occurs during the get operation.
        """
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": "IMAGE"}},
            )
            return itemToImage(response["Item"])
        except KeyError:
            raise ValueError(f"Image with ID {image_id} not found")

    def getMaxImageId(self) -> int:
        """
        Retrieves the maximum image ID from the database.

        Queries a global secondary index (GSI) sorted in descending order
        to get the highest image ID quickly. If no images exist, returns 0.

        Returns
        -------
        int
            The maximum image ID, or 0 if no images are found.

        Raises
        ------
        Exception
            If there is an error querying DynamoDB.
        """
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
                return 0  # No images found

            pk_str = items[0]["PK"]["S"]  # e.g. "IMAGE#00042"
            image_id_str = pk_str.split("#")[1]  # "00042"
            return int(image_id_str)
        except Exception as e:
            raise Exception(f"Error getting max image ID: {e}")

    def updateImage(self, image: Image):
        """
        Updates an existing Image item in the database.

        Uses a conditional put to ensure that the item exists before updating.

        Parameters
        ----------
        image : Image
            The Image object containing updated data.

        Raises
        ------
        ValueError
            If the image does not exist.
        Exception
            If another error occurs during the update operation.
        """
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

    def getImageDetails(
        self, image_id: int
    ) -> tuple[
        Image,
        list[Line],
        list[Word],
        list[WordTag],
        list[Letter],
        list[Dict[str, Union[Receipt, list[ReceiptLine], list[Word], list[Letter]]]],
    ]:
        """
        Retrieves detailed information about an Image from the database,
        including its lines, words, letters, and any associated receipts.

        This method queries all items matching the partition key ("IMAGE#{image_id}")
        and then groups items by their type to build a comprehensive view of the
        Image's related data.

        Parameters
        ----------
        image_id : int
            The ID of the image for which to retrieve details.

        Returns
        -------
        tuple
            A tuple containing:
            1) The Image object.
            2) A list of Line objects.
            3) A list of Word objects.
            4) A list of WordTag objects.
            5) A list of Letter objects.
            6) A list of dictionaries, each representing a Receipt and its associated
               lines, words, word_tags, and letters.

        Raises
        ------
        Exception
            If there is an error querying DynamoDB.
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

            # Keep querying if there's a LastEvaluatedKey
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
        """
        Deletes an Image item from the database by its ID.

        Uses a conditional expression to ensure that the Image exists before deletion.

        Parameters
        ----------
        image_id : int
            The ID of the image to delete.

        Raises
        ------
        ValueError
            If the image does not exist.
        Exception
            If another error occurs during the delete operation.
        """
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
        """
        Deletes multiple Image items from the database in batches of up to 25 items.

        Any unprocessed items are automatically retried until none remain.

        Parameters
        ----------
        images : list[Image]
            The list of Image objects to delete.

        Raises
        ------
        ValueError
            If an error occurs while deleting the images in batch.
        """
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
        Lists images and their basic associated details (lines, receipts) using a
        global secondary index where GSI1PK = 'IMAGE'. Supports optional pagination.

        Parameters
        ----------
        limit : int, optional
            The maximum number of images to return in this call.
            If None, all images are returned (paginating until exhausted).
        last_evaluated_key : dict, optional
            The DynamoDB key from where the next page should start (for pagination).

        Returns
        -------
        tuple
            A tuple containing:
            1) A dictionary keyed by image_id, where each value is another dict with:
               {
                   "image": Image,
                   "lines": List[Line],
                   "receipts": List[Receipt],
               }
            2) The LastEvaluatedKey dict if more items remain, otherwise None.
        """
        if limit is None and last_evaluated_key is None:
            # CASE 1: Return *all* images
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

        # CASE 2: Paginated => Return exactly 'limit' images (if available)
        payload: Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]] = {}
        included_image_ids = set()
        images_found = 0
        lek_to_return = None

        next_key = last_evaluated_key

        while True:
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
            next_key = response.get("LastEvaluatedKey")

            leftover_image_id = None
            leftover_image_key = None
            last_consumed_image_key = None

            for item in items:
                sk = item["SK"]["S"]
                item_type = item["TYPE"]["S"]

                # IMAGE item
                if sk == "IMAGE":
                    if images_found < limit:
                        img = itemToImage(item)
                        payload[img.image_id] = {"image": img}
                        included_image_ids.add(img.image_id)
                        images_found += 1
                        last_consumed_image_key = {
                            **img.key(),
                            **img.gsi1_key()
                        }
                    else:
                        # This is the (limit+1)-th image => leftover
                        leftover_img = itemToImage(item)
                        leftover_image_id = leftover_img.image_id
                        leftover_image_key = {
                            **leftover_img.key(),
                            **leftover_img.gsi1_key()
                        }
                
                # LINE or RECEIPT item
                elif item_type in ("LINE", "RECEIPT"):
                    if leftover_image_id:
                        # Only attach data if it belongs to an included image
                        if item_type == "LINE":
                            ln = itemToLine(item)
                            if ln.image_id in included_image_ids:
                                payload[ln.image_id].setdefault("lines", []).append(ln)
                        else:
                            rcpt = itemToReceipt(item)
                            if rcpt.image_id in included_image_ids:
                                payload[rcpt.image_id].setdefault("receipts", []).append(rcpt)
                    else:
                        # No leftover identified yet, so attach to included images if relevant
                        if item_type == "LINE":
                            ln = itemToLine(item)
                            if ln.image_id in included_image_ids:
                                payload[ln.image_id].setdefault("lines", []).append(ln)
                        else:
                            rcpt = itemToReceipt(item)
                            if rcpt.image_id in included_image_ids:
                                payload[rcpt.image_id].setdefault("receipts", []).append(rcpt)

            if leftover_image_id is not None:
                # We found the leftover (limit+1)-th image, so we're done for this page
                lek_to_return = last_consumed_image_key
                break

            if images_found < limit:
                # Need more images if available
                if next_key is None:
                    # No more data left in Dynamo
                    break
                else:
                    continue
            else:
                # We have exactly 'limit' images or just reached it
                if next_key is not None:
                    lek_to_return = next_key
                break

        return payload, lek_to_return

    def listImages(
        self, 
        limit: Optional[int] = None, 
        lastEvaluatedKey: Optional[Dict] = None
    ) -> Tuple[List[Image], Optional[Dict]]:
        """
        Lists images from the database via a global secondary index
        (named "GSITYPE" in this implementation) on the "TYPE" attribute.

        If 'limit' is provided, a single query up to that many items is returned,
        along with a LastEvaluatedKey for pagination if more remain. If 'limit' is
        None, all images are retrieved by paginating until all items are fetched.

        Parameters
        ----------
        limit : int, optional
            The maximum number of images to return in one query.
        lastEvaluatedKey : dict, optional
            The key from which to continue a previous paginated query.

        Returns
        -------
        tuple
            A tuple containing:
            1) A list of Image objects.
            2) The LastEvaluatedKey (dict) if more items remain, otherwise None.

        Raises
        ------
        ValueError
            If there's an error listing images from DynamoDB.
        """
        images = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "IMAGE"}},
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            images.extend([itemToImage(item) for item in response["Items"]])

            if limit is None:
                # If no limit is provided, paginate until all items are retrieved
                while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    images.extend([itemToImage(item) for item in response["Items"]])
                last_evaluated_key = None
            else:
                # If a limit is provided, capture the LastEvaluatedKey (if any)
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return images, last_evaluated_key

        except ClientError as e:
            raise ValueError(f"Could not list images from the database: {e}")