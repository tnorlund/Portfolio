"""Offline idempotency and failure-isolation tests for the embedding writer."""

from __future__ import annotations

from typing import Any

import pytest
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

from receipt_embeddings import EmbeddingWriter, EmbeddingWriteRequest

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"
TABLE = "ReceiptsTable-dc5be22"


class MemoryDynamo:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(value: dict[str, Any]) -> str:
        return f"{value['PK']['S']}#{value['SK']['S']}"

    def batch_get_item(self, **kwargs: Any) -> dict[str, Any]:
        keys = kwargs["RequestItems"][TABLE]["Keys"]
        return {
            "Responses": {
                TABLE: [
                    {
                        "PK": self.items[self._key(key)]["PK"],
                        "SK": self.items[self._key(key)]["SK"],
                    }
                    for key in keys
                    if self._key(key) in self.items
                ]
            }
        }

    def batch_write_item(self, **kwargs: Any) -> dict[str, Any]:
        for request in kwargs["RequestItems"][TABLE]:
            item = request["PutRequest"]["Item"]
            self.items[self._key(item)] = item
        return {}


def _line(text: str = "COFFEE", *, line_id: int = 2) -> EmbeddingWriteRequest:
    return EmbeddingWriteRequest(
        kind="line",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        text=text,
        embedding_input=f"<EDGE>\n{text}\n<EDGE>",
        row_line_ids=(line_id,),
    )


@pytest.mark.unit
def test_realtime_embed_then_second_run_writes_nothing() -> None:
    dynamo = MemoryDynamo()
    calls: list[str] = []

    def embedder(**kwargs: Any) -> list[list[float]]:
        calls.append(kwargs["texts"][0])
        return [[0.01] * EMBEDDING_DIMENSIONS]

    writer = EmbeddingWriter(dynamo, TABLE, embedder=embedder)
    first = writer.write([_line()])
    second = writer.write([_line()])

    assert first.written == 1
    assert second.written == 0
    assert len(second.skipped_existing_keys) == 1
    assert calls == ["<EDGE>\nCOFFEE\n<EDGE>"]


@pytest.mark.unit
def test_one_embedding_failure_does_not_abort_healthy_item() -> None:
    dynamo = MemoryDynamo()

    def embedder(**kwargs: Any) -> list[list[float]]:
        text = kwargs["texts"][0]
        if "BAD" in text:
            raise RuntimeError("embedding unavailable")
        return [[0.01] * EMBEDDING_DIMENSIONS]

    writer = EmbeddingWriter(dynamo, TABLE, embedder=embedder)
    report = writer.write([_line("GOOD"), _line("BAD", line_id=3)])

    assert report.written == 1
    assert len(report.failures) == 1
    assert report.failures[0].stage == "embed"


@pytest.mark.unit
def test_duplicate_request_is_skip_and_report() -> None:
    dynamo = MemoryDynamo()
    writer = EmbeddingWriter(
        dynamo,
        TABLE,
        embedder=lambda **_kwargs: [[0.01] * EMBEDDING_DIMENSIONS],
    )

    report = writer.write([_line(), _line()])

    assert report.written == 1
    assert len(report.failures) == 1
    assert report.failures[0].stage == "validate"


class ThrottlingDynamo(MemoryDynamo):
    """Batched writes throttle; single-item retries throttle only for the
    poisoned key, so healthy items still land."""

    def __init__(self, poisoned_sk: str) -> None:
        super().__init__()
        self._poisoned_sk = poisoned_sk

    def batch_write_item(self, **kwargs: Any) -> dict[str, Any]:
        requests = kwargs["RequestItems"][TABLE]
        if len(requests) > 1:
            raise RuntimeError("ProvisionedThroughputExceededException")
        item = requests[0]["PutRequest"]["Item"]
        if item["SK"]["S"] == self._poisoned_sk:
            raise RuntimeError("ProvisionedThroughputExceededException")
        return super().batch_write_item(**kwargs)


@pytest.mark.unit
def test_write_throttle_isolates_items_and_keeps_healthy_writes() -> None:
    dynamo = ThrottlingDynamo("RECEIPT#00001#LINE#00003#EMBEDDING")
    writer = EmbeddingWriter(
        dynamo,
        TABLE,
        embedder=lambda **_kwargs: [[0.01] * EMBEDDING_DIMENSIONS],
        sleep=lambda _: None,
    )

    report = writer.write([_line("GOOD"), _line("BAD", line_id=3)])

    assert report.written == 1
    assert len(report.failures) == 1
    assert report.failures[0].stage == "write"
    stored_sks = {item["SK"]["S"] for item in dynamo.items.values()}
    assert stored_sks == {"RECEIPT#00001#LINE#00002#EMBEDDING"}


@pytest.mark.unit
def test_global_write_outage_reports_every_item_and_writes_nothing() -> None:
    """The writer never raises on a total outage: every item becomes an
    attributable failure with zero writes — the exact report shape the
    backfill's fail-closed exit keys on."""

    class DeadDynamo(MemoryDynamo):
        def batch_write_item(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("endpoint unreachable")

    dynamo = DeadDynamo()
    writer = EmbeddingWriter(
        dynamo,
        TABLE,
        embedder=lambda **_kwargs: [[0.01] * EMBEDDING_DIMENSIONS],
        sleep=lambda _: None,
    )

    report = writer.write([_line("A"), _line("B", line_id=3)])

    assert report.written == 0
    assert len(report.failures) == 2
    assert {failure.stage for failure in report.failures} == {"write"}
    assert dynamo.items == {}


@pytest.mark.unit
def test_line_anchor_fields_flow_through_writer_to_stored_item() -> None:
    dynamo = MemoryDynamo()
    writer = EmbeddingWriter(
        dynamo,
        TABLE,
        embedder=lambda **_kwargs: [[0.01] * EMBEDDING_DIMENSIONS],
    )
    request = EmbeddingWriteRequest(
        kind="line",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="CALL 555-123-4567",
        row_line_ids=(2,),
        normalized_phone_10="5551234567",
        normalized_full_address="123 MAIN ST HENDERSON NV 89014",
    )

    report = writer.write([request])

    assert report.written == 1
    stored = next(iter(dynamo.items.values()))
    assert stored["normalized_phone_10"] == {"S": "5551234567"}
    assert stored["normalized_full_address"] == {
        "S": "123 MAIN ST HENDERSON NV 89014"
    }
