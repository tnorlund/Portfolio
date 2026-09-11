"""House-style accessors for dedicated receipt embedding items."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
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

    @handle_dynamodb_errors("add_receipt_embeddings")
    def add_receipt_embeddings(
        self, receipt_embeddings: list[ReceiptEmbedding]
    ) -> None:
        """
        Adds multiple receipt embedding items to DynamoDB in batches.

        Embedding items are copied verbatim between environments rather
        than regenerated: OpenAI embeddings are not bit-stable across
        calls or model revisions, so copying is the only way a receipt
        shared by dev and prod behaves identically in both
        (docs/chroma-removal/SPEC.md §3.1).

        Parameters
        ----------
        receipt_embeddings : list[ReceiptEmbedding]
            The line and/or word embedding items to add.

        Raises
        ------
        ValueError
            If receipt_embeddings is invalid.
        """
        if receipt_embeddings is None:
            raise EntityValidationError("receipt_embeddings cannot be None")
        if not isinstance(receipt_embeddings, list):
            raise EntityValidationError(
                "receipt_embeddings must be a list of ReceiptEmbedding items"
            )
        for embedding in receipt_embeddings:
            if not isinstance(
                embedding, (ReceiptLineEmbedding, ReceiptWordEmbedding)
            ):
                raise EntityValidationError(
                    "receipt_embeddings must be a list of ReceiptEmbedding "
                    "items"
                )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=embedding.to_item())
            )
            for embedding in receipt_embeddings
        ]
        self._batch_write_with_retry(request_items)


__all__ = ["_ReceiptEmbedding"]
