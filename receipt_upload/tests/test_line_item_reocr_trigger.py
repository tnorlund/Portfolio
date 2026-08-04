"""SMART re-OCR trigger tests: _maybe_trigger_items_reocr threads the
strategy ladder (mechanism + attempt number) into the trigger payload."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

# isort: off
# Editable local packages land in different groups across the two CI venvs.
from infra.receipt_line_item_updater import line_item_processor

# isort: on

IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"

# Items sum (1.00) is wildly off the printed subtotal (50.0), so
# should_reocr_items_zone fires.
MISMATCH_ITEMS = [{"price": 1.00}]
MISMATCH_SUMMARY = {"subtotal": 50.0, "grand_total": None, "tax": None}
ZONE_WORDS = [
    {
        "line_id": 1,
        "word_id": 1,
        "text": "1.00",
        "x": 0.8,
        "y_mid": 0.5,
        "h": 0.02,
    }
]


def _prior_job():
    return SimpleNamespace(
        job_type="REGIONAL_REOCR",
        receipt_id=1,
        reocr_reason=line_item_processor.REOCR_REASON,
    )


def _setup(monkeypatch, prior_jobs):
    monkeypatch.setenv("TRIGGER_REOCR_FUNCTION_NAME", "trigger-fn")
    client = MagicMock()
    client.list_ocr_jobs_for_image.return_value = (prior_jobs, None)
    # Identity unit-square receipt: receipt-relative == image coords.
    client.get_receipt.return_value = SimpleNamespace(
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_right={"x": 1.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 0.0},
        width=1000,
        height=2000,
    )
    client.get_image.return_value = SimpleNamespace(width=1000, height=2000)
    monkeypatch.setattr(line_item_processor, "dynamo_client", client)

    lambda_client = MagicMock()
    import boto3

    monkeypatch.setattr(boto3, "client", MagicMock(return_value=lambda_client))
    return lambda_client


def _invoke_payload(lambda_client):
    lambda_client.invoke.assert_called_once()
    return json.loads(lambda_client.invoke.call_args.kwargs["Payload"])


def test_attempt_one_uses_mechanism_ladder_head(monkeypatch):
    lambda_client = _setup(monkeypatch, prior_jobs=[])
    fired = line_item_processor._maybe_trigger_items_reocr(
        IMAGE_ID,
        1,
        MISMATCH_ITEMS,
        MISMATCH_SUMMARY,
        ZONE_WORDS,
        reocr_mechanism="reverse-video-total",
    )
    assert fired is True
    payload = _invoke_payload(lambda_client)
    assert payload["reocr_strategy"] == "invert"
    assert payload["reocr_mechanism"] == "reverse-video-total"
    assert payload["reocr_reason"] == line_item_processor.REOCR_REASON


def test_attempt_two_uses_a_different_strategy(monkeypatch):
    lambda_client = _setup(monkeypatch, prior_jobs=[_prior_job()])
    fired = line_item_processor._maybe_trigger_items_reocr(
        IMAGE_ID,
        1,
        MISMATCH_ITEMS,
        MISMATCH_SUMMARY,
        ZONE_WORDS,
        reocr_mechanism="reverse-video-total",
    )
    assert fired is True
    payload = _invoke_payload(lambda_client)
    # Second rung of the reverse-video ladder, not a repeat of invert.
    assert payload["reocr_strategy"] == "plain"


def test_no_mechanism_climbs_unknown_ladder(monkeypatch):
    lambda_client = _setup(monkeypatch, prior_jobs=[_prior_job()])
    fired = line_item_processor._maybe_trigger_items_reocr(
        IMAGE_ID, 1, MISMATCH_ITEMS, MISMATCH_SUMMARY, ZONE_WORDS
    )
    assert fired is True
    payload = _invoke_payload(lambda_client)
    assert payload["reocr_strategy"] == "upscale2x"
    assert payload["reocr_mechanism"] is None


def test_attempt_cap_still_blocks(monkeypatch):
    lambda_client = _setup(
        monkeypatch, prior_jobs=[_prior_job(), _prior_job()]
    )
    fired = line_item_processor._maybe_trigger_items_reocr(
        IMAGE_ID,
        1,
        MISMATCH_ITEMS,
        MISMATCH_SUMMARY,
        ZONE_WORDS,
        reocr_mechanism="reverse-video-total",
    )
    assert fired is False
    lambda_client.invoke.assert_not_called()
