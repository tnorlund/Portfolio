"""Tests for label_validation_viz_cache_helpers behavior."""

from __future__ import annotations

from typing import Any

from receipt_langsmith.spark import (
    label_validation_viz_cache_helpers as helpers,
)

# Test doubles intentionally expose minimal Spark-like interfaces.
# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-few-public-methods,protected-access


def test_read_traces_uses_s3a_for_spark(monkeypatch) -> None:
    """EMR Spark 8 reads persistent S3 data through the S3A connector."""

    class FakeDataFrame:
        columns = ("id",)

        def select(self, *_columns: str) -> "FakeDataFrame":
            return self

        def count(self) -> int:
            return 1

    class FakeReader:
        path: str | None = None

        def parquet(self, path: str) -> FakeDataFrame:
            self.path = path
            return FakeDataFrame()

    class FakeSpark:
        read = FakeReader()

    monkeypatch.setattr(
        helpers,
        "normalize_trace_df",
        lambda dataframe, _options: dataframe,
    )
    monkeypatch.setattr(helpers, "trace_columns", lambda _options: ["id"])

    helpers.read_traces(FakeSpark(), "s3://exports/traces/")

    assert FakeSpark.read.path == "s3a://exports/traces/"


def test_build_viz_receipts_scans_until_max_buildable(
    monkeypatch,
) -> None:
    """Do not stop at first N roots; stop after N buildable receipts."""
    root_traces = [
        {"trace_id": "trace-1"},
        {"trace_id": "trace-2"},
        {"trace_id": "trace-3"},
    ]
    called_trace_ids: list[str] = []

    monkeypatch.setattr(
        helpers,
        "extract_receipt_traces",
        lambda _df: root_traces,
    )
    monkeypatch.setattr(
        helpers,
        "extract_validation_traces",
        lambda _df, _trace_ids: {},
    )

    def fake_build_viz_receipt(
        root_trace: dict[str, Any],
        validation_traces: list[dict[str, Any]],
        receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    ) -> dict[str, Any] | None:
        del validation_traces, receipt_lookup
        called_trace_ids.append(str(root_trace["trace_id"]))
        if root_trace["trace_id"] == "trace-3":
            return {"trace_id": "trace-3"}
        return None

    monkeypatch.setattr(helpers, "build_viz_receipt", fake_build_viz_receipt)

    receipts = helpers._build_viz_receipts(
        df=object(),
        receipt_lookup={},
        max_receipts=1,
    )

    assert receipts == [{"trace_id": "trace-3"}]
    assert called_trace_ids == ["trace-1", "trace-2", "trace-3"]
