"""Tests for qa_viz_cache_helpers tool-expansion logic."""

from __future__ import annotations

import json
from typing import Any

import pytest

from receipt_langsmith.spark.qa_viz_cache_helpers import (
    _classify_run,
    build_question_cache,
    compute_stats,
    derive_trace_steps,
)


# ---------------------------------------------------------------------------
# Helpers to build fake run dicts
# ---------------------------------------------------------------------------

def _make_run(
    *,
    id: str = "run-1",
    name: str = "some_node",
    run_type: str = "chain",
    parent_run_id: str = "",
    dotted_order: str = "",
    inputs: dict | str | None = None,
    outputs: dict | str | None = None,
    start_time: str = "2025-01-01 00:00:00",
    end_time: str = "2025-01-01 00:00:01",
    total_tokens: int = 0,
    is_root: bool = False,
) -> dict:
    return {
        "id": id,
        "name": name,
        "run_type": run_type,
        "parent_run_id": parent_run_id,
        "dotted_order": dotted_order,
        "inputs": inputs,
        "outputs": outputs,
        "start_time": start_time,
        "end_time": end_time,
        "total_tokens": total_tokens,
        "is_root": is_root,
    }


# ---------------------------------------------------------------------------
# Test: derive_trace_steps expands ToolNode wrapper children
# ---------------------------------------------------------------------------

