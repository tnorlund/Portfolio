"""Safely apply audited batch updates to receipt word label statuses."""

import json
import os
import time
from pathlib import Path
from typing import Any

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

_OUTCOMES = (
    "UPDATED",
    "WOULD_UPDATE",
    "SKIPPED_NOT_FOUND",
    "SKIPPED_STATUS_CHANGED",
    "ERROR",
)
_ID_FIELDS = ("image_id", "receipt_id", "line_id", "word_id")


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
    )
    item = response.get("Item")
    if item is None:
        return False, None

    return True, item.get("validation_status", {}).get("S")


def _append_audit_line(path: Path, record: dict[str, Any]) -> None:
    """Append and close one compact JSON audit record."""
    with path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(
            json.dumps(record, separators=(",", ":"), default=str) + "\n"
        )


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


def batch_update_word_labels(
    dynamo_client: Any,
    updates: list[dict[str, Any]],
    *,
    label_proposed_by: str,
    audit_path: str | os.PathLike[str],
    expected_old_status: str = "VALID",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply guarded, audited status transitions to receipt word labels."""
    _validate_guard(dynamo_client)

    if not isinstance(label_proposed_by, str) or not label_proposed_by.strip():
        raise ValueError("label_proposed_by must be a non-empty string")

    if not isinstance(audit_path, (str, os.PathLike)) or not str(audit_path):
        raise ValueError("audit_path must be a non-empty string or path")

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

        if "new_status" not in update:
            raise ValueError(f"updates[{index}].new_status is required")

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

    audit_file_path = Path(audit_path)
    counts = {outcome: 0 for outcome in _OUTCOMES}
    rows = []
    last_updated: tuple[dict[str, Any], str] | None = None

    for update, new_status in zip(
        updates,
        normalized_statuses,
        strict=True,
    ):
        result = _row_result(update, new_status)

        try:
            exists, current_status = _read_status(dynamo_client, update)
        except Exception:
            result["outcome"] = "ERROR"
            counts["ERROR"] += 1
            rows.append(result)
            continue

        result["old_status"] = current_status

        if not exists:
            result["outcome"] = "SKIPPED_NOT_FOUND"
            counts["SKIPPED_NOT_FOUND"] += 1
            rows.append(result)
            continue

        if current_status != expected_status:
            result["outcome"] = "SKIPPED_STATUS_CHANGED"
            counts["SKIPPED_STATUS_CHANGED"] += 1
            rows.append(result)
            continue

        if dry_run:
            result["outcome"] = "WOULD_UPDATE"
            counts["WOULD_UPDATE"] += 1
            rows.append(result)
            continue

        try:
            reasoning = update["reasoning"]
            if not isinstance(reasoning, str):
                raise ValueError("reasoning must be a string")

            audit_record = {
                "image_id": update["image_id"],
                "receipt_id": update["receipt_id"],
                "line_id": update["line_id"],
                "word_id": update["word_id"],
                "label": update["label"],
                "old_status": expected_status,
                "new_status": new_status,
                "reasoning": reasoning,
                "label_proposed_by": label_proposed_by,
            }
            _append_audit_line(audit_file_path, audit_record)

            dynamo_client._client.update_item(
                TableName=dynamo_client.table_name,
                Key=_dynamo_key(update),
                UpdateExpression=(
                    "SET validation_status = :status, GSI3PK = :gsi3pk, "
                    "label_proposed_by = :proposed_by, reasoning = :reasoning"
                ),
                ConditionExpression="validation_status = :expected",
                ExpressionAttributeValues={
                    ":status": {"S": new_status},
                    ":gsi3pk": {
                        "S": f"VALIDATION_STATUS#{new_status}",
                    },
                    ":proposed_by": {"S": label_proposed_by},
                    ":reasoning": {"S": reasoning[:500]},
                    ":expected": {"S": expected_status},
                },
            )
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code != "ConditionalCheckFailedException":
                result["outcome"] = "ERROR"
                counts["ERROR"] += 1
                rows.append(result)
                continue

            try:
                _, current_status = _read_status(dynamo_client, update)
            except Exception:
                current_status = None

            result["old_status"] = current_status
            result["outcome"] = "SKIPPED_STATUS_CHANGED"
            counts["SKIPPED_STATUS_CHANGED"] += 1

            failed_write_record = {
                **audit_record,
                "write_outcome": "CONDITIONAL_CHECK_FAILED",
                "current_status": current_status,
            }
            try:
                _append_audit_line(audit_file_path, failed_write_record)
            except Exception:
                pass

            rows.append(result)
            continue
        except Exception:
            result["outcome"] = "ERROR"
            counts["ERROR"] += 1
            rows.append(result)
            continue

        result["outcome"] = "UPDATED"
        counts["UPDATED"] += 1
        rows.append(result)
        last_updated = (update, new_status)

    spot_check = "not_run"
    if not dry_run and last_updated is not None:
        spot_check = _spot_check(
            dynamo_client,
            last_updated[0],
            last_updated[1],
        )

    return {
        "dry_run": bool(dry_run),
        "counts": counts,
        "spot_check": spot_check,
        "rows": rows,
    }
