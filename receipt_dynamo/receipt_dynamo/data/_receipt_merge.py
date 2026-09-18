"""Conditional merge claims, output reservations, and recovery checkpoints."""

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.image import Image, item_to_image
from receipt_dynamo.entities.receipt import Receipt, item_to_receipt
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


def _conflict_positions(error: ClientError) -> list[int]:
    """Transaction items whose condition failed, in TransactItems order."""
    code = error.response.get("Error", {}).get("Code")
    if code == "ConditionalCheckFailedException":
        return [0]
    if code != "TransactionCanceledException":
        return []
    return [
        index
        for index, reason in enumerate(
            error.response.get("CancellationReasons", [])
        )
        if reason.get("Code") == "ConditionalCheckFailed"
    ]


def _conditional_conflict(error: ClientError) -> bool:
    return bool(_conflict_positions(error))


class _ReceiptMerge(FlattenedStandardMixin):
    """Keep merge retries on one output, including after source deletion."""

    if TYPE_CHECKING:
        # Provided by the _Receipt mixin on DynamoClient.
        def purge_receipt_children(
            self, image_id: str, receipt_id: int
        ) -> int: ...

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

    def _output_receipt_key(self, merge: ReceiptMerge) -> dict[str, Any]:
        return {
            "PK": merge.key["PK"],
            "SK": {"S": f"RECEIPT#{merge.output_id:05d}"},
        }

    def _reservation_key(
        self, merge: ReceiptMerge, sort_key: str
    ) -> dict[str, Any]:
        return {"PK": merge.key["PK"], "SK": {"S": sort_key}}

    def _reservation_put(
        self, merge: ReceiptMerge, sort_key: str
    ) -> dict[str, Any]:
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": {
                    **self._reservation_key(merge, sort_key),
                    "TYPE": {"S": "RECEIPT_MERGE_RESERVATION"},
                    "operation": merge.key["SK"],
                },
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        }

    def _owned_row_delete(
        self, key: dict[str, Any], attribute: str, merge: ReceiptMerge
    ) -> dict[str, Any]:
        """Delete a reservation, lock, or output only while it is ours."""
        return {
            "Delete": {
                "TableName": self.table_name,
                "Key": key,
                "ConditionExpression": f"{attribute} = :operation",
                "ExpressionAttributeValues": {":operation": merge.key["SK"]},
            }
        }

    def _output_holder(self, merge: ReceiptMerge) -> dict[str, Any] | None:
        """The current RECEIPT#<output_id> row's marker, or None if absent."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key=self._output_receipt_key(merge),
            ConsistentRead=True,
            ProjectionExpression="merge_operation",
        )
        if "Item" not in response:
            return None
        return response["Item"].get("merge_operation", {})

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
            transaction.append(self._reservation_put(merge, sort_key))
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
                    "Key": self._output_receipt_key(merge),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        )
        self._client.transact_write_items(TransactItems=transaction)

    def _reclaim_merge(self, current: ReceiptMerge, owner: str) -> bool:
        """Take over an expired or released journal without a new output.

        A PREPARING output ID that an unrelated producer has since used is
        re-minted in the same transaction, swapping the MERGE_OUTPUT#
        reservation so neither the old nor the new ID leaks. Returns False
        when a re-mint lost a race and the caller should retry.
        """
        now = int(time.time())
        claimed = replace(
            current, owner=owner, lease_until=now + MERGE_LEASE_SECONDS
        )
        update = "SET #owner = :owner, lease_until = :lease"
        values: dict[str, Any] = {
            ":owner": {"S": owner},
            ":lease": {"N": str(claimed.lease_until)},
            ":now": {"N": str(now)},
            ":done": {"S": "COMPLETED"},
        }
        transaction: list[Any] = []
        if current.status == "PREPARING":
            holder = self._output_holder(current)
            if holder is not None and holder != current.key["SK"]:
                claimed = replace(
                    claimed,
                    output_id=self._next_merge_output_id(current.image_id),
                )
                update += ", output_id = :output"
                values[":output"] = {"N": str(claimed.output_id)}
                transaction.extend(
                    [
                        self._reservation_put(
                            claimed, f"MERGE_OUTPUT#{claimed.output_id:05d}"
                        ),
                        self._owned_row_delete(
                            self._reservation_key(
                                current,
                                f"MERGE_OUTPUT#{current.output_id:05d}",
                            ),
                            "operation",
                            current,
                        ),
                        {
                            "ConditionCheck": {
                                "TableName": self.table_name,
                                "Key": self._output_receipt_key(claimed),
                                "ConditionExpression": (
                                    "attribute_not_exists(PK)"
                                ),
                            }
                        },
                    ]
                )
        journal_index = len(transaction)
        transaction.extend(
            [
                {
                    "Update": {
                        "TableName": self.table_name,
                        "Key": current.key,
                        "UpdateExpression": update,
                        "ConditionExpression": (
                            "lease_until <= :now AND #status <> :done"
                        ),
                        "ExpressionAttributeNames": {
                            "#owner": "owner",
                            "#status": "status",
                        },
                        "ExpressionAttributeValues": values,
                    }
                },
                {"Put": self._image_merge_claim(claimed)},
            ]
        )
        try:
            self._client.transact_write_items(TransactItems=transaction)
        except ClientError as error:
            failed = _conflict_positions(error)
            if not failed:
                raise
            if any(index >= journal_index for index in failed):
                raise OperationError(
                    "A merge on this image is already in progress; "
                    "retry after its lease"
                ) from error
            return False
        return True

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
                if not self._reclaim_merge(current, owner):
                    continue
                claimed = self.get_receipt_merge(image_id, source_ids)
                if claimed is None:
                    raise OperationError("Claimed merge journal is missing")
                return claimed
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
        if self._output_holder(merge) != merge.key["SK"]:
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
                        "Item": replace(
                            receipt, merge_operation=merge.key["SK"]["S"]
                        ).to_item(),
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

    @handle_dynamodb_errors("get_receipt_merge_image")
    def get_receipt_merge_image(self, merge: ReceiptMerge) -> Image:
        """Read the committed Image row before recomputing its count."""
        item = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": merge.key["PK"], "SK": {"S": "IMAGE"}},
            ConsistentRead=True,
        ).get("Item")
        if item is None:
            raise EntityNotFoundError(
                f"Image with ID {merge.image_id} not found"
            )
        return item_to_image(item)

    @handle_dynamodb_errors("update_receipt_merge_image")
    def update_receipt_merge_image(
        self,
        merge: ReceiptMerge,
        image: Image,
        *,
        expected_receipt_count: int | None,
    ) -> None:
        """Require the active image lease when writing the receipt count.

        Pass the ``receipt_count`` observed on the row that ``image`` was
        derived from; the write is refused if another writer changed it in
        between, so the caller recounts instead of clobbering that update.
        """
        self._validate_entity(image, Image, "image")
        if image.image_id != merge.image_id or merge.status != "READY":
            raise EntityValidationError("image must match a ready merge")
        condition = "attribute_exists(PK)"
        values: dict[str, Any] = {}
        if expected_receipt_count is None:
            condition += (
                " AND (attribute_not_exists(receipt_count)"
                " OR attribute_type(receipt_count, :null))"
            )
            values[":null"] = {"S": "NULL"}
        else:
            condition += " AND receipt_count = :expected"
            values[":expected"] = {"N": str(expected_receipt_count)}
        put: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": image.to_item(),
            "ConditionExpression": condition,
            "ExpressionAttributeValues": values,
        }
        self._client.transact_write_items(
            TransactItems=[
                {"ConditionCheck": self._merge_owner_condition(merge)},
                {"ConditionCheck": self._image_merge_owner_condition(merge)},
                {"Put": put},
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

    @handle_dynamodb_errors("abandon_receipt_merge")
    def abandon_receipt_merge(
        self, image_id: str, source_ids: list[int], owner: str | None = None
    ) -> Receipt | None:
        """Free a PREPARING pair whose merge will never be retried.

        Only an expired or released lease, or ``owner`` itself, may abandon.
        The journal is first fenced for the abandoner so a concurrent retry
        cannot re-claim it while the staged output is purged; the journal,
        image lock, ID reservations, and a staged output that carries this
        operation's marker are then removed in one transaction. Returns the
        purged staged output so the caller can delete its S3 objects, which
        this layer never touches. READY and COMPLETED journals are refused:
        their sources may already be gone, so they must be finished instead.
        """
        self._merge_template(image_id, source_ids, owner or "abandon")
        current = self.get_receipt_merge(image_id, source_ids)
        if current is None:
            raise EntityNotFoundError(
                f"No merge journal for receipts {sorted(source_ids)} on "
                f"image {image_id}"
            )
        if current.status != "PREPARING":
            raise OperationError(
                f"Merge is {current.status}; finish it by retrying the pair "
                "instead of abandoning it"
            )
        now = int(time.time())
        abandoner = f"abandon:{uuid4()}"
        fence = "#status = :preparing AND lease_until <= :now"
        values: dict[str, Any] = {
            ":preparing": {"S": "PREPARING"},
            ":now": {"N": str(now)},
            ":abandoner": {"S": abandoner},
            ":lease": {"N": str(now + MERGE_LEASE_SECONDS)},
        }
        if owner is not None:
            fence = (
                "#status = :preparing AND "
                "(lease_until <= :now OR #owner = :owner)"
            )
            values[":owner"] = {"S": owner}
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key=current.key,
                UpdateExpression=(
                    "SET #owner = :abandoner, lease_until = :lease"
                ),
                ConditionExpression=fence,
                ExpressionAttributeNames={
                    "#owner": "owner",
                    "#status": "status",
                },
                ExpressionAttributeValues=values,
            )
        except ClientError as error:
            if _conditional_conflict(error):
                raise OperationError(
                    "Merge is still owned or no longer PREPARING; retry "
                    "after its lease"
                ) from error
            raise
        fenced = replace(current, owner=abandoner)
        staged: Receipt | None = None
        transaction: list[Any] = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": fenced.key,
                    "ConditionExpression": (
                        "#owner = :abandoner AND #status = :preparing"
                    ),
                    "ExpressionAttributeNames": {
                        "#owner": "owner",
                        "#status": "status",
                    },
                    "ExpressionAttributeValues": {
                        ":abandoner": {"S": abandoner},
                        ":preparing": {"S": "PREPARING"},
                    },
                }
            },
            *(
                self._owned_row_delete(
                    self._reservation_key(fenced, sort_key),
                    "operation",
                    fenced,
                )
                for sort_key in (
                    f"MERGE_OUTPUT#{fenced.output_id:05d}",
                    *(f"MERGE_SOURCE#{rid:05d}" for rid in fenced.source_ids),
                )
            ),
        ]
        if self._output_holder(fenced) == fenced.key["SK"]:
            staged_item = self._client.get_item(
                TableName=self.table_name,
                Key=self._output_receipt_key(fenced),
                ConsistentRead=True,
            ).get("Item")
            if staged_item is not None:
                staged = item_to_receipt(staged_item)
            self.purge_receipt_children(image_id, fenced.output_id)
            transaction.append(
                self._owned_row_delete(
                    self._output_receipt_key(fenced), "merge_operation", fenced
                )
            )
        lock = self._client.get_item(
            TableName=self.table_name,
            Key=self._image_merge_key(fenced),
            ConsistentRead=True,
            ProjectionExpression="operation",
        ).get("Item", {})
        if lock.get("operation") == fenced.key["SK"]:
            transaction.append(
                self._owned_row_delete(
                    self._image_merge_key(fenced), "operation", fenced
                )
            )
        try:
            self._client.transact_write_items(TransactItems=transaction)
        except ClientError as error:
            if _conditional_conflict(error):
                raise OperationError(
                    "Merge journal changed while being abandoned; retry"
                ) from error
            raise
        return staged
