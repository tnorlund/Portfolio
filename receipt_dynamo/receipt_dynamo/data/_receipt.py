# infra/lambda_layer/python/dynamo/data/_receipt.py
from collections import Counter
from typing import Any

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    DeleteTypeDef,
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
from receipt_dynamo.entities.receipt_barcode import item_to_receipt_barcode
from receipt_dynamo.entities.receipt_bundle import ReceiptBundlePage
from receipt_dynamo.entities.receipt_details import ReceiptDetails
from receipt_dynamo.entities.receipt_line import (
    item_to_receipt_line,
)
from receipt_dynamo.entities.receipt_place import (
    item_to_receipt_place,
)
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)

from ._receipt_details_processor import process_receipt_details_query

_RECEIPT_DETAILS_CONVERTERS = {
    "RECEIPT": ("receipt", item_to_receipt),
    "RECEIPT_LINE": ("line", item_to_receipt_line),
    "RECEIPT_WORD": ("word", item_to_receipt_word),
    "RECEIPT_WORD_LABEL": ("label", item_to_receipt_word_label),
    "RECEIPT_PLACE": ("place", item_to_receipt_place),
    "RECEIPT_BARCODE": ("barcode", item_to_receipt_barcode),
}


class _Receipt(FlattenedStandardMixin):
    @staticmethod
    def _convert_receipt_details_item(item):
        """Convert one GSI4 item into its ReceiptDetails collection name."""
        item_type = item.get("TYPE", {}).get("S")
        converter = _RECEIPT_DETAILS_CONVERTERS.get(item_type)
        if converter is None:
            return None
        collection_name, convert_item = converter
        return (collection_name, convert_item(item))

    @staticmethod
    def _build_receipt_details(
        items: list,
        image_id: str,
        receipt_id: int,
    ) -> ReceiptDetails:
        """Build ReceiptDetails from converted GSI4 items."""
        receipt = None
        place = None
        lines, words, labels, barcodes = [], [], [], []

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
            elif item_type == "label":
                labels.append(entity)
            elif item_type == "place":
                place = entity
            elif item_type == "barcode":
                barcodes.append(entity)

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
            labels=labels,
            place=place,
            barcodes=barcodes,
            # letters excluded by GSI4 design - uses default empty list
        )

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
        # type: ignore[arg-type]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("reserve_receipt_ids")
    def reserve_receipt_ids(
        self,
        image_id: str,
        receipt_ids: list[int],
        operation_id: str,
    ) -> None:
        """Atomically reserve receipt IDs without exposing partial receipts.

        Reservation rows deliberately omit every GSI key. Normal receipt
        listing therefore ignores them, while the shared receipt primary key
        prevents another writer from claiming the same numeric ID.
        """
        self._validate_image_id(image_id)
        if not receipt_ids or len(receipt_ids) > 23:
            raise EntityValidationError(
                "receipt_ids must contain between 1 and 23 IDs"
            )
        if len(set(receipt_ids)) != len(receipt_ids):
            raise EntityValidationError("receipt_ids must be distinct")
        if not operation_id:
            raise EntityValidationError("operation_id is required")

        transact_items = []
        for receipt_id in receipt_ids:
            self._validate_receipt_id(receipt_id)
            transact_items.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": {
                            "PK": {"S": f"IMAGE#{image_id}"},
                            "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
                            "TYPE": {"S": "RESEGMENT_RESERVATION"},
                            "operation_id": {"S": operation_id},
                        },
                        "ConditionExpression": (
                            "attribute_not_exists(PK) OR "
                            "(#type = :reservation AND operation_id = :operation_id)"
                        ),
                        "ExpressionAttributeNames": {"#type": "TYPE"},
                        "ExpressionAttributeValues": {
                            ":reservation": {"S": "RESEGMENT_RESERVATION"},
                            ":operation_id": {"S": operation_id},
                        },
                    }
                }
            )
        self._client.transact_write_items(TransactItems=transact_items)

    @handle_dynamodb_errors("commit_receipt_resegmentation")
    def commit_receipt_resegmentation(
        self,
        source_receipt: Receipt,
        output_receipts: list[Receipt],
        operation_id: str,
    ) -> None:
        """Atomically activate reserved outputs and remove the source parent."""
        self._validate_entity(source_receipt, Receipt, "source_receipt")
        self._validate_entity_list(output_receipts, Receipt, "output_receipts")
        if len(output_receipts) > 23:
            raise EntityValidationError(
                "A re-segmentation can create at most 23 receipts"
            )
        if not operation_id:
            raise EntityValidationError("operation_id is required")

        transact_items = []
        for receipt in output_receipts:
            transact_items.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": receipt.to_item(),
                        "ConditionExpression": (
                            "#type = :reservation AND "
                            "operation_id = :operation_id"
                        ),
                        "ExpressionAttributeNames": {"#type": "TYPE"},
                        "ExpressionAttributeValues": {
                            ":reservation": {"S": "RESEGMENT_RESERVATION"},
                            ":operation_id": {"S": operation_id},
                        },
                    }
                }
            )
        transact_items.append(
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": source_receipt.key,
                    "ConditionExpression": "timestamp_added = :timestamp",
                    "ExpressionAttributeValues": {
                        ":timestamp": {"S": source_receipt.timestamp_added}
                    },
                }
            }
        )
        self._client.transact_write_items(TransactItems=transact_items)

    @handle_dynamodb_errors("delete_receipt_items")
    def delete_receipt_items(
        self,
        image_id: str,
        receipt_id: int,
        *,
        include_parent: bool = True,
    ) -> int:
        """Delete every row beneath a receipt primary-key prefix.

        This intentionally deletes concrete child rows so DynamoDB stream
        consumers receive word and line removal events for vector cleanup.
        The operation is idempotent and paginates the full partition prefix.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        parent_sk = f"RECEIPT#{receipt_id:05d}"
        items: list[dict[str, Any]] = []
        exclusive_start_key = None

        while True:
            params: dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :receipt_prefix)"
                ),
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":receipt_prefix": {"S": parent_sk},
                },
                "ProjectionExpression": "PK, SK",
            }
            if exclusive_start_key:
                params["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**params)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break

        requests = []
        for item in items:
            if not include_parent and item["SK"]["S"] == parent_sk:
                continue
            requests.append(
                WriteRequestTypeDef(
                    DeleteRequest=DeleteRequestTypeDef(
                        Key={"PK": item["PK"], "SK": item["SK"]}
                    )
                )
            )
        if requests:
            self._batch_write_with_retry(requests)
        return len(requests)

    @handle_dynamodb_errors("get_receipt_item_type_counts")
    def get_receipt_item_type_counts(
        self, image_id: str, receipt_id: int
    ) -> dict[str, int]:
        """Count all entity types stored below a receipt key prefix.

        Embedding items live under the same RECEIPT# SK prefix (SPEC §3.1),
        so this prefix query now also walks ``RECEIPT_*_EMBEDDING`` rows.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        counts: Counter[str] = Counter()
        exclusive_start_key = None
        while True:
            params: dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :receipt_prefix)"
                ),
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":receipt_prefix": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
                "ProjectionExpression": "#type",
                "ExpressionAttributeNames": {"#type": "TYPE"},
            }
            if exclusive_start_key:
                params["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**params)
            for item in response.get("Items", []):
                counts[item.get("TYPE", {}).get("S", "UNKNOWN")] += 1
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break
        return dict(sorted(counts.items()))

    @handle_dynamodb_errors("release_receipt_id_reservations")
    def release_receipt_id_reservations(
        self,
        image_id: str,
        receipt_ids: list[int],
        operation_id: str,
    ) -> None:
        """Remove staged children and reservations owned by an operation.

        The guarded reservation delete runs FIRST: if any parent row is a
        committed receipt (the commit transaction actually landed, or a
        concurrent apply won), the whole release fails before any child
        rows are touched, so a rollback can never destroy committed data.
        """
        transact_items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": {
                        "PK": {"S": f"IMAGE#{image_id}"},
                        "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
                    },
                    "ConditionExpression": (
                        "attribute_not_exists(PK) OR "
                        "(#type = :reservation AND "
                        "operation_id = :operation_id)"
                    ),
                    "ExpressionAttributeNames": {"#type": "TYPE"},
                    "ExpressionAttributeValues": {
                        ":reservation": {"S": "RESEGMENT_RESERVATION"},
                        ":operation_id": {"S": operation_id},
                    },
                }
            }
            for receipt_id in receipt_ids
        ]
        if transact_items:
            self._client.transact_write_items(TransactItems=transact_items)
        for receipt_id in receipt_ids:
            self.delete_receipt_items(
                image_id, receipt_id, include_parent=False
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
        """Get a receipt with its details using optimized GSI4 query.

        This method uses GSI4 which is designed for efficient single-query
        retrieval of receipt details. By design, GSI4 excludes ReceiptLetters
        to reduce read costs - letters are rarely needed in most patterns.

        Args:
            image_id (str): The ID of the image the receipt belongs to
            receipt_id (int): The ID of the receipt to get

        Returns:
            ReceiptDetails: Dataclass with receipt and related data.
                Note: letters will be an empty list (excluded from GSI4).
        """

        # Query GSI4 for all receipt-related items (excluding letters)
        # GSI4PK: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}
        items, _ = self._query_entities(
            index_name="GSI4",
            key_condition_expression="GSI4PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"},
            },
            converter_func=self._convert_receipt_details_item,
            limit=None,  # Get all items
            last_evaluated_key=None,
        )

        return self._build_receipt_details(items, image_id, receipt_id)

    @handle_dynamodb_errors("get_receipt_details_for_lines")
    def get_receipt_details_for_lines(
        self,
        image_id: str,
        receipt_id: int,
        line_ids: list[int],
    ) -> ReceiptDetails:
        """Get receipt details restricted to selected receipt lines.

        The receipt header and place are always returned. Lines, words, and
        labels are returned only when their primary-table sort key belongs to
        one of ``line_ids``. DynamoDB still evaluates the GSI4 partition, but
        filtering server-side avoids transferring and deserializing hundreds
        of unrelated items for callers that need only a visual row.

        Args:
            image_id: The ID of the image the receipt belongs to.
            receipt_id: The ID of the receipt to get.
            line_ids: Non-empty list of receipt line IDs to include.

        Returns:
            ReceiptDetails containing the receipt, place, and focused line
            data. Letters and barcodes are excluded.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        if not isinstance(line_ids, list) or not line_ids:
            raise EntityValidationError("line_ids must be a non-empty list.")
        if not all(
            isinstance(line_id, int)
            and not isinstance(line_id, bool)
            and line_id >= 0
            for line_id in line_ids
        ):
            raise EntityValidationError(
                "line_ids must contain non-negative integers."
            )

        unique_line_ids = sorted(set(line_ids))
        line_filters = []
        expression_values = {
            ":pk": {"S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"},
            ":receipt": {"S": "RECEIPT"},
            ":place": {"S": "RECEIPT_PLACE"},
            ":line": {"S": "RECEIPT_LINE"},
            ":word": {"S": "RECEIPT_WORD"},
            ":label": {"S": "RECEIPT_WORD_LABEL"},
        }
        for index, line_id in enumerate(unique_line_ids):
            value_name = f":line_id_{index}"
            expression_values[value_name] = {"S": f"#LINE#{line_id:05d}"}
            line_filters.append(f"contains(#sk, {value_name})")

        filter_expression = (
            "#type IN (:receipt, :place) OR "
            "(#type IN (:line, :word, :label) AND ("
            + " OR ".join(line_filters)
            + "))"
        )
        items, _ = self._query_entities(
            index_name="GSI4",
            key_condition_expression="GSI4PK = :pk",
            expression_attribute_names={"#type": "TYPE", "#sk": "SK"},
            expression_attribute_values=expression_values,
            converter_func=self._convert_receipt_details_item,
            filter_expression=filter_expression,
            limit=None,
            last_evaluated_key=None,
        )

        return self._build_receipt_details(items, image_id, receipt_id)

    @handle_dynamodb_errors("list_receipts")
    def list_receipts(
        self,
        limit: int | None = None,
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
            image_id (str): The ID of the image to list receipts from

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
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> ReceiptBundlePage:
        """List receipts with their words and word labels using GSI2.

        This method queries the database for receipt-related items using GSI2
        (where GSI2PK = 'RECEIPT') and returns a page of receipt bundles.

        Note: With the addition of new entities using the same GSI2PK pattern
        (ReceiptLineItemAnalysis, ReceiptLabelAnalysis), this method now uses
        a filter expression to only retrieve the specific types needed.

        Args:
            limit: The maximum number of receipt bundles to return.
                   Defaults to None (return all).
            last_evaluated_key: The key to start the query from for
                               pagination. Defaults to None.

        Returns:
            ReceiptBundlePage: A page containing:
                - bundles: Dict mapping composite keys to ReceiptBundle
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
