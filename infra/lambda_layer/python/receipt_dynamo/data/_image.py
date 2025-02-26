# infra/lambda_layer/python/dynamo/data/_image.py
from typing import Optional, List, Tuple, Dict, Union
from receipt_dynamo import (
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
    GPTInitialTagging,
    GPTValidation,
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
    itemToGPTInitialTagging,
    itemToGPTValidation,
)
from receipt_dynamo.entities import assert_valid_uuid
from botocore.exceptions import ClientError

from receipt_dynamo.entities.receipt_window import itemToReceiptWindow

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
    getImage(image_id: str) -> Image:
        Retrieves a single Image item by its ID.
    getMaxImageId() -> int:
        Retrieves the maximum image ID found in the database.
    updateImage(image: Image):
        Updates an existing Image item in the database.
    getImageDetails(image_id: str) -> tuple[Image,
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
    deleteImage(image_id: str):
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
        # Validate the image parameter.
        if image is None:
            raise ValueError("Image parameter is required and cannot be None.")
        if not isinstance(image, Image):
            raise ValueError("image must be an instance of the Image class.")

        # Attempt to put the image item into DynamoDB with a condition that prevents
        # overwriting an existing image.
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=image.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Image with ID {image.image_id} already exists"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error putting image: {e}") from e

    def addImages(self, images: List[Image]):
        """
        Adds multiple Image items to the database in batches of up to 25.

        This method validates that the provided parameter is a list of Image instances.
        It uses DynamoDB's batch_write_item operation, which can handle up to 25 items
        per batch. Any unprocessed items are automatically retried until no unprocessed
        items remain.

        Parameters
        ----------
        images : list[Image]
            The list of Image objects to be added.

        Raises
        ------
        ValueError
            If the images parameter is None, not a list, or if any element in the list
            is not an instance of the Image class, or if an error occurs while adding the images.
        Exception
            For any other errors encountered during the batch write operation.
        """
        if images is None:
            raise ValueError("Images parameter is required and cannot be None.")
        if not isinstance(images, list):
            raise ValueError("Images must be provided as a list.")
        if not all(isinstance(img, Image) for img in images):
            raise ValueError(
                "All items in the images list must be instances of the Image class."
            )

        try:
            for i in range(0, len(images), 25):
                chunk = images[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": image.to_item()}} for image in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error adding images: {e}") from e

    def getImage(self, image_id: str) -> Image:
        """
        Retrieves a single Image item by its ID from the database after validating the input.

        Parameters
        ----------
        image_id : str
            The UUID of the image to retrieve.

        Returns
        -------
        Image
            The retrieved Image object.

        Raises
        ------
        ValueError
            If image_id is not provided, is not a UUID, or if no image is found with the specified ID.
        Exception
            For various DynamoDB ClientErrors such as ProvisionedThroughputExceededException,
            ResourceNotFoundException, InternalServerError, or any other error encountered during the get_item operation.
        """
        # Validate the image_id parameter.
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        assert_valid_uuid(image_id)

        # Attempt to retrieve the image item from DynamoDB.
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": "IMAGE"}},
            )
            if "Item" not in response or not response["Item"]:
                raise ValueError(f"Image with ID {image_id} not found")
            return itemToImage(response["Item"])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Table {self.table_name} not found: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting image: {e}") from e

    def updateImage(self, image: Image):
        """
        Updates an existing Image item in the database after validating the input.

        This method ensures that the provided image parameter is valid and that the
        DynamoDB client and table name are properly configured. It uses a conditional
        put to ensure that the item exists before updating.

        Parameters
        ----------
        image : Image
            The Image object containing updated data.

        Raises
        ------
        ValueError
            If the image parameter is None, is not an instance of the Image class,
            or if the image does not exist.
        Exception
            If any other error occurs during the update operation.
        """
        # Validate the image parameter.
        if image is None:
            raise ValueError("Image parameter is required and cannot be None.")
        if not isinstance(image, Image):
            raise ValueError("image must be an instance of the Image class.")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=image.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(f"Image with ID {image.image_id} not found") from e
            else:
                raise Exception(f"Error updating image: {e}") from e
            
    def updateImages(self, images: List[Image]):
        """
        Updates multiple Image items in the database.

        This method validates that the provided parameter is a list of Image instances.
        It uses DynamoDB's transact_write_items operation, which can handle up to 25 items
        per transaction. Any unprocessed items are automatically retried until no unprocessed
        items remain.

        Parameters
        ----------
        images : list[Image]
            The list of Image objects to update.

        Raises
        ------
        ValueError: When given a bad parameter.
        Exception: For underlying DynamoDB errors such as:
            - ProvisionedThroughputExceededException (exceeded capacity)
            - InternalServerError (server-side error)
            - ValidationException (invalid parameters)
            - AccessDeniedException (permission issues)
            - or any other unexpected errors.
        """
        if images is None:
            raise ValueError("Images parameter is required and cannot be None.")
        if not isinstance(images, list):
            raise ValueError("Images must be provided as a list.")
        if not all(isinstance(img, Image) for img in images):
            raise ValueError("All items in the images list must be instances of the Image class.")
        
        for i in range(0, len(images), 25):
            chunk = images[i : i + 25]
            transact_items = []
            for image in chunk:
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": image.to_item(),
                            "ConditionExpression": "attribute_exists(PK)",
                        }
                    }
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(f"One or more images do not exist") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception(f"Provisioned throughput exceeded: {e}") from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise Exception(f"One or more parameters given were invalid: {e}") from e
                elif error_code == "AccessDeniedException":
                    raise Exception(f"Access denied: {e}") from e
                else:
                    raise ValueError(f"Error updating images: {e}") from e

    def getImageDetails(self, image_id: str) -> tuple[
        list[Image],
        list[Line],
        list[Word],
        list[WordTag],
        list[Letter],
        list[Receipt],
        list[ReceiptLine],
        list[ReceiptWord],
        list[ReceiptWordTag],
        list[ReceiptLetter],
        list[GPTInitialTagging],
        list[GPTValidation],
    ]:
        """
        Retrieves detailed information about an Image from the database,
        including its lines, words, letters, and any associated receipts.

        This method queries all items matching the partition key ("IMAGE#{image_id}")
        and then groups items by their type to build a comprehensive view of the
        Image's related data.

        Parameters
        ----------
        image_id : str
            The UUID of the image for which to retrieve details.

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
        images = []
        lines = []
        words = []
        word_tags = []
        letters = []
        receipts = []
        receipt_windows = []
        receipt_lines = []
        receipt_words = []
        receipt_word_tags = []
        receipt_letters = []
        gpt_initial_taggings = []
        gpt_validations = []
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

            for item in items:
                if item["TYPE"]["S"] == "IMAGE":
                    images.append(itemToImage(item))
                elif item["TYPE"]["S"] == "LINE":
                    lines.append(itemToLine(item))
                elif item["TYPE"]["S"] == "WORD":
                    words.append(itemToWord(item))
                elif item["TYPE"]["S"] == "WORD_TAG":
                    word_tags.append(itemToWordTag(item))
                elif item["TYPE"]["S"] == "LETTER":
                    letters.append(itemToLetter(item))
                elif item["TYPE"]["S"] == "RECEIPT":
                    receipts.append(itemToReceipt(item))
                elif item["TYPE"]["S"] == "RECEIPT_WINDOW":
                    receipt_windows.append(itemToReceiptWindow(item))
                elif item["TYPE"]["S"] == "RECEIPT_LINE":
                    receipt_lines.append(itemToReceiptLine(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD":
                    receipt_words.append(itemToReceiptWord(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD_TAG":
                    receipt_word_tags.append(itemToReceiptWordTag(item))
                elif item["TYPE"]["S"] == "RECEIPT_LETTER":
                    receipt_letters.append(itemToReceiptLetter(item))
                elif item["TYPE"]["S"] == "GPT_INITIAL_TAGGING":
                    gpt_initial_taggings.append(itemToGPTInitialTagging(item))
                elif item["TYPE"]["S"] == "GPT_VALIDATION":
                    gpt_validations.append(itemToGPTValidation(item))

            return (
                images,
                lines,
                words,
                word_tags,
                letters,
                receipts,
                receipt_windows,
                receipt_lines,
                receipt_words,
                receipt_word_tags,
                receipt_letters,
                gpt_initial_taggings,
                gpt_validations,
            )

        except Exception as e:
            raise Exception(f"Error getting image details: {e}")

    def deleteImage(self, image_id: str):
        """
        Deletes an Image item from the database by its ID.

        Uses a conditional expression to ensure that the Image exists before deletion.

        Parameters
        ----------
        image_id : str
            The UUID of the image to delete.

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
        self, limit: Optional[int] = None, last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[
        Dict[int, Dict[str, Union[Image, List[Receipt], List[Line]]]], Optional[Dict]
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
                        payload[receipt.image_id].setdefault("receipts", []).append(
                            receipt
                        )
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
                        last_consumed_image_key = {**img.key(), **img.gsi1_key()}
                    else:
                        # This is the (limit+1)-th image => leftover
                        leftover_img = itemToImage(item)
                        leftover_image_id = leftover_img.image_id
                        leftover_image_key = {
                            **leftover_img.key(),
                            **leftover_img.gsi1_key(),
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
                                payload[rcpt.image_id].setdefault(
                                    "receipts", []
                                ).append(rcpt)
                    else:
                        # No leftover identified yet, so attach to included images if relevant
                        if item_type == "LINE":
                            ln = itemToLine(item)
                            if ln.image_id in included_image_ids:
                                payload[ln.image_id].setdefault("lines", []).append(ln)
                        else:
                            rcpt = itemToReceipt(item)
                            if rcpt.image_id in included_image_ids:
                                payload[rcpt.image_id].setdefault(
                                    "receipts", []
                                ).append(rcpt)

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

    def listImagesWordsTags(
        self, image_id: str, limit: Optional[int] = None, lastEvaluatedKey: Optional[Dict] = None
    ) -> Tuple[List[Image], List[WordTag], Optional[Dict]]:
        """
        Lists images and their associated words and tags from the database.

        This function retrieves images along with their associated words and tags
        from the database.

        Parameters
        ----------
        image_id : str
            The ID of the image to retrieve.
        limit : int, optional
            The maximum number of images to return in one query.
        lastEvaluatedKey : dict, optional
            The key from which to continue a previous paginated query.

        Returns
        -------
        tuple
            A tuple containing:
            1) The Image object.
            2) A list of Word objects.
            3) A list of WordTag objects.
            4) The LastEvaluatedKey (dict) if more items remain, otherwise None.

        Raises
        ------
        ValueError
            If there's an error listing images from DynamoDB.
        """
        image = None
        words = []
        word_tags = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :val",
                "ExpressionAttributeValues": {":val": {"S": f"IMAGE#{image_id}"}},
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey    

            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            for item in response["Items"]:
                if item["TYPE"]["S"] == "IMAGE":
                    image = itemToImage(item)
                elif item["TYPE"]["S"] == "WORD":
                    words.append(itemToWord(item))
                elif item["TYPE"]["S"] == "WORD_TAG":
                    word_tags.append(itemToWordTag(item))

            last_evaluated_key = response.get("LastEvaluatedKey", None)
            return image, words, word_tags, last_evaluated_key

        except ClientError as e:
            raise ValueError(f"Could not list images and words and tags from the database: {e}")

    def listImages(
        self, limit: Optional[int] = None, lastEvaluatedKey: Optional[Dict] = None
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
