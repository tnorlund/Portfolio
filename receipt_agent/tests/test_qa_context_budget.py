"""Offline coverage for complete aggregation with bounded model context."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import SecretStr
from receipt_embeddings.service_limits import LINE_INDEX
from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import VectorItem

from receipt_agent.agents.question_answering import graph as qa_graph
from receipt_agent.agents.question_answering.context import (
    MAX_CONTEXT_BYTES,
    MAX_MONTH_ROWS,
    MAX_SUMMARY_ROWS,
    QAContextBudgetExceeded,
    aggregate_receipts,
    aggregate_view,
    bounded_rows,
    ensure_context_budget,
    receipt_evidence_view,
    tool_result_view,
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


def _details(index: int, *, lines: int = 2) -> dict:
    """Product-priced receipts whose basket totals are deliberately larger."""
    return {
        "image_id": f"detail-{index}",
        "receipt_id": 1,
        "merchant": "Market",
        "words_by_line": {
            line: [
                {
                    "text": "COFFEE" if line % 2 == 0 else "MILK",
                    "label": "PRODUCT_NAME",
                    "word_id": line + 1,
                    "x": 0,
                }
            ]
            for line in range(lines)
        },
        "amounts": [
            {
                "label": "LINE_TOTAL",
                "amount": 1.0 if line % 2 == 0 else 2.0,
                "line_idx": line,
                "word_id": line + 2,
            }
            for line in range(lines)
        ]
        + [{"label": "GRAND_TOTAL", "amount": 100.0}],
        "formatted_receipt": "\n".join(
            f"Line {line}: COFFEE[PRODUCT_NAME] 1.00[LINE_TOTAL]"
            for line in range(lines)
        ),
    }


def _captured_context(provider: MagicMock) -> dict:
    prompt = provider.invoke.call_args.args[0][-1].content
    return json.loads(
        prompt.split("Receipt Data:\n", 1)[1].split("\n\nGenerate a clear", 1)[
            0
        ]
    )


def test_product_comparisons_reach_synthesis_without_totals_in_agent_prose() -> (
    None
):
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    holder["retrieved_receipts"] = [_details(i) for i in range(50)]
    aggregate = next(
        tool for tool in tools if tool.name == "aggregate_amounts"
    )
    messages = [AIMessage(content="Compare the completed product scopes.")]
    # Repeat the coffee scope to ensure it refreshes rather than multiplying.
    for product in ["COFFEE", "MILK", "COFFEE"]:
        result = aggregate.invoke({"filter_text": product})
        messages.append(
            ToolMessage(content=json.dumps(result), tool_call_id=product)
        )
    state = QAState(
        question="Compare my spending on coffee and milk",
        messages=messages,
        classification=QuestionClassification(
            question_type="comparison", retrieval_strategy="multi_source"
        ),
    )
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="Coffee $50; milk $100.")
    qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    scopes = {
        row["filter_text"]: row for row in context["amount_aggregations"]
    }
    assert scopes["COFFEE"]["total"] == 50
    assert scopes["MILK"]["total"] == 100
    assert scopes["COFFEE"]["count"] == 50
    assert scopes["COFFEE"]["receipt_count"] == 50
    assert scopes["COFFEE"]["retrieved_receipt_count"] == 50
    assert scopes["COFFEE"]["breakdown_coverage"]["total_count"] == 50
    assert len(scopes["COFFEE"]["breakdown"]) == 5
    assert "retained_basket_totals" not in context
    assert "all_retained_receipts" not in context
    assert len(holder["amount_aggregates"]) == 2
    assert len(holder["amount_aggregates"][0]["source_receipts"]) == 50
    assert len(holder["amount_aggregates"][0]["breakdown"]) == 50
    ensure_context_budget(provider.invoke.call_args.args[0])


def test_repeated_product_calculations_keep_history_bounded() -> None:
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    holder["retrieved_receipts"] = [_details(i) for i in range(1000)]
    aggregate = next(
        tool for tool in tools if tool.name == "aggregate_amounts"
    )
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for i in range(15):
        result = aggregate.invoke({"filter_text": "COFFEE"})
        messages.append(
            ToolMessage(content=json.dumps(result), tool_call_id=str(i))
        )
        assert result["total"] == 1000
        assert result["breakdown_coverage"]["total_count"] == 1000
    ensure_context_budget(messages)
    assert len(holder["amount_aggregates"]) == 1


def test_three_large_receipts_keep_raw_words_out_of_provider_history() -> None:
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    receipts = [_details(i, lines=300) for i in range(3)]
    holder["retrieved_receipts"] = receipts
    holder["fetched_receipt_keys"] = {
        (r["image_id"], r["receipt_id"]) for r in receipts
    }
    tool = next(tool for tool in tools if tool.name == "get_receipt")
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for receipt in receipts:
        result = tool.invoke(
            {"image_id": receipt["image_id"], "receipt_id": 1}
        )
        assert "words_by_line" not in result
        assert result["result_coverage"]["amounts"]["total_count"] == 301
        messages.append(
            ToolMessage(
                content=json.dumps(result), tool_call_id=receipt["image_id"]
            )
        )
    assert len(json.dumps(receipts)) > MAX_CONTEXT_BYTES
    ensure_context_budget(messages)
    assert all(
        len(r["words_by_line"]) == 300 for r in holder["retrieved_receipts"]
    )
    assert len(holder["tool_results"]) == 3
    provider = MagicMock()
    provider.bind_tools.return_value = provider
    provider.invoke.return_value = AIMessage(
        content="The receipt data is ready."
    )
    qa_graph.create_agent_node(provider, tools, holder)(
        QAState(question="Inspect my receipts", messages=messages)
    )
    provider.invoke.assert_called_once()
    page = tool.invoke(
        {"image_id": receipts[0]["image_id"], "receipt_id": 1, "offset": 20}
    )
    assert page["formatted_receipt"].startswith("Line 20:")
    assert page["amounts"][0]["line_idx"] == 20
    assert page["result_coverage"]["amounts"]["next_offset"] == 40


def test_product_search_pages_keep_all_candidates_in_retained_state() -> None:
    client = _client([])
    backend = FakeVectorIndex(
        [
            VectorItem(
                key=f"IMAGE#img-{i}#RECEIPT#00001#LINE#00001",
                index=LINE_INDEX,
                vector=[1.0],
                metadata={
                    "image_id": f"img-{i}",
                    "receipt_id": 1,
                    "text": "COFFEE " + ("coffee description " * 12) + " 1.00",
                    "merchant_name": "Market",
                    "section_type": "ITEMS",
                },
            )
            for i in range(100)
        ]
    )
    tools, holder = create_qa_tools(
        client, lambda texts: [[1.0] for _ in texts], vector_client=backend
    )
    tool = next(tool for tool in tools if tool.name == "search_product_lines")
    first = tool.invoke({"query": "coffee", "search_type": "semantic"})
    assert first["unique_items"] == 100
    assert first["raw_total"] == 100
    assert 0 < len(first["items"]) < 100
    second = tool.invoke(
        {
            "query": "coffee",
            "search_type": "semantic",
            "offset": first["result_coverage"]["items"]["next_offset"],
        }
    )
    assert first["items"][0]["image_id"] != second["items"][0]["image_id"]
    retained = next(
        entry
        for entry in holder["tool_results"].values()
        if entry["tool"] == "search_product_lines"
    )
    assert len(retained["result"]["items"]) == 100
    ensure_context_budget(
        [
            ToolMessage(content=json.dumps(first), tool_call_id="first"),
            ToolMessage(content=json.dumps(second), tool_call_id="second"),
        ]
    )


def test_merchant_discovery_pages_keep_a_stable_complete_inventory() -> None:
    client = _client([])
    places = [
        SimpleNamespace(merchant_name=f"Merchant {i:03d}") for i in range(60)
    ]
    client.list_receipt_places.side_effect = [
        (places, None),
        (list(reversed(places)), None),
    ]
    tools, holder = create_qa_tools(
        client,
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    tool = next(tool for tool in tools if tool.name == "list_merchants")
    first = tool.invoke({})
    second = tool.invoke({"offset": 20})
    assert first["total_merchants"] == second["total_merchants"] == 60
    assert first["merchants"][0]["merchant"] == "Merchant 000"
    assert second["merchants"][0]["merchant"] == "Merchant 020"
    assert (
        len(next(iter(holder["tool_results"].values()))["result"]["merchants"])
        == 60
    )


def test_oversized_single_record_is_explicit_and_pagination_advances() -> None:
    rows = [{"merchant": "long " * 3000, "amount": 12.34}, {"amount": 56.78}]
    page, coverage = bounded_rows(rows)
    assert page[0]["record_omitted"] is True
    assert page[0]["source_offset"] == 0
    assert "amount" not in page[0]
    assert coverage["next_offset"] == 1
    next_page, next_coverage = bounded_rows(rows, offset=1)
    assert next_page == [{"amount": 56.78}]
    assert next_coverage["next_offset"] is None
    assert rows[0]["amount"] == 12.34
    receipt_page = receipt_evidence_view(
        [_row(0, merchant_name="long " * 3000), _row(1)]
    )
    assert receipt_page["summaries"][0]["record_omitted"] is True
    assert receipt_page["summary_coverage"]["next_offset"] == 1
    text_page = tool_result_view(
        {"formatted_receipt": "12.34 " * 2000 + "\nOther line"}
    )
    assert "record_omitted" in text_page["formatted_receipt"]
    assert (
        text_page["result_coverage"]["formatted_receipt"]["next_offset"] == 1
    )


async def test_new_question_clears_prior_scoped_evidence() -> None:
    holder = {
        "amount_aggregates": [{"total": 999}],
        "tool_results": {"old": {}},
    }

    class Graph:
        async def ainvoke(self, state: QAState, config: dict) -> None:
            assert holder["amount_aggregates"] == []
            assert holder["tool_results"] == {}
            holder["answer"] = {"answer": "No prior scope reused."}

    result = await qa_graph.answer_question(Graph(), holder, "A new question")
    assert result["answer"] == "No prior scope reused."


def test_long_history_hands_retained_evidence_to_synthesis() -> None:
    provider = MagicMock()
    provider.bind_tools.return_value = provider
    holder = {"retrieved_receipts": [_details(0)]}
    state = QAState(
        question="What did I buy?",
        messages=[
            ToolMessage(content="x" * MAX_CONTEXT_BYTES, tool_call_id="long")
        ],
    )
    result = qa_graph.create_agent_node(provider, [], holder)(state)
    assert result["current_phase"] == "shape"
    assert qa_graph.route_after_agent(state, holder) == "shape"
    provider.invoke.assert_not_called()
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    provider.invoke.return_value = AIMessage(content="Coffee and milk.")
    answer = qa_graph.create_synthesize_node(provider, holder)(state)
    assert answer["final_answer"] == "Coffee and milk."
    ensure_context_budget(provider.invoke.call_args.args[0])


def test_many_receipt_scopes_keep_every_total_when_breakdowns_are_compacted() -> (
    None
):
    rows = [
        _row(
            index,
            effective_date=f"{2000 + index // 12}-{1 + index % 12:02d}-01",
        )
        for index in range(120)
    ]
    holder = {
        "aggregates": [
            {
                "source": f"merchant-{index}",
                "filters": {"merchant": f"merchant-{index}"},
                **aggregate_receipts(rows),
            }
            for index in range(20)
        ]
    }
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(
        content="Each scoped merchant totals $12."
    )
    qa_graph.create_synthesize_node(provider, holder)(
        QAState(
            question="Compare merchant spending",
            messages=[
                AIMessage(content="Use the completed merchant comparisons.")
            ],
        )
    )
    context = _captured_context(provider)
    assert len(context["precomputed_aggregates"]) == 20
    for index, aggregate in enumerate(context["precomputed_aggregates"]):
        assert aggregate["filters"] == {"merchant": f"merchant-{index}"}
        assert aggregate["total_spending"] == 12
        assert aggregate["count"] == 120
        assert aggregate["monthly_spending"] == []
        assert aggregate["date_coverage"]["calendar_month_count"] == 120
        assert aggregate["month_coverage"]["total_groups"] == 120
    assert len(holder["aggregates"][0]["monthly_spending"]) == 120
    ensure_context_budget(provider.invoke.call_args.args[0])


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


def test_synthesis_budget_limit_preserves_a_completed_agent_answer() -> None:
    provider = MagicMock()
    node = qa_graph.create_synthesize_node(provider, {})
    answer = "x" * MAX_CONTEXT_BYTES
    result = node(
        QAState(
            question="total",
            messages=[AIMessage(content=answer)],
        )
    )
    assert result["final_answer"] == answer
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
