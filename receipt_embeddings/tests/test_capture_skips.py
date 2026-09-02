"""Live-capture skip semantics: per-receipt failures never abort a run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.similarity_harness import capture_golden
from scripts.similarity_harness.capture_golden import (
    _classify_skip,
    _run_capture_loop,
    build_offline_bootstrap,
)
from scripts.similarity_harness.common import (
    LINE_INDEX,
    SCHEMA_VERSION,
    WORD_INDEX,
    receipt_key,
    validate_fixture,
)

from receipt_embeddings import ScoredItem


class _NotFound(Exception):
    """Stands in for receipt_dynamo's EntityNotFoundError."""


class _FakeSource:
    """Offline stand-in for ``_LiveCaptureSource`` with injectable faults."""

    _entity_not_found = _NotFound

    def __init__(
        self,
        *,
        missing_word_vectors: frozenset[str] = frozenset(),
        quota_error_receipts: frozenset[str] = frozenset(),
        missing_receipts: frozenset[str] = frozenset(),
    ) -> None:
        self._missing_word_vectors = missing_word_vectors
        self._quota_error_receipts = quota_error_receipts
        self._missing_receipts = missing_receipts
        self.dynamo = SimpleNamespace(
            get_receipt_details=self._get_receipt_details,
            get_receipt_sections_from_receipt=lambda image_id, receipt_id: [],
        )

    def _get_receipt_details(self, image_id: str, receipt_id: int) -> Any:
        if image_id in self._missing_receipts:
            raise _NotFound(f"receipt {image_id} does not exist")
        line = SimpleNamespace(line_id=1, text="STORE")
        word = SimpleNamespace(line_id=1, word_id=1, extracted_data=None)
        return SimpleNamespace(lines=[line], words=[word])

    def get(self, key: str, index: str) -> tuple[list[float], dict[str, Any]]:
        if index == WORD_INDEX and any(
            marker in key for marker in self._missing_word_vectors
        ):
            raise ValueError(f"Chroma has no vector for {key}")
        return [0.6, 0.8], {}

    def search(
        self, vector: Any, index: str, top_k: int
    ) -> tuple[list[ScoredItem], dict[str, list[float]], float]:
        if index == LINE_INDEX and self._quota_error_receipts:
            raise RuntimeError(
                "Quota exceeded: NumQueryEmbeddings above account limit"
            )
        prefix = "word-neighbor" if index == WORD_INDEX else "line-neighbor"
        items = [
            ScoredItem(
                key=f"{prefix}-{rank:05d}",
                distance=0.01 * rank,
                metadata={},
            )
            for rank in range(top_k)
        ]
        vectors = {item.key: [0.6, 0.8] for item in items}
        return items, vectors, 0.0


def _receipts(count: int) -> list[dict[str, Any]]:
    return [
        {
            "cohort": "test",
            "image_id": f"img-{position:02d}",
            "merchant_name": "",
            "receipt_id": 1,
        }
        for position in range(count)
    ]


def _group_rows(lines: list[Any]) -> list[list[Any]]:
    return [[line] for line in lines]


@pytest.mark.unit
def test_missing_vector_skips_one_receipt_and_run_continues() -> None:
    receipts = _receipts(3)
    source = _FakeSource(missing_word_vectors=frozenset({"IMAGE#img-01#"}))

    corpus, queries, rows, skips = _run_capture_loop(
        source, receipts, _group_rows
    )

    assert [row["key"] for row in rows] == [
        receipt_key("img-00", 1),
        receipt_key("img-02", 1),
    ]
    assert len(queries) == 6
    assert skips == [
        {
            "detail": (
                "Chroma has no vector for "
                "IMAGE#img-01#RECEIPT#00001#LINE#00001#WORD#00001"
            ),
            "key": receipt_key("img-01", 1),
            "reason": "missing_vector",
        }
    ]
    fixture = {
        "capture_parameters": {"distance": "cosine"},
        "corpus": sorted(corpus.values(), key=lambda value: value["key"]),
        "cost_model": {"read_request_usd_per_million": 0.125},
        "queries": sorted(queries, key=lambda query: str(query["query_id"])),
        "receipts": sorted(rows, key=lambda value: str(value["key"])),
        "schema_version": SCHEMA_VERSION,
        "source": {"backend": "test", "canonical": False},
    }
    validate_fixture(fixture, minimum_receipts=0)


@pytest.mark.unit
def test_quota_error_and_absent_receipt_are_classified_skips() -> None:
    receipts = _receipts(2)
    quota_source = _FakeSource(quota_error_receipts=frozenset({"img-00"}))
    _, _, rows, skips = _run_capture_loop(quota_source, receipts, _group_rows)
    assert rows == []
    assert [skip["reason"] for skip in skips] == [
        "chroma_quota_or_rate_limit",
        "chroma_quota_or_rate_limit",
    ]

    absent_source = _FakeSource(missing_receipts=frozenset({"img-00"}))
    _, _, rows, skips = _run_capture_loop(absent_source, receipts, _group_rows)
    assert [row["key"] for row in rows] == [receipt_key("img-01", 1)]
    assert [skip["reason"] for skip in skips] == ["receipt_not_found"]


@pytest.mark.unit
def test_classify_skip_buckets() -> None:
    assert (
        _classify_skip(ValueError("Chroma has no vector for X"), _NotFound)
        == "missing_vector"
    )
    assert (
        _classify_skip(RuntimeError("429 Too Many Requests"), _NotFound)
        == "chroma_quota_or_rate_limit"
    )
    assert _classify_skip(_NotFound("gone"), _NotFound) == "receipt_not_found"
    assert (
        _classify_skip(ValueError("receipt X has no rows or words"), _NotFound)
        == "incomplete_receipt_data"
    )
    assert _classify_skip(KeyError("boom"), _NotFound) == "error:KeyError"
    assert _classify_skip(KeyError("boom"), None) == "error:KeyError"


@pytest.mark.unit
def test_floor_failure_reports_skips_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CHROMA_CLOUD_API_KEY", "present")
    monkeypatch.setenv("CHROMA_CLOUD_TENANT", "present")
    monkeypatch.setenv("CHROMA_CLOUD_DATABASE", "receipt_dev")
    # 35 receipts: enough corpus for the 30-neighbor word queries, still
    # under the 40-receipt floor.
    small = build_offline_bootstrap(capture_golden._default_receipts()[:35])
    skips = [
        {"detail": "boom", "key": "a#00001", "reason": "missing_vector"},
        {"detail": "boom", "key": "b#00001", "reason": "missing_vector"},
        {"detail": "slow", "key": "c#00001", "reason": "receipt_not_found"},
    ]
    monkeypatch.setattr(
        capture_golden,
        "capture_live",
        lambda receipts, *, table_name, canonical: (small, skips),
    )
    out = tmp_path / "golden.json"

    assert capture_golden.main(["--out", str(out)]) == 1

    assert not out.exists()
    stderr = capsys.readouterr().err
    assert "skip report: 3 receipts skipped" in stderr
    assert "2 x missing_vector" in stderr
    assert "1 x receipt_not_found" in stderr
    assert "below the 40-receipt floor" in stderr
