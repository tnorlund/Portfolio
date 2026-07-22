"""Append-only DynamoDB access for MERCHANT_TRUTH_GATE records.

Gate records are per-run eval evidence about a merchant-truth bundle (contract
section 7.6). They share the merchant partition with the truth records but are
a separate record class with their own ``TYPE``, so this accessor is kept
distinct from ``_MerchantTruth`` (which owns the immutable bundle write
contract) rather than folded into it.

Discipline mirrors the truth components: create-only conditional writes, no
update/delete accessor, and the same exact-table env guard on every write.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import FlattenedStandardMixin
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import merchant_truth_pk
from receipt_dynamo.entities.merchant_truth_gate import MerchantTruthGateRecord

CREATE_ONLY = "attribute_not_exists(PK) AND attribute_not_exists(SK)"


class _MerchantTruthGate(FlattenedStandardMixin):
    """Accessor for the append-only MERCHANT_TRUTH_GATE record class."""

    def _assert_gate_table(self, expected_table_name: str) -> None:
        if not expected_table_name or self.table_name != expected_table_name:
            raise MerchantTruthTableMismatchError(
                f"refusing merchant-truth-gate write to {self.table_name!r}; "
                f"expected exact table {expected_table_name!r}"
            )

    def add_gate_record(
        self,
        record: MerchantTruthGateRecord,
        expected_table_name: str,
    ) -> MerchantTruthGateRecord:
        """Append one gate record via a create-only conditional write.

        The condition ``attribute_not_exists(PK) AND attribute_not_exists(SK)``
        makes the write append-only: an identical ``(slug, run_at, version)``
        SK is a conflict, never a silent overwrite -- same discipline as the
        truth components.
        """
        self._assert_gate_table(expected_table_name)
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=record.to_item(),
                ConditionExpression=CREATE_ONLY,
            )
        except ClientError as error:
            if (
                error.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise MerchantTruthConflictError(
                    "gate record already exists for "
                    f"{record.slug} {record.run_at} v{record.version}"
                ) from error
            raise
        return record

    def list_gate_records(
        self,
        slug: str,
        *,
        ascending: bool = True,
        consistent_read: bool = False,
    ) -> list[MerchantTruthGateRecord]:
        """List a merchant's gate records ordered by SK (run_at then version).

        The SK ``GATE#{run_at}#v{n:010d}`` sorts chronologically; ``ascending``
        follows ``ScanIndexForward`` so callers can read oldest- or
        newest-first.
        """
        items = self._paginate_gate_query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": merchant_truth_pk(slug)},
                ":sk": {"S": "GATE#"},
            },
            ScanIndexForward=ascending,
            ConsistentRead=consistent_read,
        )
        return [MerchantTruthGateRecord.from_item(item) for item in items]

    def list_all_gate_records(self) -> list[MerchantTruthGateRecord]:
        """Enumerate every gate record fleet-wide through GSITYPE.

        One query on ``TYPE = MERCHANT_TRUTH_GATE`` (the one-TYPE-per-record
        rule), so gate-history enumeration never drags other record classes.
        """
        items = self._paginate_gate_query(
            TableName=self.table_name,
            IndexName="GSITYPE",
            KeyConditionExpression="#type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={":type": {"S": "MERCHANT_TRUTH_GATE"}},
        )
        return [MerchantTruthGateRecord.from_item(item) for item in items]

    def _paginate_gate_query(self, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            request = dict(params)
            if exclusive_start_key is not None:
                request["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**request)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return items
