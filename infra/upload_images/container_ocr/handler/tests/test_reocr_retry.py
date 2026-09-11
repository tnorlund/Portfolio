"""Failure injection at the OCR queue and regional correction boundaries."""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from handler import handler as handler_module
from handler import ocr_processor as ocr_module
from handler.tests.test_overlay import (
    _IMG_ID,
    _make_label,
    _make_letter,
    _make_line,
    _make_processor,
    _make_word,
)
from receipt_dynamo.constants import OCRJobType
from receipt_upload.merchant_resolution import dynamo_embedding_write


@dataclass
class OverlayStore:
    """Durable copies, so failed calls cannot mutate subsequent reads."""

    words: list[Any]
    lines: list[Any]
    letters: list[Any] = field(default_factory=list)
    labels: list[Any] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    completed: bool = False
    owner: str | None = None

    def replace(self, attribute: str, values: list[Any]) -> None:
        """Persist by entity key without sharing the caller's objects."""
        entities = {
            entity.key["SK"]["S"]: entity
            for entity in getattr(self, attribute)
        }
        entities.update(
            {entity.key["SK"]["S"]: copy.deepcopy(entity) for entity in values}
        )
        setattr(self, attribute, list(entities.values()))

    def remove(self, attribute: str, values: list[Any]) -> None:
        """Delete only supplied keys, including already-deleted keys."""
        keys = {entity.key["SK"]["S"] for entity in values}
        setattr(
            self,
            attribute,
            [
                entity
                for entity in getattr(self, attribute)
                if entity.key["SK"]["S"] not in keys
            ],
        )


@pytest.fixture(name="overlay")
def _overlay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """One labeled price correction with a real durable read/write boundary."""
    monkeypatch.setattr("boto3.client", MagicMock(return_value=MagicMock()))
    processor = _make_processor()
    store = OverlayStore(
        words=[_make_word(text="8.99", x=0.75, word_id=1)],
        lines=[_make_line(text="8.99")],
        letters=[_make_letter(text="8", x=0.75)],
        labels=[_make_label()],
    )
    job = SimpleNamespace(
        image_id=_IMG_ID,
        receipt_id=1,
        job_id="job-1",
        job_type=OCRJobType.REGIONAL_REOCR.value,
        reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
        status="PENDING",
        s3_bucket="bucket",
        s3_key="input.json",
    )
    routing = SimpleNamespace(
        status="PENDING", s3_bucket="bucket", s3_key="result.json"
    )
    payload = tmp_path / "result.json"
    payload.write_text('{"lines": []}', encoding="utf-8")
    monkeypatch.setattr(
        ocr_module, "get_ocr_job", lambda *_args: copy.deepcopy(job)
    )
    monkeypatch.setattr(
        ocr_module,
        "get_ocr_routing_decision",
        lambda *_args: copy.deepcopy(routing),
    )
    monkeypatch.setattr(
        ocr_module, "download_file_from_s3", lambda *_args: payload
    )
    monkeypatch.setattr(
        ocr_module, "process_ocr_dict_as_image", lambda *_args: ([], [], [])
    )
    monkeypatch.setattr(
        ocr_module,
        "image_ocr_to_receipt_ocr",
        lambda **_kwargs: (
            [_make_line(text="9.99")],
            [_make_word(text="9.99", x=1 / 6, w=1 / 3)],
            [_make_letter(text="9", x=1 / 6)],
        ),
    )
    dynamo = processor.dynamo
    dynamo.list_receipt_words_from_receipt.side_effect = (
        lambda *_args: copy.deepcopy(store.words)
    )
    dynamo.list_receipt_lines_from_receipt.side_effect = (
        lambda *_args: copy.deepcopy(store.lines)
    )
    dynamo.list_receipt_word_labels_for_receipt.side_effect = (
        lambda **_kwargs: (copy.deepcopy(store.labels), None)
    )
    dynamo.list_receipt_letters_from_word.side_effect = (
        lambda **_kwargs: copy.deepcopy(store.letters)
    )
    dynamo.get_receipt_details.side_effect = (
        lambda *_args, **_kwargs: SimpleNamespace(
            receipt=dynamo.get_receipt.return_value,
            words=copy.deepcopy(store.words),
            lines=copy.deepcopy(store.lines),
            letters=copy.deepcopy(store.letters),
            labels=copy.deepcopy(store.labels),
            place=None,
        )
    )
    for method, attribute in (
        ("update_receipt_words", "words"),
        ("add_receipt_words", "words"),
        ("update_receipt_lines", "lines"),
        ("put_receipt_letters", "letters"),
        ("update_receipt_word_labels", "labels"),
    ):
        getattr(dynamo, method).side_effect = (
            lambda values, attr=attribute: store.replace(attr, values)
        )
    for method, attribute in (
        ("remove_receipt_letters", "letters"),
        ("delete_receipt_words", "words"),
        ("delete_receipt_word_labels", "labels"),
    ):
        getattr(dynamo, method).side_effect = (
            lambda values, attr=attribute: store.remove(attr, values)
        )

    def claim(_image: str, _job: str, owner: str, **_kwargs: Any) -> str:
        if store.completed:
            return "completed"
        if store.owner is not None:
            return "busy"
        store.owner = owner
        return "claimed"

    def release(_image: str, _job: str, owner: str) -> bool:
        if store.owner != owner:
            return False
        store.owner = None
        return True

    def persist_routing(value: Any, owner: str) -> None:
        assert store.owner == owner
        store.statuses.append(value.status)
        routing.status = value.status
        store.completed = value.status == "COMPLETED"
        store.owner = None

    dynamo.claim_ocr_routing_decision.side_effect = claim
    dynamo.release_ocr_routing_decision.side_effect = release
    dynamo.complete_ocr_routing_decision.side_effect = persist_routing
    writer = MagicMock(return_value={"requests": 1, "written": 1, "failed": 0})
    monkeypatch.setattr(
        dynamo_embedding_write, "write_native_embeddings", writer
    )
    monkeypatch.setenv("RECEIPT_SUMMARY_QUEUE_URL", "https://sqs.test/summary")
    return SimpleNamespace(
        processor=processor,
        store=store,
        job=job,
        routing=routing,
        writer=writer,
    )


