"""One conditional document, with read-time source and context validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

from receipt_dynamo.data._nutrition_catalog import _NutritionCatalog
from receipt_dynamo.data.base_operations.error_handling import (
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
    NutritionConflictError,
)
from receipt_dynamo.entities.nutrition_support import (
    check_nutrition_hash,
    nutrition_hash,
    nutrition_json,
)
from receipt_dynamo.entities.receipt_nutrition import (
    NutritionInput,
    ReceiptNutritionSnapshot,
    item_to_receipt_nutrition_snapshot,
    nutrition_receipt_key,
    nutrition_summary_key,
)


class _ReceiptNutrition(_NutritionCatalog):
    """Not stream-connected; callers must supply current explicit context."""

    def _nutrition_get(self, key: dict[str, Any]) -> dict[str, Any] | None:
        return self._client.get_item(
            TableName=self.table_name, Key=key, ConsistentRead=True
        ).get("Item")

    def _nutrition_source_once(
        self, image_id: str, receipt_id: int
    ) -> NutritionInput | None:
        key = nutrition_receipt_key(image_id, receipt_id)
        parent = self._nutrition_get(key)
        if parent is None:
            return None
        args: dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": {
                ":pk": key["PK"],
                ":sk": {"S": key["SK"]["S"] + "#LINE_ITEM#"},
            },
            "ConsistentRead": True,
        }
        lines = []
        while True:
            page = self._client.query(**args)
            lines.extend(page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                break
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return NutritionInput(
            image_id,
            receipt_id,
            nutrition_json({"parent": parent, "lines": lines}, limit=None),
        )

    @handle_dynamodb_errors("get_nutrition_input")
    def get_nutrition_input(
        self, image_id: str, receipt_id: int
    ) -> NutritionInput | None:
        """Two equal observations detect churn; not source-transaction proof."""
        before = self._nutrition_source_once(image_id, receipt_id)
        after = self._nutrition_source_once(image_id, receipt_id)
        if before != after:
            raise NutritionConflictError(
                "receipt source changed while reading"
            )
        return after

    @handle_dynamodb_errors("save_receipt_nutrition")
    def save_receipt_nutrition(
        self,
        source: NutritionInput,
        *,
        context: dict[str, Any],
        payload_json: str,
        expected_revision: str | None,
        expected_table_name: str,
    ) -> ReceiptNutritionSnapshot:
        """Atomically replace rows and totals; reject stale source/writer."""
        self._assert_nutrition_table(expected_table_name)
        if not isinstance(source, NutritionInput):
            raise EntityValidationError(
                "nutrition source observation required"
            )
        context_hash = nutrition_hash(context)
        record = ReceiptNutritionSnapshot(
            source.image_id,
            source.receipt_id,
            nutrition_hash(uuid4().hex),
            source.fingerprint,
            context_hash,
            payload_json,
        )
        item = record.to_item()  # Validate both payload and size before I/O.
        if expected_revision is not None:
            check_nutrition_hash(expected_revision)
        current = self.get_nutrition_input(source.image_id, source.receipt_id)
        if current is None:
            raise EntityNotFoundError("receipt parent is absent")
        if current != source:
            raise NutritionConflictError("receipt source changed before save")
        existing = self._nutrition_get(
            nutrition_summary_key(source.image_id, source.receipt_id)
        )
        # Identical retry after a lost response is a success. A changed fact,
        # override or source must take the normal compare-and-swap path.
        if existing:
            previous = item_to_receipt_nutrition_snapshot(
                source.image_id, source.receipt_id, existing
            )
            if (
                previous.source_fingerprint == record.source_fingerprint
                and previous.context_hash == context_hash
                and previous.payload_json == item["payload_json"]["S"]
            ):
                return previous
        put: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": "attribute_not_exists(PK)",
        }
        if expected_revision is not None:
            put.update(
                ConditionExpression="revision = :expected",
                ExpressionAttributeValues={
                    ":expected": {"S": expected_revision}
                },
            )
        self._nutrition_transact(
            [
                {
                    "ConditionCheck": {
                        "TableName": self.table_name,
                        "Key": nutrition_receipt_key(
                            source.image_id, source.receipt_id
                        ),
                        "ConditionExpression": "timestamp_added = :timestamp",
                        "ExpressionAttributeValues": {
                            ":timestamp": source.parent["timestamp_added"]
                        },
                    }
                },
                {"Put": put},
            ]
        )
        return record

    @handle_dynamodb_errors("get_receipt_nutrition")
    def get_receipt_nutrition(
        self, image_id: str, receipt_id: int, *, context: dict[str, Any]
    ) -> ReceiptNutritionSnapshot | None:
        """Never infer freshness from a stream counter or stored row count."""
        before = self.get_nutrition_input(image_id, receipt_id)
        if before is None:
            return None
        item = self._nutrition_get(nutrition_summary_key(image_id, receipt_id))
        if item is None:
            return None
        record = item_to_receipt_nutrition_snapshot(image_id, receipt_id, item)
        after = self.get_nutrition_input(image_id, receipt_id)
        if after is None:
            return None
        return replace(
            record,
            stale=(
                before != after
                or record.source_fingerprint != after.fingerprint
                or record.context_hash != nutrition_hash(context)
            ),
        )
