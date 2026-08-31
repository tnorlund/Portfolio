"""Accessors for RECEIPT_WORD_EMBEDDING items."""

from __future__ import annotations

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.embedding_codec import word_embedding_sk
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
    item_to_receipt_word_embedding,
)


class _ReceiptWordEmbedding(FlattenedStandardMixin):
    """CRUD for dedicated word embedding items."""

    @handle_dynamodb_errors("get_receipt_word_embedding")
    def get_receipt_word_embedding(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
    ) -> ReceiptWordEmbedding | None:
        """Return one word embedding, or None if it is absent."""

        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        _require_non_negative_int(line_id, "line_id")
        _require_non_negative_int(word_id, "word_id")
        return self._get_entity(
            f"IMAGE#{image_id}",
            word_embedding_sk(receipt_id, line_id, word_id),
            ReceiptWordEmbedding,
            item_to_receipt_word_embedding,
        )

    @handle_dynamodb_errors("list_receipt_word_embeddings_from_receipt")
    def list_receipt_word_embeddings_from_receipt(
        self,
        image_id: str,
        receipt_id: int,
    ) -> list[ReceiptWordEmbedding]:
        """List word embeddings under one receipt's SK prefix."""

        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        items, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
                ":type": {"S": "RECEIPT_WORD_EMBEDDING"},
            },
            converter_func=item_to_receipt_word_embedding,
            limit=None,
            last_evaluated_key=None,
            filter_expression="#t = :type",
        )
        return items


def _require_non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EntityValidationError(f"{name} must be an integer")
    if value < 0:
        raise EntityValidationError(f"{name} must be non-negative")
