"""A completed Swift refinement must re-finalize canonical receipt rows."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from receipt_dynamo.entities.ocr_job import OCRJob
from receipt_dynamo_stream.message_builder import build_messages_from_records
from receipt_dynamo_stream.models import TargetQueue
from receipt_dynamo_stream.sqs_publisher import _message_to_dict


def _job(status="PENDING", job_type="LINE_ITEM_REFINE", receipt_id=1):
    return OCRJob(
        image_id="11111111-2222-4333-8444-555555555555",
        job_id="22222222-3333-4444-8555-666666666666",
        s3_bucket="test-bucket",
        s3_key="result.json",
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        status=status,
        job_type=job_type,
        receipt_id=receipt_id,
    )


def _record(old, new, event="MODIFY"):
    job = new or old
    dynamodb = {"Keys": job.key}
    if old:
        dynamodb["OldImage"] = old.to_item()
    if new:
        dynamodb["NewImage"] = new.to_item()
    return {"eventName": event, "eventID": "refine-done", "dynamodb": dynamodb}


def test_refine_completion_routes_receipt_identity_to_canonical_updater():
    old = _job()
    new = replace(old, status="COMPLETED")
    messages = build_messages_from_records([_record(old, new)])
    assert len(messages) == 1
    assert messages[0].collections == (TargetQueue.LINE_ITEMS,)
    assert messages[0].entity_data == {
        "entity_type": "OCR_JOB",
        "image_id": old.image_id,
        "receipt_id": 1,
    }
    assert messages[0].changes["status"].new == "COMPLETED"


@pytest.mark.parametrize(
    "old_status,new_status,job_type,receipt_id,event",
    [
        ("PENDING", "PENDING", "LINE_ITEM_REFINE", 1, "MODIFY"),
        ("PENDING", "FAILED", "LINE_ITEM_REFINE", 1, "MODIFY"),
        ("COMPLETED", "COMPLETED", "LINE_ITEM_REFINE", 1, "MODIFY"),
        ("COMPLETED", "PENDING", "LINE_ITEM_REFINE", 1, "MODIFY"),
        ("PENDING", "COMPLETED", "FIRST_PASS", 1, "MODIFY"),
        ("PENDING", "COMPLETED", "REFINEMENT", 1, "MODIFY"),
        ("PENDING", "COMPLETED", "LINE_ITEM_REFINE", None, "MODIFY"),
        (None, "COMPLETED", "LINE_ITEM_REFINE", 1, "INSERT"),
        ("COMPLETED", None, "LINE_ITEM_REFINE", 1, "REMOVE"),
    ],
)
def test_non_completion_events_do_not_recompute(
    old_status, new_status, job_type, receipt_id, event
):
    old = _job(old_status, job_type, receipt_id) if old_status else None
    new = _job(new_status, job_type, receipt_id) if new_status else None
    assert build_messages_from_records([_record(old, new, event)]) == []


def test_receipt_retargeting_does_not_route_a_completion():
    old = _job()
    new = replace(old, status="COMPLETED", receipt_id=2)
    assert build_messages_from_records([_record(old, new)]) == []


def test_upstream_redelivery_refinalizes_already_completed_refine():
    old = replace(_job("COMPLETED"), updated_at=_job().created_at)
    new = replace(old, updated_at=old.updated_at + timedelta(seconds=60))
    messages = build_messages_from_records([_record(old, new)])
    assert len(messages) == 1
    assert messages[0].collections == (TargetQueue.LINE_ITEMS,)
    # Exercise the actual publisher conversion: raw datetime FieldChanges
    # would route in unit tests but fail before a message reached SQS.
    body = json.loads(json.dumps(_message_to_dict(messages[0])))
    assert body["changes"]["updated_at"] == {
        "old": old.updated_at.isoformat(),
        "new": new.updated_at.isoformat(),
    }
    assert body["entity_data"]["receipt_id"] == 1
