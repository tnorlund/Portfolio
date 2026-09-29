"""Offline coverage for complete aggregation with bounded model context."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import SecretStr
from receipt_embeddings.testing import FakeVectorIndex

from receipt_agent.agents.question_answering import graph as qa_graph
from receipt_agent.agents.question_answering.context import (
    MAX_CONTEXT_BYTES,
    MAX_MONTH_ROWS,
    MAX_SUMMARY_ROWS,
    QAContextBudgetExceeded,
    aggregate_receipts,
    aggregate_view,
    ensure_context_budget,
)
from receipt_agent.agents.question_answering.state import (
    QAState,
    QuestionClassification,
)
from receipt_agent.agents.question_answering.tools import (
    SYSTEM_PROMPT,
    create_qa_tools,
)


def _row(index: int, **overrides: Any) -> dict:
    return {
        "image_id": f"receipt-{index:06d}",
        "receipt_id": 1,
        "merchant_name": "Example Market",
        "effective_date": "2026-01-05T00:00:00",
        "date_source": "label",
        "date": "2026-01-05T00:00:00",
        "grand_total": 0.1,
        "tax": 0.01,
        "tip": 0,
        "item_count": 1,
        **overrides,
    }


def _client(rows: list[dict]) -> MagicMock:
    """Provide paginated database records, with large non-evidence metadata."""
    records = []
    for row in rows:
        record = SimpleNamespace(
            **{
                key: row[key]
                for key in ("image_id", "receipt_id", "merchant_name")
            },
            grand_total=row.get("grand_total"),
            effective_date=(
                datetime.fromisoformat(row["effective_date"])
                if row.get("effective_date")
                else None
            ),
            to_dict=lambda row=row: {**row, "ledger": "audit " * 500},
        )
        records.append(record)

    def page(limit: int, last_evaluated_key: dict | None) -> tuple:
        start = (last_evaluated_key or {}).get("offset", 0)
        end = start + limit
        return records[start:end], (
            {"offset": end} if end < len(records) else None
        )

    client = MagicMock()
    client.list_receipt_summaries.side_effect = page
    client.list_receipt_places.return_value = ([], None)
    client.get_receipt_details.return_value = SimpleNamespace(
        place=None, words=[]
    )
    return client


def _summary_tool(rows: list[dict]) -> tuple:
    client = _client(rows)
    tools, holder = create_qa_tools(
        dynamo_client=client,
        embed_fn=lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    return (
        next(tool for tool in tools if tool.name == "get_receipt_summaries"),
        holder,
        client,
    )


class _Provider:
    """Deterministic provider replacement executing a real graph/tool round."""

    def __init__(self) -> None:
        self.messages: list[list] = []
        self.tool_result: dict = {}
        self.synthesis: dict = {}

    def with_structured_output(self, schema: type) -> Any:
        return SimpleNamespace(
            invoke=lambda messages: QuestionClassification(
                question_type="aggregation",
                retrieval_strategy="exhaustive_scan",
                tools_to_use=["get_receipt_summaries"],
            )
        )

    def bind_tools(self, tools: list) -> Any:
        return self

    def invoke(self, messages: list) -> AIMessage:
        self.messages.append(messages)
        assert len(json.dumps([m.model_dump() for m in messages]).encode()) < (
            MAX_CONTEXT_BYTES
        )
        if "Receipt Data:\n" in messages[-1].content:
            prompt = messages[-1].content
            assert "Keep this agent conclusion" in prompt
            raw_context = prompt.split("Receipt Data:\n", 1)[1].split(
                "\n\nGenerate a clear", 1
            )[0]
            self.synthesis = json.loads(raw_context)
            return AIMessage(content="Recorded receipt spending is $250.50.")
        if messages[-1].type == "tool":
            self.tool_result = json.loads(messages[-1].content)
            return AIMessage(content="Keep this agent conclusion: $250.50.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "summaries-1",
                    "name": "get_receipt_summaries",
                    "args": {},
                    "type": "tool_call",
                }
            ],
        )


@pytest.mark.parametrize(
    "question",
    [
        "What's my monthly spending average?",
        "Show me spending patterns by day of week",
    ],
)
def test_large_corpus_survives_tool_shape_synthesis(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    rows = [
        _row(
            index,
            effective_date=(
                "2026-01-05T00:00:00"
                if index % 2 == 0
                else "2026-03-04T00:00:00"
            ),
        )
        for index in range(2505)
    ]
    client = _client(rows)
    provider = _Provider()
    monkeypatch.setattr(qa_graph, "create_llm", lambda **kwargs: provider)
    graph, holder = qa_graph.create_qa_graph(
        dynamo_client=client,
        embed_fn=lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
        settings=SimpleNamespace(
            openrouter_model="test/model",
            openrouter_base_url="https://unused.invalid",
            openrouter_api_key=SecretStr("unused"),
        ),
    )
    result = graph.invoke(
        QAState(
            question=question,
            messages=[
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=question),
            ],
        )
    )

    assert client.list_receipt_summaries.call_count == 3
    assert len(holder["summary_receipts"]) == 2505
    assert len(result["shaped_summaries"]) == 2505
    # Even auto-fetched details with no usable OCR inherit trusted totals.
    assert result["shaped_summaries"][0].grand_total == 0.1
    assert result["shaped_summaries"][-1].image_id == rows[-1]["image_id"]
    assert result["shaped_summaries"][-1].date == rows[-1]["effective_date"]
    assert len(provider.tool_result["summaries"]) == MAX_SUMMARY_ROWS
    assert provider.tool_result["summary_coverage"]["total_count"] == 2505
    assert provider.tool_result["total_spending"] == 250.5
    assert provider.tool_result["total_tax"] == 25.05
    assert "ledger" not in json.dumps(provider.messages, default=str)
    aggregate = provider.synthesis["precomputed_aggregates"][0]
    assert aggregate["count"] == 2505
    assert aggregate["total_spending"] == 250.5
    coverage = aggregate["date_coverage"]
    assert coverage["calendar_month_count"] == 3
    assert coverage["observed_month_count"] == 2
    assert coverage["average_per_calendar_month"] == 83.5
    assert coverage["average_per_observed_month"] == 125.25
    weekdays = {row["weekday"]: row for row in aggregate["weekday_spending"]}
    assert weekdays["Monday"]["total_spending"] == 125.3
    assert weekdays["Wednesday"]["total_spending"] == 125.2
    assert len(result["evidence"]) == qa_graph.MAX_EVIDENCE_ITEMS
    assert result["receipt_count"] == 2505
    assert result["evidence_coverage"]["total_receipts"] == 2505
    assert result["evidence_coverage"]["cited_receipts"] == 200
    assert len(provider.messages) == 3


def test_money_missing_dates_refunds_and_zero_totals_are_explicit() -> None:
    rows = [
        _row(0, grand_total=0),
        _row(1, grand_total=None),
        _row(2, grand_total=-0.1),
        _row(3, grand_total=0.2, date_source="bank"),
        _row(4, grand_total=0.3, date=None, effective_date=None),
    ]
    result = aggregate_receipts(rows)
    assert result["total_spending"] == 0.4
    assert result["receipts_with_totals"] == 4
    assert result["receipts_missing_totals"] == 1
    assert result["average_receipt"] == 0.1
    coverage = result["date_coverage"]
    assert coverage["dated_total_spending"] == 0.1
    assert coverage["undated"]["count"] == 1
    assert coverage["undated"]["total_spending"] == 0.3
    assert coverage["bank_date_receipts"] == 1


def test_requested_month_denominator_includes_empty_boundaries() -> None:
    tool, holder, _ = _summary_tool(
        [_row(0, grand_total=12), _row(1, effective_date=None, date=None)]
    )
    result = tool.invoke(
        {"start_date": "2025-12-01", "end_date": "2026-03-31", "limit": 1}
    )
    coverage = result["date_coverage"]
    assert result["count"] == 1
    assert coverage["calendar_month_count"] == 1
    assert coverage["requested_calendar_month_count"] == 4
    assert coverage["average_per_requested_calendar_month"] == 3
    assert result["undated_excluded"]["count"] == 1
    assert holder["aggregates"][0]["undated_excluded"] == (
        result["undated_excluded"]
    )


def test_paging_changes_evidence_only_and_retains_full_aggregate() -> None:
    tool, holder, _ = _summary_tool([_row(index) for index in range(41)])
    first = tool.invoke({"limit": 5})
    second = tool.invoke({"limit": 5, "offset": 5})
    assert first["count"] == second["count"] == 41
    assert first["total_spending"] == second["total_spending"] == 4.1
    assert first["summaries"][0]["image_id"] == "receipt-000000"
    assert second["summaries"][0]["image_id"] == "receipt-000005"
    assert second["summary_coverage"]["next_offset"] == 10
    assert len(holder["summary_receipts"]) == 41
    assert len(holder["aggregates"]) == 1


def test_evidence_pages_survive_a_changed_database_scan_order() -> None:
    rows = [_row(index) for index in range(6)]
    tool, holder, client = _summary_tool(rows)
    first = tool.invoke({"limit": 3})
    reordered = _client(list(reversed(rows)))
    client.list_receipt_summaries.side_effect = (
        reordered.list_receipt_summaries.side_effect
    )
    second = tool.invoke({"limit": 3, "offset": 3})
    ids = [row["image_id"] for row in first["summaries"] + second["summaries"]]
    assert ids == [row["image_id"] for row in rows]
    assert len(set(ids)) == 6
    assert first["total_spending"] == second["total_spending"] == 0.6
    assert len(holder["summary_receipts"]) == 6


def test_month_pages_keep_exact_full_denominator() -> None:
    rows = [
        _row(
            index,
            effective_date=f"{2000 + index // 12}-{1 + index % 12:02d}-01",
        )
        for index in range(MAX_MONTH_ROWS + 1)
    ]
    aggregate = aggregate_receipts(rows)
    page = aggregate_view(aggregate)
    assert len(page["monthly_spending"]) == MAX_MONTH_ROWS
    assert page["month_coverage"]["next_offset"] == MAX_MONTH_ROWS
    assert page["count"] == MAX_MONTH_ROWS + 1
    assert page["date_coverage"]["calendar_month_count"] == MAX_MONTH_ROWS + 1
    assert len(aggregate["monthly_spending"]) == MAX_MONTH_ROWS + 1


def test_outliers_are_excluded_before_every_aggregate_and_retained_in_audit() -> (
    None
):
    tool, holder, _ = _summary_tool(
        [_row(0, grand_total=12.34), _row(1, grand_total=123456)]
    )
    result = tool.invoke({})
    assert result["count"] == 1
    assert result["total_spending"] == 12.34
    assert result["date_coverage"]["dated_total_spending"] == 12.34
    assert result["monthly_spending"][0]["total_spending"] == 12.34
    assert result["excluded_outlier_count"] == 1
    assert len(holder["summary_receipts"]) == 1
    assert sum(map(len, holder["excluded_summary_outliers"].values())) == 1
    assert holder["aggregates"][0]["excluded_outlier_count"] == 1


def test_synthesis_preserves_agent_analysis_beyond_old_character_cap() -> None:
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="answer")
    analysis = "Evidence reviewed. " * 200 + "Exact final conclusion: $12.34"
    qa_graph.create_synthesize_node(provider, {})(
        QAState(question="total", messages=[AIMessage(content=analysis)])
    )
    messages = provider.invoke.call_args.args[0]
    assert analysis in messages[-1].content


def test_synthesis_budget_failure_never_reaches_provider() -> None:
    provider = MagicMock()
    node = qa_graph.create_synthesize_node(provider, {})
    with pytest.raises(QAContextBudgetExceeded):
        node(
            QAState(
                question="total",
                messages=[AIMessage(content="x" * MAX_CONTEXT_BYTES)],
            )
        )
    provider.invoke.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"start_date": "not-a-date"},
        {"end_date": "not-a-date"},
        {"start_date": "2026-02-01", "end_date": "2026-01-01"},
        {"limit": 0},
        {"offset": -1},
        {"month_offset": -1},
    ],
)
def test_invalid_scope_is_not_silently_broadened(arguments: dict) -> None:
    tool, _, client = _summary_tool([_row(0)])
    assert "error" in tool.invoke(arguments)
    client.list_receipt_summaries.assert_not_called()


def test_oversized_history_never_reaches_or_retries_provider() -> None:
    provider = MagicMock()
    provider.bind_tools.return_value = provider
    state = QAState(
        question="total",
        messages=[HumanMessage(content="💸" * MAX_CONTEXT_BYTES)],
    )
    node = qa_graph.create_agent_node(provider, [], {})
    with pytest.raises(QAContextBudgetExceeded):
        node(state)
    provider.invoke.assert_not_called()


def test_plan_budget_failure_does_not_fall_back() -> None:
    provider = MagicMock()
    provider.with_structured_output.return_value = provider
    with pytest.raises(QAContextBudgetExceeded):
        qa_graph.create_plan_node(provider)(
            QAState(question="x" * MAX_CONTEXT_BYTES)
        )
    provider.invoke.assert_not_called()


def test_provider_context_error_is_not_retried_as_empty_response() -> None:
    provider = MagicMock()
    provider.bind_tools.return_value = provider
    provider.invoke.side_effect = RuntimeError(
        "Error code: 400 - Input length exceeds maximum context length"
    )
    node = qa_graph.create_agent_node(provider, [], {})
    with pytest.raises(RuntimeError, match="context length"):
        node(QAState(question="total", messages=[HumanMessage(content="q")]))
    provider.invoke.assert_called_once()


def test_budget_accepts_small_messages() -> None:
    ensure_context_budget([HumanMessage(content="Total please")])
