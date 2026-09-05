"""House-style accessors for dedicated receipt embedding items."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.entities.receipt_embedding import (
    ReceiptEmbedding,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
    item_to_receipt_embedding,
)


class _ReceiptEmbedding(FlattenedStandardMixin):
    """Read receipt embedding items."""

    @handle_dynamodb_errors("get_receipt_embeddings")
    def get_receipt_embeddings(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptEmbedding]:
        values: dict[str, Any] = {
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
            ":line": {"S": ReceiptLineEmbedding.TYPE},
            ":word": {"S": ReceiptWordEmbedding.TYPE},
        }
        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values=values,
            converter_func=item_to_receipt_embedding,
            filter_expression="#type IN (:line, :word)",
            limit=None,
            last_evaluated_key=None,
        )
        return results


__all__ = ["_ReceiptEmbedding"]
