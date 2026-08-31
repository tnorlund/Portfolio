"""Accessors for RECEIPT_LINE_EMBEDDING items."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.embedding_codec import line_embedding_sk
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
    item_to_receipt_line_embedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
)

_EMBEDDING_TYPES = (
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)
EmbeddingItem = ReceiptLineEmbedding | ReceiptWordEmbedding


@dataclass
class EmbeddingWriteReport:
    """Outcome of an idempotent embedding BatchWriteItem run.

    Counts and key lists cover only the entities passed into this call —
    never a table-wide scan. Existing keys (prior run or another
    entrant's deterministic SK) are skipped and listed in
    ``skipped_keys``.
    """

    written: int = 0
    skipped_existing: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    written_keys: list[str] = field(default_factory=list)
    skipped_keys: list[str] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.written + self.skipped_existing + len(self.failed)


class _ReceiptLineEmbedding(FlattenedStandardMixin):
    """CRUD for dedicated line embedding items."""

    @handle_dynamodb_errors("get_receipt_line_embedding")
    def get_receipt_line_embedding(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
    ) -> ReceiptLineEmbedding | None:
        """Return one line embedding, or None if it is absent."""

        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        _require_non_negative_int(line_id, "line_id")
        return self._get_entity(
            f"IMAGE#{image_id}",
            line_embedding_sk(receipt_id, line_id),
            ReceiptLineEmbedding,
            item_to_receipt_line_embedding,
        )

    @handle_dynamodb_errors("list_receipt_line_embeddings_from_receipt")
    def list_receipt_line_embeddings_from_receipt(
        self,
        image_id: str,
        receipt_id: int,
    ) -> list[ReceiptLineEmbedding]:
        """List line embeddings under one receipt's SK prefix."""

        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        items, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
                ":type": {"S": "RECEIPT_LINE_EMBEDDING"},
            },
            converter_func=item_to_receipt_line_embedding,
            limit=None,
            last_evaluated_key=None,
            filter_expression="#t = :type",
        )
        return items

    @handle_dynamodb_errors("put_embedding_items_idempotent")
    def put_embedding_items_idempotent(
        self, entities: Sequence[EmbeddingItem]
    ) -> EmbeddingWriteReport:
        """Batch-write embedding items, skipping keys that already exist.

        Per-item failures are recorded and skipped; the rest of the batch
        continues. Re-running against the same keys writes nothing.
        """

        report = EmbeddingWriteReport()
        if entities is None:
            raise EntityValidationError("entities cannot be None")
        valid: list[EmbeddingItem] = []
        for index, entity in enumerate(entities):
            if not isinstance(entity, _EMBEDDING_TYPES):
                report.failed.append(
                    {
                        "key": f"index:{index}",
                        "reason": (
                            "not an embedding entity: "
                            f"{type(entity).__name__}"
                        ),
                    }
                )
                continue
            sort_key = entity.key["SK"]["S"]
            if not sort_key.endswith("#EMBEDDING"):
                report.failed.append(
                    {
                        "key": sort_key,
                        "reason": "refusing non-embedding sort key",
                    }
                )
                continue
            try:
                entity.to_item()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                report.failed.append({"key": sort_key, "reason": str(exc)})
                continue
            valid.append(entity)

        existing = _existing_embedding_keys(
            self._client, self.table_name, [entity.key for entity in valid]
        )
        to_write: list[EmbeddingItem] = []
        for entity in valid:
            key = (entity.key["PK"]["S"], entity.key["SK"]["S"])
            if key in existing:
                report.skipped_existing += 1
                report.skipped_keys.append(entity.vector_search_key)
            else:
                to_write.append(entity)

        remaining = [
            {"PutRequest": {"Item": entity.to_item()}} for entity in to_write
        ]
        written_keys = {
            (entity.key["PK"]["S"], entity.key["SK"]["S"])
            for entity in to_write
        }
        attempts = 0
        max_attempts = max(8, ((len(to_write) + 24) // 25) * 2)
        while remaining and attempts < max_attempts:
            batch = remaining[:25]
            remaining = remaining[25:]
            try:
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: batch}
                )
            except ClientError as exc:
                for request in batch:
                    item = request["PutRequest"]["Item"]
                    report.failed.append(
                        {
                            "key": item["SK"]["S"],
                            "reason": exc.response["Error"].get(
                                "Message", str(exc)
                            ),
                        }
                    )
                    written_keys.discard((item["PK"]["S"], item["SK"]["S"]))
                attempts += 1
                continue
            unprocessed = (response.get("UnprocessedItems") or {}).get(
                self.table_name, []
            )
            remaining.extend(unprocessed)
            attempts += 1

        for request in remaining:
            item = request["PutRequest"]["Item"]
            report.failed.append(
                {
                    "key": item["SK"]["S"],
                    "reason": "unprocessed after retries",
                }
            )
            written_keys.discard((item["PK"]["S"], item["SK"]["S"]))
        report.written_keys = [
            entity.vector_search_key
            for entity in to_write
            if (entity.key["PK"]["S"], entity.key["SK"]["S"]) in written_keys
        ]
        report.written = len(report.written_keys)
        return report


def _require_non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EntityValidationError(f"{name} must be an integer")
    if value < 0:
        raise EntityValidationError(f"{name} must be non-negative")


def _existing_embedding_keys(
    client: Any, table_name: str, keys: Sequence[dict[str, Any]]
) -> set[tuple[str, str]]:
    """BatchGet only the keys this call attempted — never a table scan."""

    found: set[tuple[str, str]] = set()
    remaining = list(keys)
    attempts = 0
    while remaining and attempts < 16:
        chunk = remaining[:100]
        remaining = remaining[100:]
        response = client.batch_get_item(
            RequestItems={
                table_name: {
                    "Keys": list(chunk),
                    "ProjectionExpression": "PK, SK",
                }
            }
        )
        for item in response.get("Responses", {}).get(table_name, []):
            found.add((item["PK"]["S"], item["SK"]["S"]))
        unprocessed = (
            response.get("UnprocessedKeys", {})
            .get(table_name, {})
            .get("Keys", [])
        )
        remaining.extend(unprocessed)
        attempts += 1
    return found
