"""Safely apply durably audited batch updates to word-label statuses."""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus

ALLOWED_TRANSITIONS = frozenset(
    {
        ("VALID", "INVALID"),
        ("VALID", "NEEDS_REVIEW"),
        ("INVALID", "VALID"),
        ("NEEDS_REVIEW", "VALID"),
        ("NEEDS_REVIEW", "INVALID"),
    }
)
DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
MAX_BATCH = 500

AUDIT_PK_PREFIX = "BATCH_LABEL_UPDATE#"
AUDIT_ENTITY_TYPE = "BATCH_LABEL_UPDATE_AUDIT"

_OUTCOMES = (
    "UPDATED",
    "WOULD_UPDATE",
    "SKIPPED_NOT_FOUND",
    "SKIPPED_STATUS_CHANGED",
    "ERROR",
)
_ID_FIELDS = ("image_id", "receipt_id", "line_id", "word_id")


class AuditStoreError(RuntimeError):
    """Raised before mutation when the durable audit plan cannot be stored."""


def _validation_status(value: Any, field_name: str) -> str:
    """Return a normalized ValidationStatus value."""
    try:
        return ValidationStatus(value).value
    except (TypeError, ValueError) as error:
        valid = [status.value for status in ValidationStatus]
        raise ValueError(
            f"{field_name} must be a valid ValidationStatus value: {valid}"
        ) from error


def _validate_guard(dynamo_client: Any) -> None:
    """Ensure this operation can only target the known development table."""
    configured_table = os.environ.get("DYNAMODB_TABLE_NAME")
    client_table = getattr(dynamo_client, "table_name", None)

    if not configured_table:
        raise ValueError("DYNAMODB_TABLE_NAME must be set")

    if configured_table == PROD_TABLE or client_table == PROD_TABLE:
        raise ValueError(
            f"Production table {PROD_TABLE!r} is forbidden for batch updates"
        )

    if configured_table != client_table:
        raise ValueError(
            "DYNAMODB_TABLE_NAME must equal dynamo_client.table_name "
            f"({configured_table!r} != {client_table!r})"
        )

    if configured_table != DEV_TABLE:
        raise ValueError(
            f"Batch updates are dev-table-only; expected {DEV_TABLE!r}, "
            f"got {configured_table!r}"
        )


