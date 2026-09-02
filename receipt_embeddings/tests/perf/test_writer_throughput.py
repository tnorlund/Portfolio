"""Writer throughput vs batch size against moto; pin 25/100 chunking.

Does not assert wall-clock thresholds (moto latency is not production).
The live writer is exercised with a stub embedder / known vectors so
this never calls OpenAI.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from receipt_embeddings import EmbeddingWriter, EmbeddingWriteRequest
from receipt_embeddings.service_limits import (
    MAX_BATCH_GET_ITEMS,
    MAX_BATCH_WRITE_ITEMS,
)

from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

pytestmark = pytest.mark.performance

TABLE = "ReceiptsTable-dc5be22"
IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"
STUB_VECTOR = [0.001] * EMBEDDING_DIMENSIONS


class CountingDynamo:
    """Wrap a low-level client and record BatchGet/Write chunk sizes."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.get_chunk_sizes: list[int] = []
        self.write_chunk_sizes: list[int] = []

    def batch_get_item(self, **kwargs: Any) -> Mapping[str, Any]:
        table = next(iter(kwargs["RequestItems"]))
        keys = kwargs["RequestItems"][table]["Keys"]
        self.get_chunk_sizes.append(len(keys))
        return self._inner.batch_get_item(**kwargs)

    def batch_write_item(self, **kwargs: Any) -> Mapping[str, Any]:
        table = next(iter(kwargs["RequestItems"]))
        self.write_chunk_sizes.append(len(kwargs["RequestItems"][table]))
        return self._inner.batch_write_item(**kwargs)


def _requests(count: int, *, start: int = 0) -> list[EmbeddingWriteRequest]:
    return [
        EmbeddingWriteRequest(
            kind="line",
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=start + offset,
            text=f"LINE {start + offset}",
            embedding_input=f"<EDGE>\nLINE {start + offset}\n<EDGE>",
            row_line_ids=(start + offset,),
            vector=STUB_VECTOR,
        )
        for offset in range(count)
    ]


def _expected_chunks(total: int, limit: int) -> int:
    return math.ceil(total / limit) if total else 0


def test_writer_batch_get_and_write_chunk_at_service_limits(
    receipts_client: Any,
) -> None:
    """80 keys fit one BatchGet; 101 keys need two. Writes cap at 25."""

    counting = CountingDynamo(receipts_client)
    writer = EmbeddingWriter(
        counting, TABLE, embedder=lambda **_k: [], sleep=lambda _s: None
    )

    first = writer.write(_requests(80))
    assert first.written == 80
    assert first.failures == []
    assert counting.get_chunk_sizes == [80]
    assert counting.write_chunk_sizes == [25, 25, 25, 5]
    assert max(counting.write_chunk_sizes) == MAX_BATCH_WRITE_ITEMS

    counting.get_chunk_sizes.clear()
    counting.write_chunk_sizes.clear()
    second = writer.write(_requests(80))
    assert second.written == 0
    assert len(second.skipped_existing_keys) == 80
    assert counting.get_chunk_sizes == [80]
    assert counting.write_chunk_sizes == []

    counting.get_chunk_sizes.clear()
    counting.write_chunk_sizes.clear()
    extra = writer.write(_requests(101, start=80))
    assert extra.written == 101
    assert counting.get_chunk_sizes == [100, 1]
    assert max(counting.get_chunk_sizes) == MAX_BATCH_GET_ITEMS
    assert counting.write_chunk_sizes == [25, 25, 25, 25, 1]
    assert all(
        size <= MAX_BATCH_WRITE_ITEMS for size in counting.write_chunk_sizes
    )


def test_writer_throughput_vs_batch_size_against_moto(
    receipts_client: Any,
) -> None:
    """Record items/sec for caller batch sizes; chunking stays in bounds."""

    sizes: Sequence[int] = (1, 10, 25, 50)
    rates: dict[int, float] = {}
    counting = CountingDynamo(receipts_client)
    writer = EmbeddingWriter(
        counting, TABLE, embedder=lambda **_k: [], sleep=lambda _s: None
    )
    start_id = 0
    for size in sizes:
        counting.get_chunk_sizes.clear()
        counting.write_chunk_sizes.clear()
        begun = time.perf_counter()
        report = writer.write(_requests(size, start=start_id))
        elapsed = time.perf_counter() - begun
        start_id += size
        assert report.written == size
        assert report.failures == []
        assert counting.get_chunk_sizes == [size]
        assert len(counting.write_chunk_sizes) == _expected_chunks(
            size, MAX_BATCH_WRITE_ITEMS
        )
        assert all(
            chunk <= MAX_BATCH_WRITE_ITEMS
            for chunk in counting.write_chunk_sizes
        )
        rates[size] = size / elapsed if elapsed else float("inf")

    # Informative only — moto is not a latency SLO.
    assert rates[1] > 0
    print(
        "writer items/sec vs caller batch size (moto, stub vectors): "
        + ", ".join(f"{size}={rates[size]:.1f}" for size in sizes)
    )
