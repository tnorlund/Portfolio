# infra/lambda_layer/python/dynamo/data/_receipt.py
from typing import Any, Dict, Optional

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt import Receipt, item_to_receipt
from receipt_dynamo.entities.receipt_details import ReceiptDetails
from receipt_dynamo.entities.receipt_letter import item_to_receipt_letter
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_summary import ReceiptSummaryPage
from receipt_dynamo.entities.receipt_word import (
    ReceiptWord,
    item_to_receipt_word,
)
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

from ._receipt_details_processor import process_receipt_details_query


class _Receipt(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
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
        self._transact_write_with_chunking(
            transact_items  # type: ignore[arg-type]
        )

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
        self._validate_image_id(image_id)
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer.")
        if receipt_id < 0:
            raise EntityValidationError(
                "receipt_id must be a positive integer."
            )

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}",
            entity_class=Receipt,
            converter_func=item_to_receipt,
        )

        if result is None:
            raise EntityNotFoundError(
                f"receipt with receipt_id={receipt_id} and "
                f"image_id={image_id} does not exist."
            )

        # Type assertion: we know this is a Receipt due to converter_func
        return result  # type: ignore[no-any-return]

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

        # Custom converter function that handles multiple entity types
        def convert_item(item):
            item_type = item.get("TYPE", {}).get("S")
            if item_type == "RECEIPT":
                return ("receipt", item_to_receipt(item))
            if item_type == "RECEIPT_LINE":
                return ("line", item_to_receipt_line(item))
            if item_type == "RECEIPT_WORD":
                return ("word", item_to_receipt_word(item))
            if item_type == "RECEIPT_LETTER":
                return ("letter", item_to_receipt_letter(item))
            if item_type == "RECEIPT_WORD_LABEL":
                return ("label", item_to_receipt_word_label(item))
            return None

        # Query all items for this receipt
        items, _ = self._query_entities(
            index_name=None,  # Query main table
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
            },
            converter_func=convert_item,
            limit=None,  # Get all items
            last_evaluated_key=None,
        )

        receipt = None
        lines, words, letters, labels = [], [], [], []

        # Process converted items
        for item in items:
            if item is None:
                continue
            item_type, entity = item
            if item_type == "receipt":
                receipt = entity
            elif item_type == "line":
                lines.append(entity)
            elif item_type == "word":
                words.append(entity)
            elif item_type == "letter":
                letters.append(entity)
            elif item_type == "label":
                labels.append(entity)

        if receipt is None:
            raise EntityNotFoundError(
                (
                    "receipt not found for "
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
        # Validate parameters using base operations helper
        self._validate_pagination_params(limit, last_evaluated_key)

        # Additional validation specific to list_receipts
        return self._query_by_type(
            entity_type="RECEIPT",
            converter_func=item_to_receipt,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipts_from_image")
    def get_receipts_from_image(self, image_id: str) -> list[Receipt]:
        """List all receipts from an image using the GSI

        Args:
            image_id (int): The ID of the image to list receipts from

        Returns:
            list[Receipt]: A list of receipts from the image
        """
        receipts, _ = self._query_entities(
            index_name=None,  # Query main table
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names={
                "#type": "TYPE",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": "RECEIPT#"},
                ":type": {"S": "RECEIPT"},
            },
            converter_func=item_to_receipt,
            filter_expression="#type = :type",
            limit=None,  # Get all receipts
            last_evaluated_key=None,
        )
        return receipts

    @handle_dynamodb_errors("list_receipt_details")
    def list_receipt_details(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> ReceiptSummaryPage:
        """List receipts with their words and word labels using GSI2.

        This method queries the database for receipt-related items using GSI2
        (where GSI2PK = 'RECEIPT') and returns a page of receipt summaries.

        Note: With the addition of new entities using the same GSI2PK pattern
        (ReceiptLineItemAnalysis, ReceiptLabelAnalysis), this method now uses
        a filter expression to only retrieve the specific types needed.

        Args:
            limit: The maximum number of receipt summaries to return.
                   Defaults to None (return all).
            last_evaluated_key: The key to start the query from for
                               pagination. Defaults to None.

        Returns:
            ReceiptSummaryPage: A page containing:
                - summaries: Dict mapping composite keys to ReceiptSummary
                            objects
                - last_evaluated_key: Key for next page (None if no more)

        Raises:
            EntityValidationError: If input parameters are invalid
            DynamoDBError: If the database query fails
        """
        # Validate inputs
        self._validate_pagination_params(limit, last_evaluated_key)

        # Build query parameters
        query_params = {
            "TableName": self.table_name,
            "IndexName": "GSI2",
            "KeyConditionExpression": "GSI2PK = :pk",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {
                ":pk": {"S": "RECEIPT"},
                ":receipt": {"S": "RECEIPT"},
                ":word": {"S": "RECEIPT_WORD"},
                ":label": {"S": "RECEIPT_WORD_LABEL"},
            },
            "FilterExpression": "#t IN (:receipt, :word, :label)",
            "ScanIndexForward": True,
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        # Use processor function to handle the complex query logic
        return process_receipt_details_query(self._client, query_params, limit)

    @handle_dynamodb_errors("list_receipt_and_words")
    def list_receipt_and_words(
        self, image_id: str, receipt_id: int
    ) -> tuple[Receipt, list[ReceiptWord]]:
        """DEPRECATED: List a receipt and its words using GSI3

        This method is deprecated due to GSI3 optimization changes.
        Use get_receipt() and list_receipt_words_from_receipt() instead.

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
        import warnings

        warnings.warn(
            "list_receipt_and_words is deprecated. Use get_receipt() and "
            "list_receipt_words_from_receipt() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._validate_image_id(image_id)
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer")
        if receipt_id < 0:
            raise EntityValidationError("receipt_id must be positive")

        # Use the recommended alternative methods
        try:
            receipt = self.get_receipt(image_id, receipt_id)
        except EntityNotFoundError:
            raise EntityNotFoundError(
                f"receipt with receipt_id={receipt_id} and "
                f"image_id={image_id} does not exist"
            )

        words = self.list_receipt_words_from_receipt(image_id, receipt_id)

        # Sort words by line_id and word_id
        words.sort(key=lambda w: (w.line_id, w.word_id))

        return receipt, words