def _dynamo_key(update: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Build the exact RECEIPT_WORD_LABEL primary key."""
    return {
        "PK": {"S": f"IMAGE#{update['image_id']}"},
        "SK": {
            "S": (
                f"RECEIPT#{update['receipt_id']:05d}"
                f"#LINE#{update['line_id']:05d}"
                f"#WORD#{update['word_id']:05d}"
                f"#LABEL#{update['label']}"
            )
        },
    }


def _gsi3_sort_key(update: dict[str, Any]) -> str:
    """Build the exact ReceiptWordLabel GSI3 sort key."""
    return (
        f"IMAGE#{update['image_id']}"
        f"#RECEIPT#{update['receipt_id']:05d}"
        f"#LINE#{update['line_id']:05d}"
        f"#WORD#{update['word_id']:05d}"
        f"#LABEL#{update['label']}"
    )


def _read_status(
    dynamo_client: Any,
    update: dict[str, Any],
) -> tuple[bool, str | None]:
    """Read the current validation status for one label row."""
    response = dynamo_client._client.get_item(
        TableName=dynamo_client.table_name,
        Key=_dynamo_key(update),
        ProjectionExpression="validation_status",
        ConsistentRead=True,
    )
    item = response.get("Item")
    if item is None:
        return False, None

    return True, item.get("validation_status", {}).get("S")


def _row_result(
    update: dict[str, Any],
    new_status: str,
) -> dict[str, Any]:
    """Build the stable public result shape for one input row."""
    result = {field: update.get(field) for field in _ID_FIELDS}
    result.update(
        {
            "label": update.get("label"),
            "outcome": "ERROR",
            "old_status": None,
            "new_status": new_status,
        }
    )
    return result


def _spot_check(
    dynamo_client: Any,
    update: dict[str, Any],
    new_status: str,
) -> str:
    """Verify the final updated row through GSI3."""
    expression_values = {
        ":p": {"S": f"VALIDATION_STATUS#{new_status}"},
        ":s": {"S": _gsi3_sort_key(update)},
    }

    for attempt in range(5):
        try:
            response = dynamo_client._client.query(
                TableName=dynamo_client.table_name,
                IndexName="GSI3",
                KeyConditionExpression="GSI3PK = :p AND GSI3SK = :s",
                ExpressionAttributeValues=expression_values,
            )
            if response.get("Count") == 1:
                return "passed"
        except Exception:
            pass

        if attempt < 4:
            time.sleep(1)

    return "failed"


def _timestamp() -> str:
    """Return a stable UTC audit timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _audit_pk(batch_id: str) -> str:
    return f"{AUDIT_PK_PREFIX}{batch_id}"


def _audit_key(batch_id: str, row_index: int | None) -> dict[str, Any]:
    sort_key = "MANIFEST" if row_index is None else f"ROW#{row_index:05d}"
    return {"PK": {"S": _audit_pk(batch_id)}, "SK": {"S": sort_key}}


def _audit_uri(table_name: str, batch_id: str) -> str:
    """Return the durable DynamoDB URI for the batch audit partition."""
    return f"dynamodb://{table_name}/{quote(_audit_pk(batch_id), safe='')}"


def _plan_sha256(
    updates: list[dict[str, Any]],
    *,
    label_proposed_by: str,
    expected_old_status: str,
) -> str:
    """Hash the exact operator intent independently from mutable outcomes."""
    payload = {
        "expected_old_status": expected_old_status,
        "label_proposed_by": label_proposed_by,
        "updates": updates,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aws_error_code(error: BaseException) -> str:
    """Return an explicit stable code for AWS and non-AWS failures."""
    if isinstance(error, ClientError):
        return str(
            error.response.get("Error", {}).get("Code") or "ClientError"
        )
    return type(error).__name__


def _audit_manifest_item(
    batch_id: str,
    *,
    plan_sha256: str,
    label_proposed_by: str,
    expected_old_status: str,
    row_count: int,
) -> dict[str, Any]:
    """Build the batch-level durable manifest item."""
    return {
        **_audit_key(batch_id, None),
        "TYPE": {"S": AUDIT_ENTITY_TYPE},
        "record_type": {"S": "MANIFEST"},
        "batch_id": {"S": batch_id},
        "outcome": {"S": "PLANNED"},
        "plan_sha256": {"S": plan_sha256},
        "label_proposed_by": {"S": label_proposed_by},
        "expected_old_status": {"S": expected_old_status},
        "row_count": {"N": str(row_count)},
        "created_at": {"S": _timestamp()},
    }


def _audit_row_item(
    batch_id: str,
    row_index: int,
    update: dict[str, Any],
    *,
    plan_sha256: str,
    label_proposed_by: str,
    expected_old_status: str,
    new_status: str,
) -> dict[str, Any]:
    """Build one complete, pre-mutation row plan."""
    return {
        **_audit_key(batch_id, row_index),
        "TYPE": {"S": AUDIT_ENTITY_TYPE},
        "record_type": {"S": "ROW"},
        "batch_id": {"S": batch_id},
        "row_index": {"N": str(row_index)},
        "outcome": {"S": "PLANNED"},
        "plan_sha256": {"S": plan_sha256},
        "image_id": {"S": str(update["image_id"])},
        "receipt_id": {"N": str(update["receipt_id"])},
        "line_id": {"N": str(update["line_id"])},
        "word_id": {"N": str(update["word_id"])},
        "label": {"S": str(update["label"])},
        "expected_old_status": {"S": expected_old_status},
        "new_status": {"S": new_status},
        "reasoning": {"S": update["reasoning"]},
        "label_proposed_by": {"S": label_proposed_by},
        "created_at": {"S": _timestamp()},
    }


def _put_new_audit_item(dynamo_client: Any, item: dict[str, Any]) -> None:
    """Persist one immutable-key audit item, rejecting collisions."""
    dynamo_client._client.put_item(
        TableName=dynamo_client.table_name,
        Item=item,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )


def _persist_audit_plan(
    dynamo_client: Any,
    updates: list[dict[str, Any]],
    normalized_statuses: list[str],
    *,
    batch_id: str,
    plan_sha256: str,
    label_proposed_by: str,
    expected_old_status: str,
) -> None:
    """Persist the full manifest and every row before the first mutation."""
    try:
        _put_new_audit_item(
            dynamo_client,
            _audit_manifest_item(
                batch_id,
                plan_sha256=plan_sha256,
                label_proposed_by=label_proposed_by,
                expected_old_status=expected_old_status,
                row_count=len(updates),
            ),
        )
        for row_index, (update, new_status) in enumerate(
            zip(updates, normalized_statuses, strict=True)
        ):
            _put_new_audit_item(
                dynamo_client,
                _audit_row_item(
                    batch_id,
                    row_index,
                    update,
                    plan_sha256=plan_sha256,
                    label_proposed_by=label_proposed_by,
                    expected_old_status=expected_old_status,
                    new_status=new_status,
                ),
            )
    except Exception as error:
        raise AuditStoreError(
            "Durable audit plan failed before mutation "
            f"({_aws_error_code(error)})"
        ) from error


def _finalize_audit_row(
    dynamo_client: Any,
    batch_id: str,
    row_index: int,
    *,
    outcome: str,
    observed_status: str | None,
    reconciliation: str,
    aws_error_code: str | None = None,
) -> None:
    """Replace PLANNED with one explicit terminal outcome."""
    update_expression = (
        "SET #outcome = :outcome, completed_at = :completed_at, "
        "observed_status = :observed_status, reconciliation = :reconciliation"
    )
    values: dict[str, Any] = {
        ":outcome": {"S": outcome},
        ":completed_at": {"S": _timestamp()},
        ":observed_status": (
            {"S": observed_status}
            if observed_status is not None
            else {"NULL": True}
        ),
        ":reconciliation": {"S": reconciliation},
        ":planned": {"S": "PLANNED"},
    }
    if aws_error_code is not None:
        update_expression += ", aws_error_code = :aws_error_code"
        values[":aws_error_code"] = {"S": aws_error_code}

    dynamo_client._client.update_item(
        TableName=dynamo_client.table_name,
        Key=_audit_key(batch_id, row_index),
        UpdateExpression=update_expression,
        ConditionExpression="#outcome = :planned",
        ExpressionAttributeNames={"#outcome": "outcome"},
        ExpressionAttributeValues=values,
    )


def _annotate_committed_audit(
    dynamo_client: Any,
    batch_id: str,
    row_index: int,
    *,
    observed_status: str | None,
    aws_error_code: str,
) -> None:
    """Record that a raised transport error was observed after commit."""
    dynamo_client._client.update_item(
        TableName=dynamo_client.table_name,
        Key=_audit_key(batch_id, row_index),
        UpdateExpression=(
            "SET reconciliation = :reconciliation, "
            "observed_status = :observed_status, "
            "aws_error_code = :aws_error_code"
        ),
        ConditionExpression="#outcome = :updated",
        ExpressionAttributeNames={"#outcome": "outcome"},
        ExpressionAttributeValues={
            ":reconciliation": {"S": "COMMITTED_AFTER_ERROR"},
            ":observed_status": (
                {"S": observed_status}
                if observed_status is not None
                else {"NULL": True}
            ),
            ":aws_error_code": {"S": aws_error_code},
            ":updated": {"S": "UPDATED"},
        },
    )


def _read_audit_outcome(
    dynamo_client: Any,
    batch_id: str,
    row_index: int,
) -> str | None:
    """Consistently read a row audit outcome for error reconciliation."""
    response = dynamo_client._client.get_item(
        TableName=dynamo_client.table_name,
        Key=_audit_key(batch_id, row_index),
        ProjectionExpression="#outcome",
        ExpressionAttributeNames={"#outcome": "outcome"},
        ConsistentRead=True,
    )
    item = response.get("Item")
    return None if item is None else item.get("outcome", {}).get("S")


def _label_update(
    dynamo_client: Any,
    update: dict[str, Any],
    *,
    expected_status: str,
    new_status: str,
    label_proposed_by: str,
    reasoning: str,
) -> dict[str, Any]:
    """Build the conditional label update for an atomic transaction."""
    return {
        "TableName": dynamo_client.table_name,
        "Key": _dynamo_key(update),
        "UpdateExpression": (
            "SET validation_status = :status, GSI3PK = :gsi3pk, "
            "label_proposed_by = :proposed_by, reasoning = :reasoning"
        ),
        "ConditionExpression": "validation_status = :expected",
        "ExpressionAttributeValues": {
            ":status": {"S": new_status},
            ":gsi3pk": {"S": f"VALIDATION_STATUS#{new_status}"},
            ":proposed_by": {"S": label_proposed_by},
            ":reasoning": {"S": reasoning[:500]},
            ":expected": {"S": expected_status},
        },
    }


def _audit_success_update(
    dynamo_client: Any,
    batch_id: str,
    row_index: int,
    new_status: str,
) -> dict[str, Any]:
    """Build the audit outcome update paired atomically with label mutation."""
    return {
        "TableName": dynamo_client.table_name,
        "Key": _audit_key(batch_id, row_index),
        "UpdateExpression": (
            "SET #outcome = :updated, completed_at = :completed_at, "
            "observed_status = :observed_status, "
            "reconciliation = :reconciliation"
        ),
        "ConditionExpression": "#outcome = :planned",
        "ExpressionAttributeNames": {"#outcome": "outcome"},
        "ExpressionAttributeValues": {
            ":updated": {"S": "UPDATED"},
            ":completed_at": {"S": _timestamp()},
            ":observed_status": {"S": new_status},
            ":reconciliation": {"S": "ATOMIC_COMMIT"},
            ":planned": {"S": "PLANNED"},
        },
    }


def _complete_audit_manifest(
    dynamo_client: Any,
    batch_id: str,
    counts: dict[str, int],
) -> str | None:
    """Mark a processed audit batch complete without hiding row results."""
    try:
        dynamo_client._client.update_item(
            TableName=dynamo_client.table_name,
            Key=_audit_key(batch_id, None),
            UpdateExpression=(
                "SET #outcome = :completed, completed_at = :completed_at, "
                "counts_json = :counts_json"
            ),
            ConditionExpression="#outcome = :planned",
            ExpressionAttributeNames={"#outcome": "outcome"},
            ExpressionAttributeValues={
                ":completed": {"S": "COMPLETED"},
                ":completed_at": {"S": _timestamp()},
                ":counts_json": {
                    "S": json.dumps(
                        counts, sort_keys=True, separators=(",", ":")
                    )
                },
                ":planned": {"S": "PLANNED"},
            },
        )
    except Exception as error:
        return _aws_error_code(error)
    return None


def _finalize_or_audit_error(
    dynamo_client: Any,
    batch_id: str,
    row_index: int,
    result: dict[str, Any],
    *,
    outcome: str,
    observed_status: str | None,
    reconciliation: str,
    aws_error_code: str | None = None,
) -> str:
    """Finalize a no-mutation result, failing closed if auditing fails."""
    try:
        _finalize_audit_row(
            dynamo_client,
            batch_id,
            row_index,
            outcome=outcome,
            observed_status=observed_status,
            reconciliation=reconciliation,
            aws_error_code=aws_error_code,
        )
    except Exception as audit_error:
        result["outcome"] = "ERROR"
        result["error_code"] = "AUDIT_WRITE_FAILED"
        result["audit_error_code"] = _aws_error_code(audit_error)
        result["reconciliation"] = "NO_MUTATION_AUDIT_FAILED"
        return "ERROR"

    result["outcome"] = outcome
    result["reconciliation"] = reconciliation
    if aws_error_code is not None:
        result["error_code"] = aws_error_code
    return outcome


def batch_update_word_labels(
    dynamo_client: Any,
    updates: list[dict[str, Any]],
    *,
    label_proposed_by: str,
    expected_old_status: str = "VALID",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply guarded status transitions with a durable DynamoDB audit."""
    _validate_guard(dynamo_client)

    if not isinstance(label_proposed_by, str) or not label_proposed_by.strip():
        raise ValueError("label_proposed_by must be a non-empty string")

    if not isinstance(updates, list):
        raise ValueError("updates must be a list")

    if len(updates) > MAX_BATCH:
        raise ValueError(
            f"updates contains {len(updates)} rows; maximum is {MAX_BATCH}"
        )

    expected_status = _validation_status(
        expected_old_status,
        "expected_old_status",
    )
    normalized_statuses = []

    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            raise ValueError(f"updates[{index}] must be an object")

        required = (*_ID_FIELDS, "label", "new_status", "reasoning")
        missing = [field for field in required if field not in update]
        if missing:
            raise ValueError(
                f"updates[{index}] missing required fields: {missing}"
            )
        if not isinstance(update["reasoning"], str):
            raise ValueError(f"updates[{index}].reasoning must be a string")

        new_status = _validation_status(
            update["new_status"],
            f"updates[{index}].new_status",
        )
        transition = (expected_status, new_status)
        if transition not in ALLOWED_TRANSITIONS:
            raise ValueError(
                "Status transition "
                f"{expected_status}->{new_status} is not allowed"
            )
        normalized_statuses.append(new_status)

    counts = {outcome: 0 for outcome in _OUTCOMES}
    rows = []
    last_updated: tuple[dict[str, Any], str] | None = None
    batch_id: str | None = None
    audit_uri: str | None = None
    audit_sha256: str | None = None

    if not dry_run:
        batch_id = str(uuid4())
        audit_sha256 = _plan_sha256(
            updates,
            label_proposed_by=label_proposed_by,
            expected_old_status=expected_status,
        )
        audit_uri = _audit_uri(dynamo_client.table_name, batch_id)
        _persist_audit_plan(
            dynamo_client,
            updates,
            normalized_statuses,
            batch_id=batch_id,
            plan_sha256=audit_sha256,
            label_proposed_by=label_proposed_by,
            expected_old_status=expected_status,
        )

    for row_index, (update, new_status) in enumerate(
        zip(updates, normalized_statuses, strict=True)
    ):
        result = _row_result(update, new_status)

        try:
            exists, current_status = _read_status(dynamo_client, update)
        except Exception as error:
            outcome = "ERROR"
            result["error_code"] = _aws_error_code(error)
            result["reconciliation"] = "READ_FAILED"
            if batch_id is not None:
                outcome = _finalize_or_audit_error(
                    dynamo_client,
                    batch_id,
                    row_index,
                    result,
                    outcome="ERROR",
                    observed_status=None,
                    reconciliation="READ_FAILED",
                    aws_error_code=_aws_error_code(error),
                )
            result["outcome"] = outcome
            counts[outcome] += 1
            rows.append(result)
            continue

        result["old_status"] = current_status

        if not exists:
            outcome = "SKIPPED_NOT_FOUND"
            if batch_id is not None:
                outcome = _finalize_or_audit_error(
                    dynamo_client,
                    batch_id,
                    row_index,
                    result,
                    outcome=outcome,
                    observed_status=None,
                    reconciliation="PRECHECK_NOT_FOUND",
                )
            else:
                result["outcome"] = outcome
            counts[outcome] += 1
            rows.append(result)
            continue

        if current_status != expected_status:
            outcome = "SKIPPED_STATUS_CHANGED"
            if batch_id is not None:
                outcome = _finalize_or_audit_error(
                    dynamo_client,
                    batch_id,
                    row_index,
                    result,
                    outcome=outcome,
                    observed_status=current_status,
                    reconciliation="PRECHECK_STATUS_CHANGED",
                )
            else:
                result["outcome"] = outcome
            counts[outcome] += 1
            rows.append(result)
            continue

        if dry_run:
            result["outcome"] = "WOULD_UPDATE"
            counts["WOULD_UPDATE"] += 1
            rows.append(result)
            continue

        assert batch_id is not None
        error_code: str | None = None
        try:
            dynamo_client._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": _label_update(
                            dynamo_client,
                            update,
                            expected_status=expected_status,
                            new_status=new_status,
                            label_proposed_by=label_proposed_by,
                            reasoning=update["reasoning"],
                        )
                    },
                    {
                        "Update": _audit_success_update(
                            dynamo_client,
                            batch_id,
                            row_index,
                            new_status,
                        )
                    },
                ]
            )
        except Exception as error:
            error_code = _aws_error_code(error)

        if error_code is None:
            result["outcome"] = "UPDATED"
            result["reconciliation"] = "ATOMIC_COMMIT"
            counts["UPDATED"] += 1
            rows.append(result)
            last_updated = (update, new_status)
            continue

        try:
            audit_outcome = _read_audit_outcome(
                dynamo_client,
                batch_id,
                row_index,
            )
            exists_after, status_after = _read_status(dynamo_client, update)
        except Exception as reconciliation_error:
            result["outcome"] = "ERROR"
            result["error_code"] = error_code
            result["reconciliation"] = "RECONCILIATION_READ_FAILED"
            result["reconciliation_error_code"] = _aws_error_code(
                reconciliation_error
            )
            counts["ERROR"] += 1
            rows.append(result)
            continue

        if audit_outcome == "UPDATED":
            result["outcome"] = "UPDATED"
            result["error_code"] = error_code
            result["reconciliation"] = "COMMITTED_AFTER_ERROR"
            try:
                _annotate_committed_audit(
                    dynamo_client,
                    batch_id,
                    row_index,
                    observed_status=status_after,
                    aws_error_code=error_code,
                )
            except Exception as audit_error:
                result["audit_annotation_error_code"] = _aws_error_code(
                    audit_error
                )
            counts["UPDATED"] += 1
            rows.append(result)
            last_updated = (update, new_status)
            continue

        result["old_status"] = status_after
        if not exists_after:
            outcome = "SKIPPED_NOT_FOUND"
            reconciliation = "NOT_FOUND_AFTER_ERROR"
        elif status_after != expected_status:
            outcome = "SKIPPED_STATUS_CHANGED"
            reconciliation = (
                "AMBIGUOUS_EXTERNAL_STATE"
                if status_after == new_status
                else "STATUS_CHANGED_AFTER_ERROR"
            )
        else:
            outcome = "ERROR"
            reconciliation = "NOT_APPLIED"

        outcome = _finalize_or_audit_error(
            dynamo_client,
            batch_id,
            row_index,
            result,
            outcome=outcome,
            observed_status=status_after,
            reconciliation=reconciliation,
            aws_error_code=error_code,
        )
        counts[outcome] += 1
        rows.append(result)

    spot_check = "not_run"
    if not dry_run and last_updated is not None:
        spot_check = _spot_check(
            dynamo_client,
            last_updated[0],
            last_updated[1],
        )

    manifest_error = None
    if batch_id is not None:
        manifest_error = _complete_audit_manifest(
            dynamo_client,
            batch_id,
            counts,
        )

    return {
        "dry_run": bool(dry_run),
        "batch_id": batch_id,
        "audit_uri": audit_uri,
        "audit_sha256": audit_sha256,
        "audit_manifest_status": (
            "not_run"
            if batch_id is None
            else ("complete" if manifest_error is None else "error")
        ),
        "audit_manifest_error_code": manifest_error,
        "counts": counts,
        "spot_check": spot_check,
        "rows": rows,
    }
