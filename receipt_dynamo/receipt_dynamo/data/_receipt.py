# infra/lambda_layer/python/dynamo/data/_receipt.py
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteTypeDef,
        PutRequestTypeDef,
        PutTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)
from receipt_dynamo.entities.receipt import Receipt, item_to_receipt
from receipt_dynamo.entities.receipt_details import ReceiptDetails
from receipt_dynamo.entities.receipt_letter import (
    item_to_receipt_letter,
)
from receipt_dynamo.entities.receipt_line import (
    item_to_receipt_line,
)
from receipt_dynamo.entities.receipt_word import (
    ReceiptWord,
    item_to_receipt_word,
)
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    # You might also check that each key maps to a dictionary with a DynamoDB
    # type key (e.g., "S")
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _Receipt(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    @handle_dynamodb_errors("add_receipt")
    def add_receipt(self, receipt: Receipt):
        """Adds a receipt to the database

        Args:
            receipt (Receipt): The receipt to add to the database

        Raises:
            ValueError: When a receipt with the same ID already exists
        """
        self._validate_entity(receipt, Receipt, "receipt")
        self._add_entity(
            receipt, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipts")
    def add_receipts(self, receipts: list[Receipt]):
        """Adds a list of receipts to the database

        Args:
            receipts (list[Receipt]): The receipts to add to the database

        Raises:
            ValueError: When a receipt with the same ID already exists
        """
        self._validate_entity_list(receipts, Receipt, "receipts")
        # Create write request items for batch operation
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=receipt.to_item())
            )
            for receipt in receipts
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt")
    def update_receipt(self, receipt: Receipt):
        """Updates a receipt in the database

        Args:
            receipt (Receipt): The receipt to update in the database

        Raises:
            ValueError: When the receipt does not exist
        """
        self._validate_entity(receipt, Receipt, "receipt")
        self._update_entity(
            receipt, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_receipts")
    def update_receipts(self, receipts: list[Receipt]):
        """
        Updates a list of receipts in the database using transactions.
        Each receipt update is conditional upon the receipt already existing.

        Since DynamoDB's ``transact_write_items`` supports a maximum of 25
        operations per call, the list of receipts is split into chunks of 25
        items or less. Each chunk is updated in a separate transaction.

        Args:
            receipts (list[Receipt]): The receipts to update in the database.

        Raises:
            ValueError: When given a bad parameter.
            Exception: For underlying DynamoDB errors such as:
                - ProvisionedThroughputExceededException (exceeded capacity)
                - InternalServerError (server-side error)
                - ValidationException (invalid parameters)
                - AccessDeniedException (permission issues)
                - or any other unexpected errors.
        """
        self._update_entities(receipts, Receipt, "receipts")

    @handle_dynamodb_errors("delete_receipt")
    def delete_receipt(self, receipt: Receipt):
        """Deletes a receipt from the database

        Args:
            receipt (Receipt): The receipt to delete from the database

        Raises:
            ValueError: When the receipt does not exist
        """
        self._validate_entity(receipt, Receipt, "receipt")
        self._delete_entity(
            receipt, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_receipts")
    def delete_receipts(self, receipts: list[Receipt]):
        """
        Deletes a list of receipts from the database using transactions.
        Each delete operation is conditional upon the receipt existing
        (using the ConditionExpression "attribute_exists(PK)").

        Since DynamoDB's ``transact_write_items`` supports a maximum of 25
        operations per transaction, the receipts list is split into chunks of
        25 or fewer. Each chunk is processed in a separate transaction.

        Args:
            receipts (list[Receipt]): The receipts to delete from the database.

        Raises:
            ValueError: When a receipt does not exist or another error occurs.
        """
        self._validate_entity_list(receipts, Receipt, "receipts")
        # Create transactional delete items
        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=receipt.key,
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for receipt in receipts
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt")
    def get_receipt(self, image_id: str, receipt_id: int) -> Receipt:
        """
        Retrieves a receipt from the database.

        Args:
            image_id (str): The ID of the image the receipt belongs to.
            receipt_id (int): The ID of the receipt to retrieve.

        Returns:
            Receipt: The receipt object.

        Raises:
            ValueError: If input parameters are invalid or the receipt does not
                exist.
            Exception: For underlying DynamoDB errors such as:
                - ResourceNotFoundException (table or index not found)
                - ProvisionedThroughputExceededException (exceeded capacity)
                - ValidationException (invalid parameters)
                - InternalServerError (server-side error)
                - AccessDeniedException (permission issues)
                - or any other unexpected errors.
        """
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")

        # Validate image_id as a UUID and receipt_id as a positive integer.
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int):
            raise ValueError("Receipt ID must be an integer.")
        if receipt_id < 0:
            raise ValueError("Receipt ID must be a positive integer.")

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
            },
        )
        if "Item" in response:
            return item_to_receipt(response["Item"])
        else:
            raise EntityNotFoundError(
                (
                    f"Receipt with ID {receipt_id} and Image ID "
                    f"'{image_id}' does not exist."
                )
            )

    @handle_dynamodb_errors("get_receipt_details")
    def get_receipt_details(
        self, image_id: str, receipt_id: int
    ) -> ReceiptDetails:
        """Get a receipt with its details

        Args:
            image_id (int): The ID of the image the receipt belongs to
            receipt_id (int): The ID of the receipt to get

        Returns:
            ReceiptDetails: Dataclass with receipt and related data
        """
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
            },
        }
        receipt = None
        lines, words, letters, labels = [], [], [], []
        while True:
            response = self._client.query(**query_params)
            for item in response.get("Items", []):
                if item["TYPE"]["S"] == "RECEIPT":
                    receipt = item_to_receipt(item)
                elif item["TYPE"]["S"] == "RECEIPT_LINE":
                    lines.append(item_to_receipt_line(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD":
                    words.append(item_to_receipt_word(item))
                elif item["TYPE"]["S"] == "RECEIPT_LETTER":
                    letters.append(item_to_receipt_letter(item))
                elif item["TYPE"]["S"] == "RECEIPT_WORD_LABEL":
                    labels.append(item_to_receipt_word_label(item))
            # paginate
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                break
        if receipt is None:
            raise ValueError(
                (
                    "Receipt not found for "
                    f"image_id={image_id}, receipt_id={receipt_id}"
                )
            )
        return ReceiptDetails(
            receipt=receipt,
            lines=lines,
            words=words,
            letters=letters,
            labels=labels,
        )

    @handle_dynamodb_errors("list_receipts")
    def list_receipts(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Receipt], dict | None]:
        """
        Retrieve receipt records from the database with support for precise
        pagination.

        This method queries the database for items identified as receipts and
        returns a list of corresponding Receipt objects along with a pagination
        key (LastEvaluatedKey) for subsequent queries. When a limit is
        provided, the method continues paginating until it accumulates exactly
        that number of receipts (or until no more items are available). If no
        limit is specified, the method retrieves all available receipts.

        Parameters:
            limit (int, optional): The maximum number of receipt items to
                return. If ``None``, all receipts are fetched.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query, used to continue a previous pagination
                session.

        Returns:
            tuple:
                - A list of Receipt objects, containing up to ``limit`` items
                      if a limit is specified.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or ``None`` if there are no further pages.

        Raises:
            ValueError: If the limit is not an integer or is less than or equal
                to 0.
            ValueError: If the last_evaluated_key is not a dictionary.
            Exception: If the underlying database query fails.

        Notes:
            - For each query iteration, if a limit is provided, the method
                dynamically calculates the remaining number of items needed and
                adjusts the query's ``Limit`` parameter accordingly.
            - This approach ensures that exactly the specified number of
                receipts is returned (when available), even if it requires
                multiple query operations.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        receipts: List[Receipt] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                # If a limit is provided, adjust the query's Limit to only
                # fetch what is needed.
                if limit is not None:
                    remaining = limit - len(receipts)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                receipts.extend(
                    [item_to_receipt(item) for item in response["Items"]]
                )

                # If we have reached or exceeded the limit, trim the list and
                # break.
                if limit is not None and len(receipts) >= limit:
                    # ensure we return exactly the limit
                    receipts = receipts[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                # Continue paginating if there's more data; otherwise, we're
                # done.
                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return receipts, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipts from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipts from the database: {e}"
                ) from e

    @handle_dynamodb_errors("get_receipts_from_image")
    def get_receipts_from_image(self, image_id: int) -> list[Receipt]:
        """List all receipts from an image using the GSI

        Args:
            image_id (int): The ID of the image to list receipts from

        Returns:
            list[Receipt]: A list of receipts from the image
        """
        receipts: List[Receipt] = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": "RECEIPT#"},
                },
            )
            receipts.extend(
                [item_to_receipt(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"IMAGE#{image_id}"},
                        ":sk": {"S": "RECEIPT#"},
                    },
                )
                receipts.extend(
                    [item_to_receipt(item) for item in response["Items"]]
                )
            return receipts
        except ClientError as e:
            raise ValueError(f"Error listing receipts from image: {e}")

    @handle_dynamodb_errors("list_receipt_details")
    def list_receipt_details(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        Dict[
            str,
            Dict[
                str, Union[Receipt, List[ReceiptWord], List[ReceiptWordLabel]]
            ],
        ],
        Optional[Dict],
    ]:
        """List receipts with their words and word labels using GSI2.

        This method queries the database for all receipt items using GSI2
        (where GSI2PK = 'RECEIPT') and returns a dictionary containing the
        receipt details, including associated words and word labels.

        Args:
            limit (Optional[int], optional): The maximum number of receipt
                details to return. Defaults to ``None``.
            last_evaluated_key (Optional[dict], optional): The key to start the
                query from for pagination. Defaults to ``None``.

        Returns:
            Tuple[Dict[str, Dict], Optional[Dict]]: A tuple containing:
                - Dictionary mapping
                    "<image_id>_<receipt_id>" to a dictionary with:
                    - "receipt": The Receipt object
                    - "words": List of ReceiptWord objects
                    - "word_labels": List of ReceiptWordLabel objects
                - Last evaluated key for pagination (None if no more pages)

        Raises:
            ValueError: If there is an error querying the database
        """
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :pk",
                "ExpressionAttributeValues": {":pk": {"S": "RECEIPT"}},
                "ScanIndexForward": True,
            }

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            payload: Dict[str, Any] = {}
            current_receipt = None
            current_key = None
            receipt_count = 0

            while True:
                response = self._client.query(**query_params)

                for item in response["Items"]:
                    item_type = item["TYPE"]["S"]

                    if item_type == "RECEIPT":
                        # If we've hit our limit, use this receipt's key as the
                        # LEK and stop
                        if limit is not None and receipt_count >= limit:
                            last_evaluated_key = {
                                "PK": item["PK"],
                                "SK": item["SK"],
                                "GSI2PK": item["GSI2PK"],
                                "GSI2SK": item["GSI2SK"],
                            }
                            return payload, last_evaluated_key

                        receipt = item_to_receipt(item)
                        current_key = (
                            f"{receipt.image_id}_{receipt.receipt_id}"
                        )
                        payload[current_key] = {
                            "receipt": receipt,
                            "words": [],
                            "word_labels": [],
                        }
                        current_receipt = receipt
                        receipt_count += 1

                    elif (
                        item_type == "RECEIPT_WORD"
                        and current_receipt
                        and current_key is not None
                    ):
                        word = item_to_receipt_word(item)
                        if (
                            word.image_id == current_receipt.image_id
                            and word.receipt_id == current_receipt.receipt_id
                        ):
                            payload[current_key]["words"].append(word)

                    elif (
                        item_type == "RECEIPT_WORD_LABEL"
                        and current_receipt
                        and current_key is not None
                    ):
                        label = item_to_receipt_word_label(item)
                        if (
                            label.image_id == current_receipt.image_id
                            and label.receipt_id == current_receipt.receipt_id
                        ):
                            payload[current_key]["word_labels"].append(label)

                # If no more pages
                if "LastEvaluatedKey" not in response:
                    return payload, None

                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]

        except ClientError as e:
            raise ValueError(
                "Could not list receipt details from the database"
            ) from e

    @handle_dynamodb_errors("list_receipt_and_words")
    def list_receipt_and_words(
        self, image_id: str, receipt_id: int
    ) -> tuple[Receipt, list[ReceiptWord]]:
        """List a receipt and its words using GSI3

        Args:
            image_id (str): The ID of the image to list receipts from
            receipt_id (int): The ID of the receipt to list words from

        Returns:
            tuple[Receipt, list[ReceiptWord]]: A tuple containing:
                - The receipt object
                - List of receipt words sorted by line_id and word_id

        Raises:
            ValueError: When input parameters are invalid or if the receipt
                does not exist.
            Exception: For underlying DynamoDB errors
        """
        if image_id is None:
            raise ValueError("Image ID is required")
        if receipt_id is None:
            raise ValueError("Receipt ID is required")
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int):
            raise ValueError("Receipt ID must be an integer")
        if receipt_id < 0:
            raise ValueError("Receipt ID must be positive")

        try:
            # Use GSI3 to get both receipt and words in a single query
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI3",
                KeyConditionExpression=(
                    "GSI3PK = :pk AND begins_with(GSI3SK, :sk)"
                ),
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            )

            receipt = None
            words = []

            # Process items
            for item in response.get("Items", []):
                item_type = item.get("TYPE", {}).get("S")
                if item_type == "RECEIPT":
                    receipt = item_to_receipt(item)
                elif item_type == "RECEIPT_WORD":
                    try:
                        word = item_to_receipt_word(item)
                        words.append(word)
                    except ValueError as e:
                        # TODO: Use proper logging instead of print
                        continue

            if not receipt:
                raise ValueError(
                    (
                        f"Receipt with ID {receipt_id} and Image ID "
                        f"'{image_id}' does not exist"
                    )
                )

            # Sort words by line_id and word_id
            words.sort(key=lambda w: (w.line_id, w.word_id))

            return receipt, words

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ValueError(f"Receipt not found: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"Validation exception: {e}"
                ) from e
            else:
                raise OperationError(
                    f"Error listing receipt and words: {e}"
                ) from e
