from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_line_item import (
    ReceiptLineItem,
    item_to_receipt_line_item,
    slugify_merchant,
)

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptLineItem(FlattenedStandardMixin):
    """
    Methods to interact with "ReceiptLineItem" entities in DynamoDB — one
    row per extracted line item, per receipt, written by the deterministic
    geometric extractor.

    Rewrites are delete-then-put per receipt
    (``delete_receipt_line_items_for_receipt`` + ``add_receipt_line_items``),
    mirroring ``persist_receipt_rows``: re-running extraction on unchanged
    input produces byte-identical rows, so duplicate deliveries are
    harmless.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).
    """

    @handle_dynamodb_errors("add_receipt_line_item")
    def add_receipt_line_item(self, line_item: ReceiptLineItem) -> None:
        """Adds a single ReceiptLineItem (fails if it already exists)."""
        self._validate_entity(line_item, ReceiptLineItem, "line_item")
        self._add_entity(
            line_item, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipt_line_items")
    def add_receipt_line_items(
        self, line_items: list[ReceiptLineItem]
    ) -> None:
        """Adds multiple ReceiptLineItems in batches (no condition —
        intended for the delete-then-put rewrite path only)."""
        self._validate_entity_list(
            line_items, ReceiptLineItem, "line_items"
        )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=li.to_item())
            )
            for li in line_items
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_line_item")
    def get_receipt_line_item(
        self, image_id: str, receipt_id: int, item_index: int
    ) -> ReceiptLineItem:
        """Retrieves a single ReceiptLineItem by IDs."""
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE_ITEM#{item_index:05d}"
            ),
            entity_class=ReceiptLineItem,
            converter_func=item_to_receipt_line_item,
        )
        if result is None:
            raise EntityNotFoundError(
                f"ReceiptLineItem with image_id {image_id}, receipt_id "
                f"{receipt_id}, item_index {item_index} not found"
            )
        return result

    @handle_dynamodb_errors("get_receipt_line_items_from_receipt")
    def get_receipt_line_items_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptLineItem]:
        """Retrieves all ReceiptLineItems for a receipt, in index order."""
        if image_id is None:
            raise EntityValidationError("image_id is required")
        if receipt_id is None:
            raise EntityValidationError("receipt_id is required")
        try:
            items: list[ReceiptLineItem] = []
            last_evaluated_key = None
            while True:
                query_kwargs = {
                    "TableName": self.table_name,
                    "KeyConditionExpression": (
                        "PK = :pk and begins_with(SK, :sk)"
                    ),
                    "ExpressionAttributeValues": {
                        ":pk": {"S": f"IMAGE#{image_id}"},
                        ":sk": {
                            "S": f"RECEIPT#{receipt_id:05d}#LINE_ITEM#"
                        },
                    },
                    # Strongly consistent: callers verify a rewrite
                    # immediately after delete-then-put.
                    "ConsistentRead": True,
                }
                if last_evaluated_key is not None:
                    query_kwargs["ExclusiveStartKey"] = last_evaluated_key
                response = self._client.query(**query_kwargs)
                items.extend(
                    item_to_receipt_line_item(item)
                    for item in response["Items"]
                )
                last_evaluated_key = response.get("LastEvaluatedKey")
                if last_evaluated_key is None:
                    return items
        except ClientError as e:
            raise EntityValidationError(
                f"Could not get ReceiptLineItems from DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("delete_receipt_line_items_for_receipt")
    def delete_receipt_line_items_for_receipt(
        self, image_id: str, receipt_id: int
    ) -> int:
        """Deletes every ReceiptLineItem of a receipt; returns the count.

        First half of the idempotent delete-then-put rewrite.
        """
        existing = self.get_receipt_line_items_from_receipt(
            image_id, receipt_id
        )
        if not existing:
            return 0
        try:
            for i in range(0, len(existing), CHUNK_SIZE):
                chunk = existing[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=li.key)
                    )
                    for li in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise EntityValidationError(
                "Could not delete ReceiptLineItems from the database"
            ) from e
        return len(existing)

    @handle_dynamodb_errors("list_receipt_line_items_by_merchant")
    def list_receipt_line_items_by_merchant(
        self,
        merchant_name: str,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLineItem], dict | None]:
        """Every observation of every product at a merchant, via GSI1.

        Rows with ``name_quality == "low"`` omit the GSI keys, so this
        query never returns junk-named items.
        """
        if not merchant_name:
            raise EntityValidationError("merchant_name is required")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )
        try:
            query_kwargs = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": (
                    "GSI1PK = :pk AND begins_with(GSI1SK, :sk)"
                ),
                "ExpressionAttributeValues": {
                    ":pk": {
                        "S": f"MERCHANT#{slugify_merchant(merchant_name)}"
                    },
                    ":sk": {"S": "LINE_ITEM#"},
                },
            }
            if limit is not None:
                query_kwargs["Limit"] = limit
            if last_evaluated_key is not None:
                query_kwargs["ExclusiveStartKey"] = last_evaluated_key
            response = self._client.query(**query_kwargs)
            items = [
                item_to_receipt_line_item(item)
                for item in response["Items"]
            ]
            return items, response.get("LastEvaluatedKey")
        except ClientError as e:
            raise EntityValidationError(
                f"Could not query ReceiptLineItems by merchant: {e}"
            ) from e

    @handle_dynamodb_errors("list_receipt_line_items")
    def list_receipt_line_items(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLineItem], dict | None]:
        """Returns all ReceiptLineItems with optional pagination."""
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )
        return self._query_by_type(
            entity_type="RECEIPT_LINE_ITEM",
            converter_func=item_to_receipt_line_item,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
