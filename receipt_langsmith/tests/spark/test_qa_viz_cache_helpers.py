"""Tests for qa_viz_cache_helpers tool-expansion logic."""

from __future__ import annotations

import json

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
