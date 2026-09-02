"""Retrying native #EMBEDDING item sweeper."""

from __future__ import annotations

from typing import Any

import pytest

from receipt_embeddings.service_limits import MAX_BATCH_WRITE_ITEMS
from receipt_embeddings.sweep import delete_native_embedding_items

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"
TABLE = "ReceiptsTable-dc5be22"


def _key(receipt_id: int, sk: str) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": sk},
    }


class SweepDynamo:
    def __init__(
        self,
        pages: list[list[dict[str, dict[str, str]]]],
        *,
        unprocessed_once: bool = False,
        always_unprocessed: bool = False,
    ) -> None:
        self.pages = list(pages)
        self.unprocessed_once = unprocessed_once
        self.always_unprocessed = always_unprocessed
        self.queries = 0
        self.writes: list[list[dict[str, Any]]] = []
        self.sleeps: list[float] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        items = (
            self.pages[self.queries] if self.queries < len(self.pages) else []
        )
        self.queries += 1
        response: dict[str, Any] = {"Items": items}
        if self.queries < len(self.pages):
            response["LastEvaluatedKey"] = {"SK": {"S": "continue"}}
        return response

    def batch_write_item(self, **kwargs: Any) -> dict[str, Any]:
        pending = kwargs["RequestItems"][TABLE]
        self.writes.append(pending)
        if self.always_unprocessed or (
            self.unprocessed_once and len(self.writes) == 1
        ):
            return {"UnprocessedItems": {TABLE: pending}}
        return {"UnprocessedItems": {}}


def test_sweeper_filters_embedding_sks_and_paginates() -> None:
    client = SweepDynamo(
        [
            [
                _key(1, "RECEIPT#00001#LINE#00001#EMBEDDING"),
                _key(1, "RECEIPT#00001#LINE#00001"),
            ],
            [_key(1, "RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING")],
        ]
    )
    deleted = delete_native_embedding_items(
        client, TABLE, IMAGE_ID, 1, sleep=client.sleeps.append
    )
    assert deleted == 2
    assert client.queries == 2
    assert len(client.writes) == 1
    sks = {req["DeleteRequest"]["Key"]["SK"]["S"] for req in client.writes[0]}
    assert sks == {
        "RECEIPT#00001#LINE#00001#EMBEDDING",
        "RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING",
    }


def test_sweeper_retries_unprocessed_items() -> None:
    client = SweepDynamo(
        [[_key(1, "RECEIPT#00001#LINE#00001#EMBEDDING")]],
        unprocessed_once=True,
    )
    deleted = delete_native_embedding_items(
        client, TABLE, IMAGE_ID, 1, sleep=client.sleeps.append
    )
    assert deleted == 1
    assert len(client.writes) == 2
    assert client.sleeps == [0.2]


def test_sweeper_raises_when_unprocessed_after_retries() -> None:
    client = SweepDynamo(
        [[_key(1, "RECEIPT#00001#LINE#00001#EMBEDDING")]],
        always_unprocessed=True,
    )
    with pytest.raises(RuntimeError, match="unprocessed after retries"):
        delete_native_embedding_items(
            client,
            TABLE,
            IMAGE_ID,
            1,
            max_retries=2,
            sleep=client.sleeps.append,
        )
    assert len(client.writes) == 2


def test_sweeper_chunks_at_batch_write_limit() -> None:
    keys = [
        _key(1, f"RECEIPT#00001#LINE#{index:05d}#EMBEDDING")
        for index in range(MAX_BATCH_WRITE_ITEMS + 1)
    ]
    client = SweepDynamo([keys])
    deleted = delete_native_embedding_items(
        client, TABLE, IMAGE_ID, 1, sleep=lambda _: None
    )
    assert deleted == MAX_BATCH_WRITE_ITEMS + 1
    assert len(client.writes) == 2
    assert len(client.writes[0]) == MAX_BATCH_WRITE_ITEMS
    assert len(client.writes[1]) == 1
