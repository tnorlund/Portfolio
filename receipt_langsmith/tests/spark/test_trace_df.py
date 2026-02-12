"""Tests for trace_df parquet read behavior."""

from __future__ import annotations

from typing import Any


class _FakeAnalysisException(Exception):
    pass


class _FakeRDD:
    def getNumPartitions(self) -> int:
        return 4


class _FakeDataFrame:
    def __init__(self) -> None:
        self.columns = ["id", "trace_id", "start_time", "end_time"]
        self.rdd = _FakeRDD()
        self.selected_columns: tuple[str, ...] | None = None

    def select(self, *columns: str) -> "_FakeDataFrame":
        self.selected_columns = tuple(columns)
        return self


class _FakeReader:
    def __init__(self, *, fail_plain: bool = False) -> None:
        self._options: dict[str, str] = {}
        self.fail_plain = fail_plain
        self.calls: list[dict[str, Any]] = []
        self.result_df = _FakeDataFrame()

    def option(self, name: str, value: str) -> "_FakeReader":
        self._options[name] = value
        return self

    def parquet(self, path: str) -> _FakeDataFrame:
        options = dict(self._options)
        self.calls.append({"path": path, "options": options})
        recursive = options.get("recursiveFileLookup") == "true"
        if self.fail_plain and not recursive:
            raise _FakeAnalysisException("simulated plain read failure")
        return self.result_df


class _FakeSparkSession:
    def __init__(self, reader: _FakeReader) -> None:
        self.read = reader


def test_read_parquet_df_retries_with_recursive_lookup(monkeypatch):
    import receipt_langsmith.spark.trace_df as trace_df_mod

    reader = _FakeReader(fail_plain=True)
    spark = _FakeSparkSession(reader)
    monkeypatch.setattr(trace_df_mod, "AnalysisException", _FakeAnalysisException)
    monkeypatch.setattr(
        trace_df_mod,
        "normalize_trace_df",
        lambda df, options: df,
    )
    monkeypatch.setattr(
        trace_df_mod,
        "trace_columns",
        lambda options: ["id", "trace_id"],
    )

    df = trace_df_mod.read_parquet_df(spark, "s3://bucket/traces/")

    assert df is reader.result_df
    assert len(reader.calls) == 2
    assert reader.calls[0]["path"] == "s3a://bucket/traces/"
    assert "recursiveFileLookup" not in reader.calls[0]["options"]
    assert reader.calls[1]["options"]["recursiveFileLookup"] == "true"
    assert reader.result_df.selected_columns == ("id", "trace_id")


def test_read_parquet_df_uses_standard_read_when_available(monkeypatch):
    import receipt_langsmith.spark.trace_df as trace_df_mod

    reader = _FakeReader(fail_plain=False)
    spark = _FakeSparkSession(reader)
    monkeypatch.setattr(trace_df_mod, "AnalysisException", _FakeAnalysisException)
    monkeypatch.setattr(
        trace_df_mod,
        "normalize_trace_df",
        lambda df, options: df,
    )
    monkeypatch.setattr(
        trace_df_mod,
        "trace_columns",
        lambda options: ["id", "trace_id"],
    )

    df = trace_df_mod.read_parquet_df(spark, "s3://bucket/traces/")

    assert df is reader.result_df
    assert len(reader.calls) == 1
    assert "recursiveFileLookup" not in reader.calls[0]["options"]
