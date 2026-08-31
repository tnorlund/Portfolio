"""Accessors for RECEIPT_WORD_EMBEDDING items. Writes are embedding-only."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_word_embedding import (
    WORD_EMBEDDING_TYPE,
    ReceiptWordEmbedding,
    item_to_receipt_word_embedding,
)

CHUNK_SIZE = 25


class _ReceiptWordEmbedding(FlattenedStandardMixin):
    """Get / list / idempotent batch-put for word embedding items."""

    @handle_dynamodb_errors("get_receipt_word_embedding")
    def get_receipt_word_embedding(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
    ) -> ReceiptWordEmbedding | None:
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}"
                        f"#LINE#{line_id:05d}"
                        f"#WORD#{word_id:05d}#EMBEDDING"
                    )
                },
            },
        )
        item = response.get("Item")
        if not item:
            return None
        return item_to_receipt_word_embedding(item)

    @handle_dynamodb_errors("list_receipt_word_embeddings_from_receipt")
    def list_receipt_word_embeddings_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptWordEmbedding]:
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)
        items: list[ReceiptWordEmbedding] = []
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
                    ":type": {"S": WORD_EMBEDDING_TYPE},
                },
            }
            if exclusive_start_key:
                params["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**params)
            for item in response.get("Items", []):
                items.append(item_to_receipt_word_embedding(item))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break
        return items

    @handle_dynamodb_errors("put_receipt_word_embeddings_idempotent")
    def put_receipt_word_embeddings_idempotent(
        self, embeddings: list[ReceiptWordEmbedding]
    ) -> dict[str, Any]:
        """Put items that do not already exist. Existing keys are skipped.

        Counts and key lists cover *this call's* arguments only — never
        table-wide embedding totals (the shared dev table may already
        hold other entrants' deterministic SKs).
        """
        if embeddings is None:
            raise EntityValidationError("embeddings cannot be None")
        written_keys: list[str] = []
        skipped_keys: list[str] = []
        pending: list[ReceiptWordEmbedding] = []
        for embedding in embeddings:
            existing = self.get_receipt_word_embedding(
                embedding.image_id,
                embedding.receipt_id,
                embedding.line_id,
                embedding.word_id,
            )
            if existing is not None:
                skipped_keys.append(embedding.harness_key())
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
            written_keys.extend(item.harness_key() for item in chunk)
        return {
            "written": len(written_keys),
            "skipped": len(skipped_keys),
            "written_keys": written_keys,
            "skipped_keys": skipped_keys,
        }
