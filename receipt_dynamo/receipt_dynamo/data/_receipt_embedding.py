"""House-style accessors for dedicated receipt embedding items."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_embedding import (
    ReceiptEmbedding,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
    item_to_receipt_embedding,
    item_to_receipt_line_embedding,
    item_to_receipt_word_embedding,
)


class _ReceiptEmbedding(FlattenedStandardMixin):
    """Read and conditionally add line/word embedding items."""

    @handle_dynamodb_errors("add_receipt_embedding")
    def add_receipt_embedding(self, embedding: ReceiptEmbedding) -> None:
        if not isinstance(
            embedding, (ReceiptLineEmbedding, ReceiptWordEmbedding)
        ):
            raise ValueError(
                "embedding must be a ReceiptLineEmbedding or "
                "ReceiptWordEmbedding"
            )
        self._add_entity(
            embedding, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("get_receipt_line_embedding")
    def get_receipt_line_embedding(
        self, image_id: str, receipt_id: int, line_id: int
    ) -> ReceiptLineEmbedding:
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#EMBEDDING"
            ),
            entity_class=ReceiptLineEmbedding,
            converter_func=item_to_receipt_line_embedding,
        )
        if result is None:
            raise EntityNotFoundError(
                "receipt line embedding does not exist for "
                f"image_id={image_id}, receipt_id={receipt_id}, line_id={line_id}"
            )
        return result  # type: ignore[no-any-return]

    @handle_dynamodb_errors("get_receipt_word_embedding")
    def get_receipt_word_embedding(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
    ) -> ReceiptWordEmbedding:
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#"
                f"WORD#{word_id:05d}#EMBEDDING"
            ),
            entity_class=ReceiptWordEmbedding,
            converter_func=item_to_receipt_word_embedding,
        )
        if result is None:
            raise EntityNotFoundError(
                "receipt word embedding does not exist for "
                f"image_id={image_id}, receipt_id={receipt_id}, "
                f"line_id={line_id}, word_id={word_id}"
            )
        return result  # type: ignore[no-any-return]

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
