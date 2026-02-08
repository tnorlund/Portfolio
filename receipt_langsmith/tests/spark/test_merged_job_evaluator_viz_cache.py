"""Unit tests for evaluator-viz-cache Spark shared-row flow."""

from __future__ import annotations

import json
from typing import Any

import pytest

from receipt_langsmith.spark.evaluator_diff_viz_cache import build_diff_cache
from receipt_langsmith.spark.evaluator_financial_math_viz_cache import (
    build_financial_math_cache,
)
from receipt_langsmith.spark.merged_job import run_evaluator_viz_cache


def test_financial_math_cache_accepts_preloaded_rows() -> None:
    """Financial helper should work without reading parquet from disk."""
    rows = [
        {
            "name": "ReceiptEvaluation",
            "trace_id": "trace-1",
            "extra": json.dumps(
                {
                    "metadata": {
                        "image_id": "img-1",
                        "receipt_id": 7,
                        "merchant_name": "Test Store",
                    }
                }
            ),
        },
        {
            "name": "financial_validation",
            "trace_id": "trace-1",
            "inputs": json.dumps(
                {
                    "image_id": "img-1",
                    "receipt_id": 7,
                    "merchant_name": "Test Store",
                    "visual_lines": [
                        {
                            "words": [
                                {
                                    "word": {
                                        "line_id": 1,
                                        "word_id": 2,
                                        "text": "12.34",
                                        "bounding_box": {
                                            "x": 0.1,
                                            "y": 0.2,
                                            "width": 0.3,
                                            "height": 0.4,
                                        },
                                    }
                                }
                            ]
                        }
                    ],
                }
            ),
            "outputs": json.dumps(
                {
                    "output": [
                        {
                            "issue": {
                                "line_id": 1,
                                "word_id": 2,
                                "word_text": "12.34",
                                "current_label": "LINE_TOTAL",
                                "description": "line total mismatch",
                                "issue_type": "LINE_ITEM_MISMATCH",
                                "expected_value": 12.34,
                                "actual_value": 11.99,
                                "difference": 0.35,
                            },
                            "llm_review": {
                                "decision": "INVALID",
                                "confidence": "HIGH",
                                "reasoning": "Mismatch",
                                "suggested_label": "LINE_TOTAL",
                            },
                        }
                    ]
                }
            ),
        },
    ]

    results = build_financial_math_cache(rows=rows)

    assert len(results) == 1
    assert results[0]["image_id"] == "img-1"
    assert results[0]["summary"]["total_equations"] == 1


def test_diff_cache_accepts_preloaded_rows() -> None:
    """Diff helper should support preloaded rows."""
    rows = [
        {
            "name": "ReceiptEvaluation",
            "trace_id": "trace-2",
            "extra": json.dumps(
                {
                    "metadata": {
                        "image_id": "img-2",
                        "receipt_id": 3,
                        "merchant_name": "Cafe",
                    }
                }
            ),
        },
        {
            "name": "currency_evaluation",
            "trace_id": "trace-2",
            "inputs": json.dumps(
                {
                    "visual_lines": [
                        {
                            "words": [
                                {
                                    "word": {
                                        "line_id": 4,
                                        "word_id": 5,
                                        "text": "4.50",
                                        "bounding_box": {
                                            "x": 1,
                                            "y": 2,
                                            "width": 3,
                                            "height": 4,
                                        },
                                    },
                                    "current_label": "LINE_TOTAL",
                                }
                            ]
                        }
                    ]
                }
            ),
            "outputs": json.dumps(
                {
                    "output": [
                        {
                            "issue": {"line_id": 4, "word_id": 5},
                            "llm_review": {
                                "decision": "INVALID",
                                "suggested_label": "TOTAL",
                                "confidence": "HIGH",
                                "reasoning": "Use TOTAL",
                            },
                        }
                    ]
                }
            ),
        },
    ]

    results = build_diff_cache(rows=rows)

    assert len(results) == 1
    assert results[0]["change_count"] == 1
    assert results[0]["words"][0]["after_label"] == "TOTAL"


class _FakeRow:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def asDict(self, recursive: bool = False) -> dict[str, Any]:
        del recursive
        return dict(self._data)


class _FakeDataFrame:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]
        self.columns = list(rows[0].keys()) if rows else []

    def filter(self, _expr: Any) -> "_FakeDataFrame":
        return self

    def withColumn(self, name: str, _expr: Any) -> "_FakeDataFrame":
        if name not in self.columns:
            self.columns.append(name)
        for row in self._rows:
            row.setdefault(name, None)
        return self

    def select(self, *columns: str) -> "_FakeDataFrame":
        selected = [
            {column: row.get(column) for column in columns}
            for row in self._rows
        ]
        return _FakeDataFrame(selected)

    def toLocalIterator(self):
        return iter([_FakeRow(row) for row in self._rows])


