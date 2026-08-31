"""Accessors for RECEIPT_LINE_EMBEDDING items. Writes are embedding-only."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_line_embedding import (
    LINE_EMBEDDING_TYPE,
    ReceiptLineEmbedding,
    item_to_receipt_line_embedding,
)

CHUNK_SIZE = 25


class _ReceiptLineEmbedding(FlattenedStandardMixin):
    """Get / list / idempotent batch-put for line embedding items."""

    @handle_dynamodb_errors("get_receipt_line_embedding")
    def get_receipt_line_embedding(
        self, image_id: str, receipt_id: int, line_id: int
    ) -> ReceiptLineEmbedding | None:
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        if isinstance(line_id, bool) or not isinstance(line_id, int):
            raise EntityValidationError("line_id must be an integer")
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}"
                        f"#LINE#{line_id:05d}#EMBEDDING"
                    )
                },
            },
        )
        item = response.get("Item")
        if not item:
            return None
        return item_to_receipt_line_embedding(item)

    @handle_dynamodb_errors("list_receipt_line_embeddings_from_receipt")
    def list_receipt_line_embeddings_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptLineEmbedding]:
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        items: list[ReceiptLineEmbedding] = []
        exclusive_start_key = None
        while True:
            params: dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :prefix)"
                ),
                "FilterExpression": "#type = :type",
                "ExpressionAttributeNames": {"#type": "TYPE"},
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":prefix": {"S": f"RECEIPT#{receipt_id:05d}#"},
                    ":type": {"S": LINE_EMBEDDING_TYPE},
                },
            }
            if exclusive_start_key:
                params["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**params)
            for item in response.get("Items", []):
                items.append(item_to_receipt_line_embedding(item))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break
        return items

    @handle_dynamodb_errors("put_receipt_line_embeddings_idempotent")
    def put_receipt_line_embeddings_idempotent(
        self, embeddings: list[ReceiptLineEmbedding]
    ) -> dict[str, int]:
        """Put items that do not already exist. Existing keys are skipped."""
        if embeddings is None:
            raise EntityValidationError("embeddings cannot be None")
        written = 0
        skipped = 0
        pending: list[ReceiptLineEmbedding] = []
        for embedding in embeddings:
            existing = self.get_receipt_line_embedding(
                embedding.image_id, embedding.receipt_id, embedding.line_id
            )
            if existing is not None:
                skipped += 1
                continue
            pending.append(embedding)
        for offset in range(0, len(pending), CHUNK_SIZE):
            chunk = pending[offset : offset + CHUNK_SIZE]
            requests = [
                WriteRequestTypeDef(
                    PutRequest=PutRequestTypeDef(Item=item.to_item())
                )
                for item in chunk
            ]
            self._batch_write_with_retry(requests)
            written += len(chunk)
        return {"written": written, "skipped": skipped}