def run_overlay(overlay: Any) -> dict[str, Any]:
    """Drive the production entry point, including error-to-result conversion."""
    return overlay.processor.process_ocr_job(_IMG_ID, "job-1")


@pytest.mark.parametrize(
    "failure", [RuntimeError("native offline"), {"failed": 1}]
)
def test_native_failure_never_records_completion(
    overlay: Any, failure: Any
) -> None:
    """Exhausted refresh attempts never publish either completion marker."""
    overlay.writer.side_effect = (
        failure if isinstance(failure, Exception) else None
    )
    if isinstance(failure, dict):
        overlay.writer.return_value = failure
    assert run_overlay(overlay)["success"] is False
    assert overlay.writer.call_count == 3
    assert "COMPLETED" not in overlay.store.statuses
    overlay.processor.dynamo.update_ocr_job.assert_not_called()


def test_summary_failure_redrives_then_duplicate_is_noop(
    overlay: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable summary queue retries before acknowledging completion."""
    sqs = MagicMock()
    sqs.send_message.side_effect = RuntimeError("queue unavailable")
    monkeypatch.setattr("boto3.client", lambda *_args: sqs)
    assert run_overlay(overlay)["success"] is False
    assert not overlay.store.completed
    sqs.send_message.side_effect = None
    assert run_overlay(overlay)["success"] is True
    assert overlay.store.completed
    call_count = overlay.writer.call_count
    assert run_overlay(overlay)["skipped"] is True
    assert overlay.writer.call_count == call_count
    assert sqs.send_message.call_count == 2


def test_label_write_failure_does_not_publish_changed_word(
    overlay: Any,
) -> None:
    """A label failure cannot erase the evidence needed for revalidation."""
    dynamo = overlay.processor.dynamo
    dynamo.update_receipt_word_labels.side_effect = RuntimeError(
        "label write failed"
    )
    assert run_overlay(overlay)["success"] is False
    assert overlay.store.words[0].text == "8.99"
    dynamo.update_receipt_word_labels.side_effect = (
        lambda values: overlay.store.replace("labels", values)
    )
    assert run_overlay(overlay)["success"] is True
    assert overlay.store.words[0].text == "9.99"
    assert overlay.store.labels[0].validation_status == "PENDING"


def test_deleted_orphan_line_is_repaired_on_redelivery(
    overlay: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted words still repair their stale line on the next attempt."""
    overlay.store.words = [
        _make_word(text="KEEP", x=0.1, word_id=1),
        _make_word(text="STALE", x=0.8, line_id=2, word_id=1),
    ]
    overlay.store.lines = [
        _make_line(text="KEEP", line_id=1),
        _make_line(text="STALE", line_id=2),
    ]
    overlay.store.labels = []
    monkeypatch.setattr(
        ocr_module, "image_ocr_to_receipt_ocr", lambda **_kwargs: ([], [], [])
    )
    dynamo = overlay.processor.dynamo
    dynamo.update_receipt_lines.side_effect = RuntimeError("line write failed")
    assert run_overlay(overlay)["success"] is False
    assert [word.text for word in overlay.store.words] == ["KEEP"]
    dynamo.update_receipt_lines.side_effect = (
        lambda values: overlay.store.replace("lines", values)
    )
    assert run_overlay(overlay)["success"] is True
    assert [line.text for line in overlay.store.lines] == ["KEEP", ""]


def test_partial_letter_replacement_is_repaired_on_redelivery(
    overlay: Any,
) -> None:
    """A retry restores letters after the old letters were already removed."""
    dynamo = overlay.processor.dynamo
    dynamo.put_receipt_letters.side_effect = RuntimeError(
        "letters unavailable"
    )
    assert run_overlay(overlay)["success"] is False
    assert overlay.store.letters == []
    dynamo.put_receipt_letters.side_effect = (
        lambda values: overlay.store.replace("letters", values)
    )
    assert run_overlay(overlay)["success"] is True
    assert [letter.text for letter in overlay.store.letters] == ["9"]
    assert overlay.store.lines[0].text == "9.99"


def test_native_refresh_uses_updated_entities_not_lagging_indexes(
    overlay: Any,
) -> None:
    """Index lag cannot regenerate vectors from the original OCR text."""
    dynamo = overlay.processor.dynamo
    dynamo.list_receipt_words_from_receipt.side_effect = None
    dynamo.list_receipt_words_from_receipt.return_value = [
        _make_word(text="8.99", x=0.75)
    ]
    dynamo.list_receipt_lines_from_receipt.side_effect = lambda *_args: [
        _make_line(text="8.99")
    ]
    assert run_overlay(overlay)["success"] is True
    values = overlay.writer.call_args.kwargs
    assert [word.text for word in values["words"]] == ["9.99"]
    assert [line.text for line in values["lines"]] == ["9.99"]


def test_ocr_mixed_batch_redrives_false_results_and_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only unsuccessful records are returned to SQS for redelivery."""
    monkeypatch.setattr(handler_module, "flush_traces", lambda: None)
    processor = MagicMock(
        side_effect=[
            {"success": True},
            {"success": False, "error": "native incomplete"},
            RuntimeError("dependency unavailable"),
        ]
    )
    monkeypatch.setattr(handler_module, "_process_single_record", processor)
    records = [
        {
            "messageId": identifier,
            "body": json.dumps({"job_id": identifier, "image_id": _IMG_ID}),
        }
        for identifier in ("success", "incomplete", "exception")
    ]
    response = handler_module.lambda_handler({"Records": records}, None)
    assert response["batchItemFailures"] == [
        {"itemIdentifier": "incomplete"},
        {"itemIdentifier": "exception"},
    ]
    assert len(json.loads(response["body"])["results"]) == 3


def test_failed_record_without_message_id_fails_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreportable record fails the invocation instead of being lost."""
    monkeypatch.setattr(
        handler_module,
        "_process_single_record",
        lambda *_args: {"success": False},
    )
    with pytest.raises(ValueError, match="messageId"):
        handler_module.lambda_handler(
            {"Records": [{"body": '{"job_id": "bad"}'}]}, None
        )


def test_overlapping_delivery_cannot_sweep_or_complete_another_attempt(
    overlay: Any,
) -> None:
    """An overlapping redelivery cannot touch the active native refresh."""
    loser_results = []

    def refresh(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        loser_results.append(run_overlay(overlay))
        return {"requests": 1, "written": 1, "failed": 0}

    overlay.writer.side_effect = refresh
    assert run_overlay(overlay)["success"] is True
    assert [result["success"] for result in loser_results] == [False]
    assert overlay.writer.call_count == 1
    assert overlay.store.statuses == ["COMPLETED"]
    overlay.processor.dynamo.release_ocr_routing_decision.assert_not_called()
    assert run_overlay(overlay)["skipped"] is True


def test_retry_repairs_line_after_deleting_last_receipt_word(
    overlay: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt emptied by the prior attempt can still finish its cleanup."""
    monkeypatch.setattr(
        ocr_module, "image_ocr_to_receipt_ocr", lambda **_kwargs: ([], [], [])
    )
    dynamo = overlay.processor.dynamo
    dynamo.update_receipt_lines.side_effect = RuntimeError("line write failed")
    assert run_overlay(overlay)["success"] is False
    assert overlay.store.words == []
    dynamo.update_receipt_lines.side_effect = (
        lambda values: overlay.store.replace("lines", values)
    )
    assert run_overlay(overlay)["success"] is True
    assert overlay.store.lines[0].text == ""
    assert overlay.store.labels == []
    assert overlay.store.letters == []


def test_consistent_read_failure_does_not_refresh_or_complete(
    overlay: Any,
) -> None:
    """A failed prerequisite read releases ownership without refreshing."""
    dynamo = overlay.processor.dynamo
    dynamo.get_receipt_details.side_effect = RuntimeError(
        "snapshot unavailable"
    )
    assert run_overlay(overlay)["success"] is False
    overlay.writer.assert_not_called()
    dynamo.complete_ocr_routing_decision.assert_not_called()
    assert overlay.store.owner is None


def test_partial_addition_does_not_duplicate_words_on_redelivery(
    overlay: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted subset of new words is matched again rather than copied."""
    overlay.store.words = [_make_word(text="ITEM", x=0.1)]
    overlay.store.lines = [_make_line(text="ITEM")]
    overlay.store.labels = []
    overlay.store.letters = []
    monkeypatch.setattr(
        ocr_module,
        "image_ocr_to_receipt_ocr",
        lambda **_kwargs: (
            [],
            [
                _make_word(text="2", x=0.1, w=0.1),
                _make_word(text="9.99", x=0.7, w=0.2, word_id=2),
            ],
            [],
        ),
    )

    def partial_write(values: list[Any]) -> None:
        overlay.store.replace("words", values[:1])
        raise RuntimeError("second word unavailable")

    dynamo = overlay.processor.dynamo
    dynamo.add_receipt_words.side_effect = partial_write
    assert run_overlay(overlay)["success"] is False
    assert [word.text for word in overlay.store.words] == ["ITEM", "2"]
    dynamo.add_receipt_words.side_effect = (
        lambda values: overlay.store.replace("words", values)
    )
    assert run_overlay(overlay)["success"] is True
    assert [word.text for word in overlay.store.words] == ["ITEM", "2", "9.99"]
    assert overlay.store.lines[0].text == "ITEM 2 9.99"