class _FakeReader:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.paths: list[str] = []
        self._df = _FakeDataFrame(rows)

    def parquet(self, path: str) -> _FakeDataFrame:
        self.paths.append(path)
        return self._df


class _FakeRDD:
    def __init__(self, data: list[tuple[str, str]]) -> None:
        self._data = data

    def foreachPartition(self, fn):
        fn(iter(self._data))


class _FakeSparkContext:
    def __init__(self) -> None:
        self.defaultParallelism = 4
        self.parallelize_calls: list[tuple[list[tuple[str, str]], int]] = []

    def parallelize(
        self, data: list[tuple[str, str]], numSlices: int
    ) -> _FakeRDD:
        records = list(data)
        self.parallelize_calls.append((records, numSlices))
        return _FakeRDD(records)


class _FakeSparkSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.read = _FakeReader(rows)
        self.sparkContext = _FakeSparkContext()


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)


class _FakeColExpr:
    def isin(self, *_values: str) -> bool:
        return True


class _FakeFunctions:
    @staticmethod
    def col(_name: str) -> _FakeColExpr:
        return _FakeColExpr()

    @staticmethod
    def lit(value: Any) -> Any:
        return value


def test_run_evaluator_cache_reuses_shared_rows(monkeypatch: pytest.MonkeyPatch):
    """Merged job should read parquet once and reuse rows for all helpers."""
    import receipt_langsmith.spark.evaluator_dedup_viz_cache as dedup_mod
    import receipt_langsmith.spark.evaluator_diff_viz_cache as diff_mod
    import receipt_langsmith.spark.evaluator_evidence_viz_cache as evidence_mod
    import receipt_langsmith.spark.evaluator_financial_math_viz_cache as fm_mod
    import receipt_langsmith.spark.evaluator_journey_viz_cache as journey_mod
    import receipt_langsmith.spark.evaluator_patterns_viz_cache as patterns_mod
    import receipt_langsmith.spark.merged_job as merged_job_mod

    trace_rows = [
        {
            "id": "root-1",
            "trace_id": "trace-1",
            "parent_run_id": None,
            "is_root": True,
            "name": "ReceiptEvaluation",
            "inputs": "{}",
            "outputs": "{}",
            "extra": "{}",
            "start_time": "2025-01-01T00:00:00",
            "end_time": "2025-01-01T00:00:01",
        }
    ]

    spark = _FakeSparkSession(trace_rows)
    s3_client = _FakeS3Client()
    captured_rows: dict[str, list[dict[str, Any]] | None] = {}

    def _helper_factory(prefix: str, merchant_keyed: bool):
        def _helper(
            parquet_dir: str | None = None,
            *,
            rows: list[dict[str, Any]] | None = None,
        ) -> list[dict[str, Any]]:
            del parquet_dir
            captured_rows[prefix] = rows
            if merchant_keyed:
                return [{"merchant_name": "Test Merchant"}]
            return [{"image_id": "img-1", "receipt_id": 1}]

        return _helper

    monkeypatch.setattr(
        fm_mod,
        "build_financial_math_cache",
        _helper_factory("financial-math", False),
    )
    monkeypatch.setattr(
        diff_mod,
        "build_diff_cache",
        _helper_factory("diff", False),
    )
    monkeypatch.setattr(
        journey_mod,
        "build_journey_cache",
        _helper_factory("journey", False),
    )
    monkeypatch.setattr(
        patterns_mod,
        "build_patterns_cache",
        _helper_factory("patterns", True),
    )
    monkeypatch.setattr(
        evidence_mod,
        "build_evidence_cache",
        _helper_factory("evidence", False),
    )
    monkeypatch.setattr(
        dedup_mod,
        "build_dedup_cache",
        _helper_factory("dedup", False),
    )
    monkeypatch.setattr(
        merged_job_mod.boto3,
        "client",
        lambda _service_name: s3_client,
    )
    monkeypatch.setattr(merged_job_mod, "F", _FakeFunctions())

    run_evaluator_viz_cache(
        spark=spark,
        parquet_dir="s3://input/traces/",
        cache_bucket="cache-bucket",
        execution_id="exec-1",
    )

    assert len(spark.read.paths) == 1
    assert len(captured_rows) == 6
    first_rows = next(iter(captured_rows.values()))
    assert first_rows is not None
    for helper_rows in captured_rows.values():
        assert helper_rows is first_rows

    assert len(spark.sparkContext.parallelize_calls) == 6
    keys = {put["Key"] for put in s3_client.puts}
    assert "financial-math/img-1_1.json" in keys
    assert "patterns/test-merchant.json" in keys
    for prefix in (
        "financial-math",
        "diff",
        "journey",
        "patterns",
        "evidence",
        "dedup",
    ):
        assert f"{prefix}/metadata.json" in keys
