"""Data operations for ReceiptFactOverride rows (owner-stated facts).

One row per receipt. Creation is add-only, updates and deletes are
compare-and-swap on ``revision`` so two editors cannot silently clobber
each other's statement of fact.
"""

from __future__ import annotations

from dataclasses import replace

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,
    FactOverrideConflictError,
)
from receipt_dynamo.entities.receipt_fact_override import (
    ReceiptFactOverride,
    check_fact_revision,
    item_to_receipt_fact_override,
    receipt_fact_override_key,
)

_REVISION_CONDITION = "attribute_exists(PK) AND #revision = :expected"

# Table-name markers that every fact-override WRITE refuses. Owner facts
# are stated on the dev table; the guard makes a mis-configured client
# harmless for direct DynamoClient callers as well as the MCP tools.
PROTECTED_FACT_TABLE_MARKERS: tuple[str, ...] = ("d7ff76a",)


def _raise_fact_conflict(error: ClientError, what: str) -> None:
    """Map a failed revision condition to FactOverrideConflictError."""
    if (
        error.response.get("Error", {}).get("Code")
        == "ConditionalCheckFailedException"
    ):
        raise FactOverrideConflictError(
            f"{what}: the override is missing or its revision changed; "
            "read it again and retry with the current revision"
        ) from error


class _ReceiptFactOverride(FlattenedStandardMixin):
    """CRUD for ReceiptFactOverride with revision compare-and-swap."""

    def _assert_fact_override_writable(self) -> None:
        """Refuse fact-override writes to a protected table."""
        table_name = str(getattr(self, "table_name", "") or "")
        if any(
            marker in table_name for marker in PROTECTED_FACT_TABLE_MARKERS
        ):
            raise EntityValidationError(
                "fact override writes are refused on the configured table: "
                "owner facts are stated on the dev table only"
            )

    def _validated_override(
        self, override: ReceiptFactOverride
    ) -> ReceiptFactOverride:
        """Re-run the entity's validation right before it is written.

        Dataclass fields are mutable, so a caller can assign a fact after
        construction without its reference; replace() re-runs
        __post_init__ so such an entity is refused instead of stored.
        """
        self._validate_entity(override, ReceiptFactOverride, "override")
        return replace(override)

    @handle_dynamodb_errors("add_receipt_fact_override")
    def add_receipt_fact_override(
        self, override: ReceiptFactOverride
    ) -> ReceiptFactOverride:
        """Create the override row for a receipt (revision must be 1).

        Raises:
            EntityAlreadyExistsError: an override already exists.
        """
        self._assert_fact_override_writable()
        override = self._validated_override(override)
        if override.revision != 1:
            raise EntityValidationError(
                "a new override must start at revision 1"
            )
        self._add_entity(
            override,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )
        return override

    @handle_dynamodb_errors("update_receipt_fact_override")
    def update_receipt_fact_override(
        self, override: ReceiptFactOverride, *, expected_revision: int
    ) -> ReceiptFactOverride:
        """Replace the override only if the stored revision still matches.

        ``override.revision`` must be ``expected_revision + 1``.

        Raises:
            FactOverrideConflictError: the row is missing or its revision
                no longer equals ``expected_revision``.
        """
        self._assert_fact_override_writable()
        override = self._validated_override(override)
        check_fact_revision(expected_revision)
        if override.revision != expected_revision + 1:
            raise EntityValidationError(
                "override revision must be expected_revision + 1"
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=override.to_item(),
                ConditionExpression=_REVISION_CONDITION,
                ExpressionAttributeNames={"#revision": "revision"},
                ExpressionAttributeValues={
                    ":expected": {"N": str(expected_revision)}
                },
            )
        except ClientError as error:
            _raise_fact_conflict(error, "update_receipt_fact_override")
            raise
        return override

    @handle_dynamodb_errors("get_receipt_fact_override")
    def get_receipt_fact_override(
        self, image_id: str, receipt_id: int
    ) -> ReceiptFactOverride | None:
        """Return the receipt's override, or None when the owner stated none.

        Reads consistently so a compare-and-swap caller sees the revision
        it must present.
        """
        response = self._client.get_item(
            TableName=self.table_name,
            Key=receipt_fact_override_key(image_id, receipt_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item_to_receipt_fact_override(item) if item else None

    @handle_dynamodb_errors("delete_receipt_fact_override")
    def delete_receipt_fact_override(
        self, image_id: str, receipt_id: int, *, expected_revision: int
    ) -> None:
        """Delete the override only if the stored revision still matches.

        Maintenance only: the editing tools retract facts by updating the
        row instead, because deleting lets a recreated override restart at
        revision 1 and makes a stale expected_revision valid again.

        Raises:
            FactOverrideConflictError: the row is missing or its revision
                no longer equals ``expected_revision``.
        """
        self._assert_fact_override_writable()
        key = receipt_fact_override_key(image_id, receipt_id)
        check_fact_revision(expected_revision)
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=key,
                ConditionExpression=_REVISION_CONDITION,
                ExpressionAttributeNames={"#revision": "revision"},
                ExpressionAttributeValues={
                    ":expected": {"N": str(expected_revision)}
                },
            )
        except ClientError as error:
            _raise_fact_conflict(error, "delete_receipt_fact_override")
            raise
