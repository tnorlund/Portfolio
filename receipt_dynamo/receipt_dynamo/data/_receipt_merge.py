"""Conditional merge claims, output reservations, and recovery checkpoints."""

import time
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_merge import (
    ReceiptMerge,
    item_to_receipt_merge,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import ConditionCheckTypeDef

# The deployed Lambda has a hard 600-second execution timeout. A dead
# invocation cannot overlap a replacement once this lease has expired.
MERGE_LEASE_SECONDS = 900
_OWNED = "#owner = :owner AND lease_until > :now AND #status = :status"


def _conditional_conflict(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code")
    if code == "ConditionalCheckFailedException":
        return True
    return code == "TransactionCanceledException" and any(
        reason.get("Code") == "ConditionalCheckFailed"
        for reason in error.response.get("CancellationReasons", [])
    )


class _ReceiptMerge(FlattenedStandardMixin):
    """Keep merge retries on one output, including after source deletion."""

    def _merge_template(
        self, image_id: str, source_ids: list[int], owner: str
    ) -> ReceiptMerge:
        self._validate_image_id(image_id)
        if (
            not isinstance(source_ids, list)
            or len(source_ids) != 2
            or any(
                not isinstance(rid, int) or isinstance(rid, bool) or rid < 1
                for rid in source_ids
            )
            or source_ids[0] == source_ids[1]
        ):
            raise EntityValidationError("merge requires two distinct IDs")
        if not isinstance(owner, str) or not owner:
            raise EntityValidationError("merge owner must be nonempty")
        return ReceiptMerge(
            image_id,
            (source_ids[0], source_ids[1]),
            max(source_ids) + 1,
            owner,
            int(time.time()) + MERGE_LEASE_SECONDS,
        )

    @handle_dynamodb_errors("get_receipt_merge")
    def get_receipt_merge(
        self, image_id: str, source_ids: list[int]
    ) -> ReceiptMerge | None:
        """Read even COMPLETED operations after both sources are gone."""
        template = self._merge_template(image_id, source_ids, "read")
        item = self._client.get_item(
            TableName=self.table_name,
            Key=template.key,
            ConsistentRead=True,
        ).get("Item")
        return item_to_receipt_merge(item) if item else None

    def _next_merge_output_id(self, image_id: str) -> int:
        maximum = 0
        for prefix in ("RECEIPT#", "MERGE_OUTPUT#"):
            pages = self._client.get_paginator("query").paginate(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": prefix},
                },
                ProjectionExpression="SK",
                ConsistentRead=True,
            )
            for page in pages:
                for item in page.get("Items", []):
                    maximum = max(maximum, int(item["SK"]["S"].split("#")[1]))
        return maximum + 1

    def _image_merge_key(self, merge: ReceiptMerge) -> dict[str, Any]:
        return {"PK": merge.key["PK"], "SK": {"S": "MERGE_LOCK"}}

    def _image_merge_claim(self, merge: ReceiptMerge) -> dict[str, Any]:
        return {
            "TableName": self.table_name,
            "Item": {
                **self._image_merge_key(merge),
                "TYPE": {"S": "RECEIPT_MERGE_LOCK"},
                "owner": {"S": merge.owner},
                "operation": merge.key["SK"],
                "lease_until": {"N": str(merge.lease_until)},
            },
            "ConditionExpression": (
                "attribute_not_exists(PK) OR lease_until <= :now"
            ),
            "ExpressionAttributeValues": {
                ":now": {"N": str(int(time.time()))}
            },
        }

    def _image_merge_owner_condition(
        self, merge: ReceiptMerge
    ) -> "ConditionCheckTypeDef":
        return {
            "TableName": self.table_name,
            "Key": self._image_merge_key(merge),
            "ConditionExpression": "#owner = :owner AND lease_until > :now",
            "ExpressionAttributeNames": {"#owner": "owner"},
            "ExpressionAttributeValues": {
                ":owner": {"S": merge.owner},
                ":now": {"N": str(int(time.time()))},
            },
        }

    def _reserve_merge(self, merge: ReceiptMerge) -> None:
        transaction: list[Any] = [
            {"Put": self._image_merge_claim(merge)},
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": merge.to_item(),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
        ]
        reservations = [
            f"MERGE_OUTPUT#{merge.output_id:05d}",
            *(f"MERGE_SOURCE#{rid:05d}" for rid in merge.source_ids),
        ]
        for sort_key in reservations:
            transaction.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": {
                            "PK": merge.key["PK"],
                            "SK": {"S": sort_key},
                            "TYPE": {"S": "RECEIPT_MERGE_RESERVATION"},
                            "operation": merge.key["SK"],
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                }
            )
        for rid in merge.source_ids:
            source_key = {
                "PK": merge.key["PK"],
                "SK": {"S": f"RECEIPT#{rid:05d}"},
            }
            source = self._client.get_item(
                TableName=self.table_name,
                Key=source_key,
                ConsistentRead=True,
                ProjectionExpression="merge_operation",
            ).get("Item", {})
            producer = source.get("merge_operation")
            source_condition: dict[str, Any] = {
                "TableName": self.table_name,
                "Key": source_key,
                "ConditionExpression": (
                    "attribute_exists(PK) AND "
                    "attribute_not_exists(merge_operation)"
                ),
            }
            if producer is not None:
                source_condition.update(
                    ConditionExpression=(
                        "attribute_exists(PK) AND merge_operation = :producer"
                    ),
                    ExpressionAttributeValues={":producer": producer},
                )
                transaction.append(
                    {
                        "ConditionCheck": {
                            "TableName": self.table_name,
                            "Key": {"PK": merge.key["PK"], "SK": producer},
                            "ConditionExpression": "#status = :done",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": {
                                ":done": {"S": "COMPLETED"}
                            },
                        }
                    }
                )
            transaction.append({"ConditionCheck": source_condition})
        transaction.append(
            {
                "ConditionCheck": {
                    "TableName": self.table_name,
                    "Key": {
                        "PK": merge.key["PK"],
                        "SK": {"S": f"RECEIPT#{merge.output_id:05d}"},
                    },
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        )
        self._client.transact_write_items(TransactItems=transaction)

    @handle_dynamodb_errors("claim_receipt_merge")
    def claim_receipt_merge(
        self, image_id: str, source_ids: list[int], owner: str
    ) -> ReceiptMerge:
        """Reserve a single output or claim an expired/released retry.

        Source reservations also refuse overlapping merges, such as [1, 2]
        and [2, 3]. ID reservations remain after completion so a later merge
        cannot reuse a deleted output's ID.
        """
        template = self._merge_template(image_id, source_ids, owner)
        for _ in range(3):
            current = self.get_receipt_merge(image_id, source_ids)
            if current:
                if current.status == "COMPLETED":
                    return current
                now = int(time.time())
                try:
                    claim = {
                        "TableName": self.table_name,
                        "Key": current.key,
                        "UpdateExpression": (
                            "SET #owner = :owner, lease_until = :lease"
                        ),
                        "ConditionExpression": (
                            "lease_until <= :now AND #status <> :done"
                        ),
                        "ExpressionAttributeNames": {
                            "#owner": "owner",
                            "#status": "status",
                        },
                        "ExpressionAttributeValues": {
                            ":owner": {"S": owner},
                            ":lease": {"N": str(now + MERGE_LEASE_SECONDS)},
                            ":now": {"N": str(now)},
                            ":done": {"S": "COMPLETED"},
                        },
                    }
                    template.lease_until = now + MERGE_LEASE_SECONDS
                    transaction: list[Any] = [
                        {"Update": claim},
                        {"Put": self._image_merge_claim(template)},
                    ]
                    self._client.transact_write_items(
                        TransactItems=transaction
                    )
                    claimed = self.get_receipt_merge(image_id, source_ids)
                    if claimed is None:
                        raise OperationError(
                            "Claimed merge journal is missing"
                        )
                    return claimed
                except ClientError as error:
                    if _conditional_conflict(error):
                        raise OperationError(
                            "A merge on this image is already in progress; "
                            "retry after its lease"
                        ) from error
                    raise
            template.output_id = self._next_merge_output_id(image_id)
            try:
                self._reserve_merge(template)
                return template
            except ClientError as error:
                if not _conditional_conflict(error):
                    raise
        raise OperationError(
            "Merge reservation failed: image is busy, or sources are "
            "missing, already reserved, or still being merged"
        )

    def _merge_owner_condition(
        self, merge: ReceiptMerge
    ) -> "ConditionCheckTypeDef":
        return {
            "TableName": self.table_name,
            "Key": merge.key,
            "ConditionExpression": _OWNED,
            "ExpressionAttributeNames": {
                "#owner": "owner",
                "#status": "status",
            },
            "ExpressionAttributeValues": {
                ":owner": {"S": merge.owner},
                ":now": {"N": str(int(time.time()))},
                ":status": {"S": merge.status},
            },
        }

    @handle_dynamodb_errors("assert_receipt_merge_owner")
    def assert_receipt_merge_owner(self, merge: ReceiptMerge) -> None:
        """Check the execution claim before each external side-effect phase."""
        current = self.get_receipt_merge(
            merge.image_id, list(merge.source_ids)
        )
        if (
            current is None
            or current.owner != merge.owner
            or current.status != merge.status
            or current.lease_until <= int(time.time())
        ):
            raise OperationError("Merge claim expired or changed; retry")
        image_claim = self._client.get_item(
            TableName=self.table_name,
            Key=self._image_merge_key(merge),
            ConsistentRead=True,
        ).get("Item", {})
        if image_claim.get("owner", {}).get("S") != merge.owner or int(
            image_claim.get("lease_until", {}).get("N", "0")
        ) <= int(time.time()):
            raise OperationError("Image merge claim expired or changed; retry")

    @handle_dynamodb_errors("assert_receipt_merge_output")
    def assert_receipt_merge_output(self, merge: ReceiptMerge) -> None:
        """Do not remove sources if a staged output was deleted or replaced."""
        item = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": merge.key["PK"],
                "SK": {"S": f"RECEIPT#{merge.output_id:05d}"},
            },
            ProjectionExpression="merge_operation",
            ConsistentRead=True,
        ).get("Item", {})
        if item.get("merge_operation") != merge.key["SK"]:
            raise OperationError("Merge output is missing or changed")

    @handle_dynamodb_errors("put_receipt_merge_output")
    def put_receipt_merge_output(
        self, merge: ReceiptMerge, receipt: Receipt
    ) -> None:
        """Never overwrite an unrelated producer that used the reserved ID."""
        self._validate_entity(receipt, Receipt, "receipt")
        if (
            receipt.image_id != merge.image_id
            or receipt.receipt_id != merge.output_id
            or merge.status != "PREPARING"
        ):
            raise EntityValidationError("receipt must match a preparing merge")
        self._client.transact_write_items(
            TransactItems=[
                {"ConditionCheck": self._merge_owner_condition(merge)},
                {"ConditionCheck": self._image_merge_owner_condition(merge)},
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": {
                            **receipt.to_item(),
                            "merge_operation": merge.key["SK"],
                        },
                        "ConditionExpression": (
                            "attribute_not_exists(PK) OR "
                            "merge_operation = :operation"
                        ),
                        "ExpressionAttributeValues": {
                            ":operation": merge.key["SK"]
                        },
                    }
                },
            ]
        )

    @handle_dynamodb_errors("update_receipt_merge_image")
    def update_receipt_merge_image(
        self, merge: ReceiptMerge, image: Image
    ) -> None:
        """Require the active image lease when writing the receipt count."""
        self._validate_entity(image, Image, "image")
        if image.image_id != merge.image_id or merge.status != "READY":
            raise EntityValidationError("image must match a ready merge")
        self._client.transact_write_items(
            TransactItems=[
                {"ConditionCheck": self._merge_owner_condition(merge)},
                {"ConditionCheck": self._image_merge_owner_condition(merge)},
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": image.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                },
            ]
        )

    @handle_dynamodb_errors("checkpoint_receipt_merge")
    def checkpoint_receipt_merge(
        self, current: ReceiptMerge, updated: ReceiptMerge
    ) -> ReceiptMerge:
        """Save READY before deletion and COMPLETED after all effects."""
        if (
            updated.key != current.key
            or updated.output_id != current.output_id
            or updated.owner != current.owner
            or (current.status, updated.status)
            not in (("PREPARING", "READY"), ("READY", "COMPLETED"))
        ):
            raise EntityValidationError("invalid merge checkpoint transition")
        condition = self._merge_owner_condition(current)
        transaction: list[Any] = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": updated.to_item(),
                    "ConditionExpression": condition["ConditionExpression"],
                    "ExpressionAttributeNames": condition[
                        "ExpressionAttributeNames"
                    ],
                    "ExpressionAttributeValues": condition[
                        "ExpressionAttributeValues"
                    ],
                }
            }
        ]
        image_condition = self._image_merge_owner_condition(current)
        if updated.status == "COMPLETED":
            transaction.append({"Delete": image_condition})
        else:
            transaction.append({"ConditionCheck": image_condition})
        self._client.transact_write_items(TransactItems=transaction)
        return updated

    @handle_dynamodb_errors("release_receipt_merge")
    def release_receipt_merge(self, merge: ReceiptMerge) -> None:
        """Release caught failures promptly; hard crashes recover by expiry."""
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": merge.key,
                            "UpdateExpression": "SET lease_until = :zero",
                            "ConditionExpression": (
                                "#owner = :owner AND #status <> :done"
                            ),
                            "ExpressionAttributeNames": {
                                "#owner": "owner",
                                "#status": "status",
                            },
                            "ExpressionAttributeValues": {
                                ":owner": {"S": merge.owner},
                                ":zero": {"N": "0"},
                                ":done": {"S": "COMPLETED"},
                            },
                        }
                    },
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": self._image_merge_key(merge),
                            "ConditionExpression": "#owner = :owner",
                            "ExpressionAttributeNames": {"#owner": "owner"},
                            "ExpressionAttributeValues": {
                                ":owner": {"S": merge.owner}
                            },
                        }
                    },
                ]
            )
        except ClientError as error:
            if not _conditional_conflict(error):
                raise