class TestDeriveTraceStepsExpandsToolChildren:
    """A depth-1 'tools' wrapper (run_type='chain') with two depth-2 tool runs
    should produce two separate 'tools' steps."""

    def setup_method(self):
        self.wrapper = _make_run(
            id="wrapper-1",
            name="tools",
            run_type="chain",
            parent_run_id="root-1",
            dotted_order="1.1",
        )
        self.tool_a = _make_run(
            id="tool-a",
            name="search_receipts",
            run_type="tool",
            parent_run_id="wrapper-1",
            dotted_order="1.1.1",
            inputs={"query": "coffee"},
            start_time="2025-01-01 00:00:00",
            end_time="2025-01-01 00:00:02",
        )
        self.tool_b = _make_run(
            id="tool-b",
            name="get_receipt",
            run_type="tool",
            parent_run_id="wrapper-1",
            dotted_order="1.1.2",
            inputs={"receipt_id": "abc123"},
            start_time="2025-01-01 00:00:02",
            end_time="2025-01-01 00:00:03",
        )
        self.all_runs = [self.wrapper, self.tool_a, self.tool_b]

    def test_produces_two_tool_steps(self):
        steps = derive_trace_steps(
            child_runs=[self.wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=self.all_runs,
        )
        assert len(steps) == 2

    def test_step_types_are_tools(self):
        steps = derive_trace_steps(
            child_runs=[self.wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=self.all_runs,
        )
        assert all(s["type"] == "tools" for s in steps)

    def test_step_names_match_tool_functions(self):
        steps = derive_trace_steps(
            child_runs=[self.wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=self.all_runs,
        )
        assert steps[0]["content"] == "search_receipts"
        assert steps[1]["content"] == "get_receipt"

    def test_step_details_contain_inputs(self):
        steps = derive_trace_steps(
            child_runs=[self.wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=self.all_runs,
        )
        assert json.loads(steps[0]["detail"]) == {"query": "coffee"}
        assert json.loads(steps[1]["detail"]) == {"receipt_id": "abc123"}

    def test_step_durations(self):
        steps = derive_trace_steps(
            child_runs=[self.wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=self.all_runs,
        )
        assert steps[0]["durationMs"] == 2000
        assert steps[1]["durationMs"] == 1000


# ---------------------------------------------------------------------------
# Test: derive_trace_steps with all_runs=None skips wrapper (backward compat)
# ---------------------------------------------------------------------------

class TestDeriveTraceStepsNoAllRunsSkipsWrapper:
    """When all_runs is None the 'tools' wrapper is silently skipped."""

    def test_no_steps_produced(self):
        wrapper = _make_run(
            id="wrapper-1",
            name="tools",
            run_type="chain",
        )
        steps = derive_trace_steps(
            child_runs=[wrapper],
            question_result={},
            receipts_lookup={},
            all_runs=None,
        )
        assert steps == []

    def test_default_param_is_none(self):
        """Calling without all_runs should behave the same as all_runs=None."""
        wrapper = _make_run(name="tools", run_type="chain")
        steps = derive_trace_steps([wrapper], {}, {})
        assert steps == []


# ---------------------------------------------------------------------------
# Test: Agent→tools→agent→tools produces interleaved steps
# ---------------------------------------------------------------------------

class TestDeriveTraceStepsMultipleLoops:
    """Agent→tools→agent→tools pattern produces interleaved steps."""

    def test_interleaved_order(self):
        agent1 = _make_run(
            id="agent-1", name="agent", run_type="chain",
            parent_run_id="root", dotted_order="1.1",
            outputs={"content": "Let me search"},
        )
        tools_wrapper1 = _make_run(
            id="tools-w1", name="tools", run_type="chain",
            parent_run_id="root", dotted_order="1.2",
        )
        agent2 = _make_run(
            id="agent-2", name="agent", run_type="chain",
            parent_run_id="root", dotted_order="1.3",
            outputs={"content": "Found it"},
        )
        tools_wrapper2 = _make_run(
            id="tools-w2", name="tools", run_type="chain",
            parent_run_id="root", dotted_order="1.4",
        )
        tool_child1 = _make_run(
            id="tc1", name="search_receipts", run_type="tool",
            parent_run_id="tools-w1", dotted_order="1.2.1",
            inputs={"query": "coffee"},
        )
        tool_child2 = _make_run(
            id="tc2", name="get_receipt", run_type="tool",
            parent_run_id="tools-w2", dotted_order="1.4.1",
            inputs={"id": "r1"},
        )

        depth1 = [agent1, tools_wrapper1, agent2, tools_wrapper2]
        all_runs = depth1 + [tool_child1, tool_child2]

        steps = derive_trace_steps(
            child_runs=depth1,
            question_result={},
            receipts_lookup={},
            all_runs=all_runs,
        )

        types = [s["type"] for s in steps]
        assert types == ["agent", "tools", "agent", "tools"]
        assert steps[1]["content"] == "search_receipts"
        assert steps[3]["content"] == "get_receipt"


# ---------------------------------------------------------------------------
# Test: _classify_run unchanged for existing types
# ---------------------------------------------------------------------------

class TestClassifyRunUnchanged:
    @pytest.mark.parametrize(
        "name,run_type,expected",
        [
            ("plan_node", "chain", "plan"),
            ("agent", "chain", "agent"),
            ("shape_output", "chain", "shape"),
            ("synthesize_answer", "chain", "synthesize"),
            ("final_answer", "chain", "synthesize"),
            ("search_receipts", "tool", "tool"),
            ("tools", "chain", None),
            ("unknown_node", "chain", None),
        ],
    )
    def test_classification(self, name, run_type, expected):
        assert _classify_run(name, run_type) == expected


# ---------------------------------------------------------------------------
# Test: build_question_cache includes tool steps end-to-end
# ---------------------------------------------------------------------------

class TestBuildQuestionCacheIncludesToolSteps:
    def test_tool_steps_in_trace(self):
        _make_run(
            id="root-1", name="qa_graph", run_type="chain",
            is_root=True, dotted_order="1",
        )
        agent = _make_run(
            id="agent-1", name="agent", run_type="chain",
            parent_run_id="root-1", dotted_order="1.1",
            outputs={"content": "reasoning"},
        )
        tools_wrapper = _make_run(
            id="tools-w", name="tools", run_type="chain",
            parent_run_id="root-1", dotted_order="1.2",
        )
        tool_run = _make_run(
            id="tool-1", name="search_receipts", run_type="tool",
            parent_run_id="tools-w", dotted_order="1.2.1",
            inputs={"query": "groceries"},
        )

        all_runs = [agent, tools_wrapper, tool_run]
        question_result = {
            "question": "What groceries?",
            "questionIndex": 0,
            "receiptCount": 3,
            "answer": "Found groceries",
            "cost": 0.01,
        }

        cache = build_question_cache(
            trace_id="trace-1",
            root_run_id="root-1",
            all_runs=all_runs,
            question_result=question_result,
            receipts_lookup={},
        )

        tool_steps = [s for s in cache["trace"] if s["type"] == "tools"]
        assert len(tool_steps) == 1
        assert tool_steps[0]["content"] == "search_receipts"
        assert "groceries" in tool_steps[0]["detail"]


# ---------------------------------------------------------------------------
# Test: compute_stats still counts depth-2 tools correctly
# ---------------------------------------------------------------------------

class TestComputeStatsStillCorrect:
    def test_tool_invocations_count_depth2(self):
        """compute_stats iterates all runs, so depth-2 tools are counted."""
        runs = [
            _make_run(name="tools", run_type="chain"),
            _make_run(name="search_receipts", run_type="tool"),
            _make_run(name="get_receipt", run_type="tool"),
            _make_run(name="agent", run_type="llm", total_tokens=100),
        ]
        question_result = {"cost": 0.05, "receiptCount": 5}

        stats = compute_stats(runs, question_result)
        assert stats["toolInvocations"] == 2
        assert stats["llmCalls"] == 1
        assert stats["receiptsProcessed"] == 5
        assert stats["cost"] == 0.05


class _FakeExpr:
    def __init__(self, resolver):
        self._resolver = resolver

    def __call__(self, row: dict[str, Any]) -> Any:
        return self._resolver(row)

    def __eq__(self, other: object) -> "_FakeExpr":  # type: ignore[override]
        if isinstance(other, _FakeExpr):
            return _FakeExpr(
                lambda row: self._resolver(row) == other._resolver(row)
            )
        return _FakeExpr(lambda row: self._resolver(row) == other)

    def isin(self, *values: Any) -> "_FakeExpr":
        allowed = set(values)
        return _FakeExpr(lambda row: self._resolver(row) in allowed)


class _FakeFunctions:
    @staticmethod
    def col(name: str) -> _FakeExpr:
        return _FakeExpr(lambda row: row.get(name))

    @staticmethod
    def lit(value: Any) -> _FakeExpr:
        return _FakeExpr(lambda _row: value)


class _FakeRow:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def asDict(self, recursive: bool = False) -> dict[str, Any]:
        del recursive
        return dict(self._payload)


class _FakeDataFrame:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def filter(self, expr: Any) -> "_FakeDataFrame":
        if callable(expr):
            return _FakeDataFrame(
                [row for row in self._rows if bool(expr(row))]
            )
        return self

    def select(self, *columns: str) -> "_FakeDataFrame":
        return _FakeDataFrame(
            [{column: row.get(column) for column in columns} for row in self._rows]
        )

    def toLocalIterator(self):
        return iter([_FakeRow(row) for row in self._rows])


class _FakeAnalysisException(Exception):
    pass


class _FakeReader:
    def __init__(
        self,
        *,
        fail_plain: bool = False,
        fail_recursive: bool = False,
    ) -> None:
        self._options: dict[str, str] = {}
        self.fail_plain = fail_plain
        self.fail_recursive = fail_recursive
        self.calls: list[dict[str, Any]] = []
        self.result_df = _FakeDataFrame([{"id": "1"}])

    def option(self, name: str, value: str) -> "_FakeReader":
        self._options[name] = value
        return self

    def parquet(self, path: str) -> _FakeDataFrame:
        options = dict(self._options)
        self.calls.append({"path": path, "options": options})
        recursive = options.get("recursiveFileLookup") == "true"
        if (not recursive and self.fail_plain) or (
            recursive and self.fail_recursive
        ):
            raise _FakeAnalysisException("simulated parquet read failure")
        return self.result_df


class _FakeSparkSession:
    def __init__(self, reader: _FakeReader) -> None:
        self.read = reader


def test_read_parquet_traces_retries_with_recursive(monkeypatch):
    import receipt_langsmith.spark.qa_viz_cache_helpers as qa_helpers

    reader = _FakeReader(fail_plain=True, fail_recursive=False)
    spark = _FakeSparkSession(reader)
    monkeypatch.setattr(qa_helpers, "AnalysisException", _FakeAnalysisException)

    result = qa_helpers.read_parquet_traces(spark, "s3://bucket/traces/")

    assert result is reader.result_df
    assert len(reader.calls) == 2
    assert reader.calls[0]["path"] == "s3a://bucket/traces/"
    assert "recursiveFileLookup" not in reader.calls[0]["options"]
    assert reader.calls[1]["options"]["recursiveFileLookup"] == "true"


def test_read_parquet_traces_returns_none_when_both_reads_fail(monkeypatch):
    import receipt_langsmith.spark.qa_viz_cache_helpers as qa_helpers

    reader = _FakeReader(fail_plain=True, fail_recursive=True)
    spark = _FakeSparkSession(reader)
    monkeypatch.setattr(qa_helpers, "AnalysisException", _FakeAnalysisException)

    result = qa_helpers.read_parquet_traces(spark, "s3://bucket/traces/")

    assert result is None
    assert len(reader.calls) == 2


def test_collect_root_runs_filters_non_root_rows(monkeypatch):
    import receipt_langsmith.spark.qa_viz_cache_helpers as qa_helpers

    monkeypatch.setattr(qa_helpers, "F", _FakeFunctions())
    df = _FakeDataFrame(
        [
            {"trace_id": "trace-1", "id": "root-1", "inputs": "{}", "is_root": True},
            {"trace_id": "trace-2", "id": "child-1", "inputs": "{}", "is_root": False},
        ]
    )

    root_runs = qa_helpers.collect_root_runs(df)

    assert root_runs == {
        "trace-1": {"trace_id": "trace-1", "id": "root-1", "inputs": "{}"}
    }


def test_collect_traces_filters_by_trace_ids(monkeypatch):
    import receipt_langsmith.spark.qa_viz_cache_helpers as qa_helpers

    monkeypatch.setattr(qa_helpers, "F", _FakeFunctions())
    df = _FakeDataFrame(
        [
            {
                "id": "run-1",
                "trace_id": "trace-1",
                "parent_run_id": None,
                "name": "root",
                "run_type": "chain",
                "status": "success",
                "dotted_order": "1",
                "is_root": True,
                "inputs": "{}",
                "outputs": "{}",
                "total_tokens": 10,
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T00:00:01Z",
            },
            {
                "id": "run-2",
                "trace_id": "trace-2",
                "parent_run_id": None,
                "name": "root",
                "run_type": "chain",
                "status": "success",
                "dotted_order": "1",
                "is_root": True,
                "inputs": "{}",
                "outputs": "{}",
                "total_tokens": 12,
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T00:00:01Z",
            },
        ]
    )

    traces = qa_helpers.collect_traces(df, trace_ids={"trace-2"})

    assert set(traces.keys()) == {"trace-2"}
    assert traces["trace-2"][0]["id"] == "run-2"
