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
    receipt_total_extrema,
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


def _expanded_scope(scope: dict, context: dict) -> dict:
    """Resolve the provider's lossless note and extrema references."""

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key.removesuffix("_ref") if key.endswith("note_ref") else key: (
                context["shared_notes"][item]
                if key.endswith("note_ref")
                else expand(item)
            )
            for key, item in value.items()
        }

    expanded = expand(scope)
    extrema = expanded.get("receipt_total_extrema", {})
    for name in ("minimum", "minimum_nonnegative", "maximum"):
        entry = extrema.get(name)
        if entry and "same_as" in entry:
            extrema[name] = extrema[entry["same_as"]]
    return expanded


def _tool_reference_context(messages: list) -> dict:
    for message in messages:
        if isinstance(message, SystemMessage) and (
            "Aggregate context references:\n" in message.content
        ):
            raw = message.content.split("Aggregate context references:\n", 1)[
                1
            ]
            return json.JSONDecoder().raw_decode(raw)[0]
    return {"shared_notes": []}


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


@pytest.mark.parametrize("include_clear_coffee", [False, True])
def test_ambiguous_product_prices_are_excluded_through_synthesis(
    include_clear_coffee: bool,
) -> None:
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    merged = _details(0)
    merged["words_by_line"] = {
        0: [
            {"text": "MILK", "label": "PRODUCT_NAME", "word_id": 1},
            {"text": "COFFEE", "label": "PRODUCT_NAME", "word_id": 2},
        ]
    }
    merged["amounts"] = [
        {"label": "LINE_TOTAL", "amount": 5.49, "line_idx": 0},
        {"label": "LINE_TOTAL", "amount": 4.29, "line_idx": 0},
    ]
    # Many ambiguous lines must remain in state without inflating the prompt.
    holder["retrieved_receipts"] = [
        {**merged, "image_id": f"merged-{index}"} for index in range(100)
    ]
    if include_clear_coffee:
        holder["retrieved_receipts"].append(_details(101))
    tool = next(tool for tool in tools if tool.name == "aggregate_amounts")
    result = tool.invoke({"filter_text": "COFFEE"})
    assert result["total"] == (1 if include_clear_coffee else 0)
    assert result["count"] == int(include_clear_coffee)
    coverage = result["amount_coverage"]
    assert coverage["ambiguous_line_count"] == 100
    assert coverage["excluded_amount_count"] == 200
    assert coverage["has_unambiguous_matches"] is include_clear_coffee
    assert coverage["all_matches_ambiguous"] is not include_clear_coffee
    assert coverage["exhaustive_corpus"] is False
    assert coverage["independently_date_filtered"] is False
    assert "excluded_ambiguous_lines" not in result
    assert (
        len(holder["amount_aggregates"][0]["excluded_ambiguous_lines"]) == 100
    )
    assert len(holder["retrieved_receipts"][0]["amounts"]) == 2
    assert len(json.dumps(result).encode()) < 4000
    excluded_sample = result["excluded_ambiguous_line_sample"]
    assert len(excluded_sample) == 5
    assert excluded_sample[0] == {
        "image_id": "merged-0",
        "receipt_id": 1,
        "line_idx": 0,
        "amounts": [5.49, 4.29],
        "total_amount_count": 2,
        "returned_amount_count": 2,
    }
    exclusion_coverage = result["excluded_ambiguous_line_coverage"]
    assert exclusion_coverage["total_count"] == 100
    assert exclusion_coverage["returned_count"] == 5

    state = QAState(
        question="How much did I spend on coffee this year?",
        messages=[
            ToolMessage(content=json.dumps(result), tool_call_id="coffee"),
            AIMessage(content="Incorrectly assign the merged milk prices."),
        ],
    )
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    assert all(
        not summary.line_items
        for summary in state.shaped_summaries
        if summary.image_id.startswith("merged-")
    )
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(
        content="Only a partial subtotal."
    )
    answer = qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    scope = _expanded_scope(context["amount_aggregations"][0], context)
    assert scope["total"] == result["total"]
    assert scope["amount_coverage"] == coverage
    assert "excluded_ambiguous_lines" not in scope
    assert scope["excluded_ambiguous_line_sample"] == excluded_sample
    assert scope["excluded_ambiguous_line_coverage"] == exclusion_coverage
    assert not any(
        "line_items" in receipt
        for receipt in context["receipt_evidence"]["summaries"]
        if receipt["image_id"].startswith("merged-")
    )
    assert all(row["image_id"] == "detail-101" for row in answer["evidence"])
    assert bool(answer["evidence"]) is include_clear_coffee
    assert "retained_basket_totals" not in context
    prompt = provider.invoke.call_args.args[0][0].content
    assert "subtotals from retrieved receipts" in prompt
    assert "all_matches_ambiguous is true, report spending as" in prompt
    assert "Do not reuse excluded prices" in prompt
    ensure_context_budget(provider.invoke.call_args.args[0])

    # An unfiltered sum makes no product-to-price ownership assertion.
    all_amounts = tool.invoke({})
    assert all_amounts["total"] == 978 + (3 if include_clear_coffee else 0)
    assert all_amounts["amount_coverage"]["ambiguous_line_count"] == 0
    assert all_amounts["amount_coverage"]["all_matches_ambiguous"] is False
    # A separate unfiltered-only shape preserves all raw line item prices.
    unfiltered_holder = {
        "retrieved_receipts": holder["retrieved_receipts"],
        "amount_aggregates": [holder["amount_aggregates"][-1]],
    }
    unfiltered = qa_graph.create_shape_node(unfiltered_holder)(state)
    assert [
        item.amount for item in unfiltered["shaped_summaries"][0].line_items
    ] == [5.49, 4.29]


@pytest.mark.parametrize("compact", [False, True])
def test_synthesis_does_not_promote_unreviewed_candidate_spending(
    compact: bool,
) -> None:
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    holder["retrieved_receipts"] = [_details(0)]
    tool = next(tool for tool in tools if tool.name == "aggregate_amounts")
    tool.invoke({"filter_text": "COFFEE"})
    holder["tool_results"]["candidate-search"] = {
        "tool": "search_product_lines",
        "result": {
            "query": "coffee",
            "items": [
                {"text": "COFFEE", "price": 1.0},
                {"text": "MILK", "price": 2.0},
            ],
            "raw_total": 3.0,
        },
    }
    if compact:
        rows = [
            _row(
                i,
                effective_date=f"{2000 + i // 12}-{1 + i % 12:02d}-01",
            )
            for i in range(120)
        ]
        holder["aggregates"] = [
            {"source": f"scope-{i}", **aggregate_receipts(rows)}
            for i in range(20)
        ]
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="Coffee totals $1.")
    qa_graph.create_synthesize_node(provider, holder)(
        QAState(question="How much did I spend on coffee?")
    )
    context = _captured_context(provider)
    candidates = context["tool_evidence"][0]["result"]
    assert "raw_total" not in candidates
    assert "relevance review" in candidates["candidate_spending_note"]
    assert context["amount_aggregations"][0]["filter_text"] == "COFFEE"
    assert context["amount_aggregations"][0]["total"] == 1.0
    assert "retained_basket_totals" not in context["coverage_note"]
    # The retrieval agent's tool contract and retained source are unchanged.
    assert (
        holder["tool_results"]["candidate-search"]["result"]["raw_total"]
        == 3.0
    )
    if compact:
        assert context["precomputed_aggregates"][0]["monthly_spending"] == []


def test_product_no_match_is_distinct_from_excluded_ambiguous_matches() -> (
    None
):
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    holder["retrieved_receipts"] = [_details(0)]
    aggregate_tool = next(
        tool for tool in tools if tool.name == "aggregate_amounts"
    )
    result = aggregate_tool.invoke({"filter_text": "PET FOOD"})
    assert result["total"] == 0
    assert result["count"] == 0
    coverage = result["amount_coverage"]
    assert coverage["ambiguous_line_count"] == 0
    assert coverage["has_unambiguous_matches"] is False
    assert coverage["all_matches_ambiguous"] is False
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="No matching amounts.")
    qa_graph.create_synthesize_node(provider, holder)(
        QAState(question="How much did I spend on pet food?")
    )
    context = _captured_context(provider)
    assert (
        _expanded_scope(context["amount_aggregations"][0], context)[
            "amount_coverage"
        ]
        == coverage
    )
    assert "no matching amounts were found in the retrieved scope" in (
        provider.invoke.call_args.args[0][0].content
    )


def test_ambiguous_product_rows_keep_vetted_receipt_extrema_citations() -> (
    None
):
    rows = [_row(index, grand_total=100) for index in range(253)]
    rows[250]["grand_total"] = 0.27
    rows[251]["grand_total"] = 50
    rows[252]["grand_total"] = 137.07
    tools, holder = create_qa_tools(
        _client(rows),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    summary_tool = next(
        tool for tool in tools if tool.name == "get_receipt_summaries"
    )
    summary_tool.invoke({"merchant_filter": "Example Market"})
    clear = _details(0, lines=1)
    clear["image_id"] = rows[0]["image_id"]
    holder["retrieved_receipts"] = [clear]
    for row in rows[250:]:
        holder["retrieved_receipts"].append(
            {
                "image_id": row["image_id"],
                "receipt_id": 1,
                "words_by_line": {
                    0: [{"text": "MILK COFFEE", "label": "PRODUCT_NAME"}]
                },
                "amounts": [
                    {"label": "LINE_TOTAL", "amount": 5.49, "line_idx": 0},
                    {"label": "LINE_TOTAL", "amount": 4.29, "line_idx": 0},
                ],
            }
        )
    aggregate_tool = next(
        tool for tool in tools if tool.name == "aggregate_amounts"
    )
    product_result = aggregate_tool.invoke({"filter_text": "COFFEE"})
    assert product_result["total"] == 1
    assert product_result["amount_coverage"]["ambiguous_line_count"] == 3
    state = QAState(
        question="What were my lowest and highest receipt totals and coffee spending?"
    )
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    assert len(state.shaped_summaries) == 253
    assert all(
        not summary.line_items
        for summary in state.shaped_summaries
        if summary.image_id in {row["image_id"] for row in rows[250:]}
    )
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="Scoped totals reviewed.")
    result = qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    assert context["amount_aggregations"][0]["total"] == 1
    extrema = context["precomputed_aggregates"][0]["receipt_total_extrema"]
    assert extrema["minimum"]["amount"] == 0.27
    assert extrema["maximum"]["amount"] == 137.07
    evidence = {row["image_id"]: row for row in result["evidence"]}
    assert len(result["evidence"]) == qa_graph.MAX_EVIDENCE_ITEMS
    assert evidence[rows[250]["image_id"]]["amount"] == 0.27
    assert evidence[rows[252]["image_id"]]["amount"] == 137.07
    assert evidence[rows[0]["image_id"]]["amount"] == 1
    # This non-extreme receipt still supports the independently vetted
    # merchant spending scope, even though its product prices are unresolved.
    assert evidence[rows[251]["image_id"]]["amount"] == 50
    assert all(row["amount"] not in (5.49, 4.29) for row in evidence.values())
    assert len(holder["amount_aggregates"][0]["excluded_ambiguous_lines"]) == 3
    ensure_context_budget(provider.invoke.call_args.args[0])


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


def test_aggregate_agent_history_preserves_eight_complete_tool_scopes() -> (
    None
):
    rows = [
        _row(
            scope * 12 + month,
            merchant_name=f"Merchant-{scope:02d}",
            grand_total=(month + 1) / 10,
            effective_date=f"2025-{month + 1:02d}-01",
        )
        for scope in range(8)
        for month in range(12)
    ]
    tool, holder, _ = _summary_tool(rows)
    provider = MagicMock()
    provider.bind_tools.return_value = provider
    provider.invoke.return_value = AIMessage(content="Continue reviewing.")
    node = qa_graph.create_agent_node(provider, [tool], holder)
    state = QAState(
        question="Compare every merchant scope",
        messages=[
            SystemMessage(content=SYSTEM_PROMPT, id="system-message"),
            HumanMessage(content="Compare every merchant scope"),
        ],
    )
    for index in range(8):
        call = {
            "name": tool.name,
            "args": {"merchant_filter": f"Merchant-{index:02d}"},
            "id": f"scope-{index}",
            "type": "tool_call",
        }
        result = tool.invoke(call).model_copy(
            update={
                "id": f"result-{index}",
                "artifact": {"audit": index},
                "response_metadata": {"scope": index},
            }
        )
        # Also cover tools whose name is supplied only by the AI tool call.
        if index % 2:
            result = result.model_copy(update={"name": None})
        state.messages.extend(
            [AIMessage(content="", tool_calls=[call]), result]
        )
        originals = [message.model_dump() for message in state.messages]
        assert node(state).get("current_phase") != "shape"
        assert [
            message.model_dump() for message in state.messages
        ] == originals
        provider_messages = provider.invoke.call_args.args[0]
        references = _tool_reference_context(provider_messages)
        assert len(references["shared_notes"]) == len(
            set(references["shared_notes"])
        )
        assert provider_messages[0].id == "system-message"
        assert (
            provider_messages[0].content.count("Aggregate context references:")
            == 1
        )
        for original, copied in zip(state.messages, provider_messages):
            if isinstance(original, ToolMessage):
                assert copied.model_dump(exclude={"content"}) == (
                    original.model_dump(exclude={"content"})
                )
                assert _expanded_scope(
                    json.loads(copied.content), references
                ) == (json.loads(original.content))
        ensure_context_budget(provider_messages)
    assert provider.invoke.call_count == 8
    assert len(holder["aggregates"]) == 8
    assert len(holder["summary_receipts"]) == 96
    assert not holder.get("retrieval_complete")


def test_product_scope_compaction_preserves_42_exact_filtered_results() -> (
    None
):
    tools, holder = create_qa_tools(
        MagicMock(),
        lambda texts: [[1.0] for _ in texts],
        vector_client=FakeVectorIndex([]),
    )
    receipt = _details(0)
    receipt["words_by_line"] = {}
    receipt["amounts"] = []
    for index in range(42):
        for line in (2 * index, 2 * index + 1):
            receipt["words_by_line"][line] = [
                {"text": f"PRODUCT-{index:03d}", "label": "PRODUCT_NAME"}
            ]
        receipt["amounts"].extend(
            {"label": "LINE_TOTAL", "amount": value, "line_idx": line}
            for line, value in (
                (2 * index, index + 1),
                (2 * index + 1, 5.49),
                (2 * index + 1, 4.29),
            )
        )
    holder["retrieved_receipts"] = [receipt]
    tool = next(tool for tool in tools if tool.name == "aggregate_amounts")
    results = [
        tool.invoke({"filter_text": f"PRODUCT-{index:03d}"})
        for index in range(42)
    ]
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for index, result in enumerate(results):
        call = {
            "name": tool.name,
            "args": {"filter_text": result["filter_text"]},
            "id": f"product-{index}",
            "type": "tool_call",
        }
        messages.extend(
            [
                AIMessage(content="", tool_calls=[call]),
                ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=call["id"],
                    name=tool.name,
                ),
            ]
        )
    agent_provider = MagicMock()
    agent_provider.bind_tools.return_value = agent_provider
    agent_provider.invoke.return_value = AIMessage(content="Scopes reviewed.")
    agent_outcome = qa_graph.create_agent_node(agent_provider, tools, holder)(
        QAState(question="Compare all product subtotals", messages=messages)
    )
    assert agent_outcome.get("current_phase") != "shape"
    references = _tool_reference_context(
        agent_provider.invoke.call_args.args[0]
    )
    for expected, actual in zip(
        results, agent_provider.invoke.call_args.args[0][2::2]
    ):
        assert (
            _expanded_scope(json.loads(actual.content), references) == expected
        )
    assert [
        json.loads(message.content) for message in messages[2::2]
    ] == results
    summary_aggregate = {
        "source": "complete receipt scope",
        "filters": {"merchant": "Example Market"},
        **aggregate_receipts([_row(0)]),
    }
    holder["aggregates"] = [summary_aggregate]
    originals = json.dumps(holder["amount_aggregates"], sort_keys=True)
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="All scopes compared.")
    qa_graph.create_synthesize_node(provider, holder)(
        QAState(question="Compare all product subtotals")
    )
    context = _captured_context(provider)
    assert _expanded_scope(context["precomputed_aggregates"][0], context) == (
        aggregate_view(summary_aggregate)
    )
    assert len(context["amount_aggregations"]) == 42
    for expected, actual in zip(results, context["amount_aggregations"]):
        assert _expanded_scope(actual, context) == expected
    assert json.dumps(holder["amount_aggregates"], sort_keys=True) == originals
    assert len(context["shared_notes"]) == len(set(context["shared_notes"]))
    ensure_context_budget(provider.invoke.call_args.args[0])


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


@pytest.mark.parametrize("question_type", ["aggregation", "time_based"])
def test_sampled_basket_totals_do_not_claim_or_prioritize_exact_extrema(
    question_type: str,
) -> None:
    holder = {"retrieved_receipts": [_details(i) for i in range(251)]}
    # A detail-only OCR total has never passed the summary outlier filter.
    holder["retrieved_receipts"][-1]["amounts"][-1]["amount"] = 129900
    state = QAState(
        question="What was my biggest purchase last month?",
        classification=QuestionClassification(
            question_type=question_type, retrieval_strategy="semantic_hybrid"
        ),
    )
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="Incomplete scope.")
    result = qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    baskets = context["retained_basket_totals"]
    assert baskets["count"] == 251
    assert "receipt_total_extrema" not in baskets
    assert "not product spending or an exhaustive corpus scan" in (
        baskets["scope_note"]
    )
    assert context["precomputed_aggregates"] == []
    assert state.shaped_summaries[-1].grand_total == 129900
    assert len(result["evidence"]) == qa_graph.MAX_EVIDENCE_ITEMS
    assert not any(
        row["image_id"] == "detail-250" for row in result["evidence"]
    )
    ensure_context_budget(provider.invoke.call_args.args[0])


@pytest.mark.parametrize("month_count,scope_count", [(12, 14), (60, 5)])
def test_first_pass_compaction_preserves_full_month_rows_at_previous_boundary(
    month_count: int, scope_count: int
) -> None:
    rows = [
        _row(
            index,
            effective_date=f"{2000 + index // 12}-{1 + index % 12:02d}-01",
            grand_total=(index + 1) / 10,
        )
        for index in range(month_count)
    ]
    full = aggregate_receipts(rows)
    holder = {
        "summary_receipts": rows,
        "aggregates": [
            {
                **full,
                "source": f"merchant-scope-{index}",
                "filters": {"merchant": f"merchant-{index}"},
            }
            for index in range(scope_count)
        ],
    }
    state = QAState(question="Which month was highest for each merchant?")
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="All months compared.")
    qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    assert len(context["precomputed_aggregates"]) == scope_count
    assert len(context["receipt_evidence"]["summaries"]) == min(
        month_count, MAX_SUMMARY_ROWS
    )
    for index, scope in enumerate(context["precomputed_aggregates"]):
        expanded = _expanded_scope(scope, context)
        assert expanded["filters"] == {"merchant": f"merchant-{index}"}
        assert expanded["monthly_spending"] == full["monthly_spending"]
        assert expanded["weekday_spending"] == full["weekday_spending"]
        assert (
            expanded["receipt_total_extrema"] == full["receipt_total_extrema"]
        )
        assert expanded["month_coverage"]["returned_groups"] == month_count
        assert expanded["month_coverage"]["next_offset"] is None
        assert "breakdown_note" not in expanded
    assert len(holder["aggregates"][0]["monthly_spending"]) == month_count
    assert len(state.shaped_summaries) == month_count
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
            # The pre-extrema context admitted 44 of these scopes. Keeping
            # only 20 would miss the observed regression to a 24-scope limit.
            for index in range(44)
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
    assert len(context["precomputed_aggregates"]) == 44
    for index, compact_scope in enumerate(context["precomputed_aggregates"]):
        aggregate = _expanded_scope(compact_scope, context)
        assert aggregate["filters"] == {"merchant": f"merchant-{index}"}
        assert aggregate["total_spending"] == 12
        assert aggregate["count"] == 120
        assert aggregate["monthly_spending"] == []
        assert aggregate["date_coverage"]["calendar_month_count"] == 120
        assert aggregate["month_coverage"]["total_groups"] == 120
        assert aggregate["receipt_total_extrema"] == (
            holder["aggregates"][index]["receipt_total_extrema"]
        )
        assert aggregate["date_coverage"] == (
            holder["aggregates"][index]["date_coverage"]
        )
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
            self.tool_result = _expanded_scope(
                json.loads(messages[-1].content),
                _tool_reference_context(messages),
            )
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


@pytest.mark.parametrize(
    "question",
    [
        "What was my cheapest grocery trip?",
        "What was my largest purchase this month?",
        "Which receipt had the highest total?",
    ],
)
def test_receipt_extrema_survive_paging_graph_and_citation_limits(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    rows = [_row(i, grand_total=50) for i in range(351)]
    rows[0]["grand_total"] = 2.58  # A plausible but wrong sample minimum.
    rows[340]["grand_total"] = 0.27
    rows[349]["grand_total"] = 137.07
    rows[350]["grand_total"] = 0.27  # A tie outside both evidence limits.
    rows.extend(
        [
            _row(1000, merchant_name="Other Market", grand_total=0.01),
            _row(1001, effective_date="2025-12-31", grand_total=999),
        ]
    )

    class ExtremaProvider(_Provider):
        def invoke(self, messages: list) -> AIMessage:
            self.messages.append(messages)
            ensure_context_budget(messages)
            if "Receipt Data:\n" in messages[-1].content:
                assert "sample suggested $2.58" in messages[-1].content
                assert "takes precedence" in messages[0].content
                self.synthesis = json.loads(
                    messages[-1]
                    .content.split("Receipt Data:\n", 1)[1]
                    .split("\n\nGenerate a clear", 1)[0]
                )
                return AIMessage(content="Exact receipt range: $0.27–$137.07")
            if messages[-1].type == "tool":
                self.tool_result = _expanded_scope(
                    json.loads(messages[-1].content),
                    _tool_reference_context(messages),
                )
                return AIMessage(content="The sample suggested $2.58.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "extrema",
                        "name": "get_receipt_summaries",
                        "args": {
                            "merchant_filter": "Example Market",
                            "start_date": "2026-01-01",
                            "end_date": "2026-01-31",
                        },
                        "type": "tool_call",
                    }
                ],
            )

    provider = ExtremaProvider()
    monkeypatch.setattr(qa_graph, "create_llm", lambda **kwargs: provider)
    graph, holder = qa_graph.create_qa_graph(
        dynamo_client=_client(rows),
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
    assert len(holder["summary_receipts"]) == 351
    assert len(result["shaped_summaries"]) == 351
    assert len(provider.tool_result["summaries"]) == MAX_SUMMARY_ROWS
    sampled_ids = {
        row["image_id"] for row in provider.tool_result["summaries"]
    }
    extrema = provider.tool_result["receipt_total_extrema"]
    assert extrema["minimum"]["amount"] == 0.27
    assert extrema["minimum"]["matching_receipts"] == 2
    assert extrema["minimum_nonnegative"] == extrema["minimum"]
    assert extrema["maximum"]["amount"] == 137.07
    scope = _expanded_scope(
        provider.synthesis["precomputed_aggregates"][0], provider.synthesis
    )
    assert scope["receipt_total_extrema"] == extrema
    assert scope["filters"]["merchant"] == "Example Market"
    assert scope["filters"]["start_date"] == "2026-01-01"
    cited = {(e["image_id"], e["receipt_id"]) for e in result["evidence"]}
    for name, index in (("minimum", 340), ("maximum", 349)):
        receipt = extrema[name]["representative_receipt"]
        assert receipt["image_id"] == rows[index]["image_id"]
        assert receipt["merchant"] == "Example Market"
        assert receipt["date"] == rows[index]["effective_date"]
        assert receipt["image_id"] not in sampled_ids
        assert (receipt["image_id"], receipt["receipt_id"]) in cited
    assert len(result["evidence"]) == qa_graph.MAX_EVIDENCE_ITEMS
    assert result["evidence_coverage"]["total_receipts"] == 351


def test_receipt_extrema_distinguish_refunds_zero_missing_and_ties() -> None:
    rows = [
        _row(5, grand_total=None),
        _row(4, grand_total=True),
        _row(3, grand_total="NaN"),
        _row(2, grand_total=-5),
        _row(1, grand_total=0),
        _row(9, grand_total="9.10"),
        _row(8, grand_total=9.1),
    ]
    extrema = receipt_total_extrema(rows)
    assert extrema["minimum"]["amount"] == -5
    assert extrema["minimum_nonnegative"]["amount"] == 0
    assert extrema["maximum"]["amount"] == 9.1
    assert extrema["maximum"]["matching_receipts"] == 2
    assert extrema["maximum"]["representative_receipt"]["image_id"] == (
        rows[-1]["image_id"]
    )
    assert receipt_total_extrema(list(reversed(rows))) == extrema
    assert receipt_total_extrema([])["minimum"] is None
    assert (
        receipt_total_extrema([_row(0, grand_total=-1)])["minimum_nonnegative"]
        is None
    )


def test_extrema_disclose_relative_outlier_exclusions_through_synthesis() -> (
    None
):
    rows = [_row(i, grand_total=50) for i in range(10)]
    rows[-1]["grand_total"] = 137.07
    # Both are below the absolute $50,000 ceiling but exceed the existing
    # max(median * 100, $5,000) relative threshold. They might be real purchases.
    rows.extend([_row(10, grand_total=6000), _row(11, grand_total=7000)])
    tool, holder, _ = _summary_tool(rows)
    result = tool.invoke({})
    assert result["count"] == 10
    assert result["total_spending"] == 587.07
    assert result["excluded_outlier_count"] == 2
    extrema = result["receipt_total_extrema"]
    assert extrema["population"] == "accepted_receipts"
    assert extrema["maximum"]["amount"] == 137.07
    assert extrema["excluded_outlier_count"] == 2
    assert extrema["excluded_outlier_range"]["minimum_total"] == 6000
    assert extrema["excluded_outlier_range"]["maximum_total"] == 7000
    assert "not confirmed" in extrema["excluded_outlier_range"]["note"]
    assert "not all matching purchases" in extrema["note"]
    assert len(json.dumps(extrema).encode()) < 2500
    assert len(next(iter(holder["excluded_summary_outliers"].values()))) == 2

    state = QAState(
        question="What was my largest purchase?",
        messages=[AIMessage(content="Unqualified maximum $137.07.")],
    )
    state.shaped_summaries = qa_graph.create_shape_node(holder)(state)[
        "shaped_summaries"
    ]
    assert len(state.shaped_summaries) == 10
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(
        content="Largest accepted receipt $137.07; two larger receipts flagged."
    )
    qa_graph.create_synthesize_node(provider, holder)(state)
    context = _captured_context(provider)
    assert (
        _expanded_scope(context["precomputed_aggregates"][0], context)[
            "receipt_total_extrema"
        ]
        == extrema
    )
    system = provider.invoke.call_args.args[0][0].content
    assert 'maximum as "largest among accepted receipts"' in system
    assert "overall largest purchase\n    is not confirmed" in system
    assert "Do not restore their values" in system
    assert "all accepted\n  matching receipts" in SYSTEM_PROMPT
    ensure_context_budget(provider.invoke.call_args.args[0])


def test_receipt_extrema_tie_evidence_is_bounded_across_scopes() -> None:
    rows = [_row(i, grand_total=2.58) for i in range(2505)]
    aggregate = aggregate_receipts(rows)
    extrema = aggregate["receipt_total_extrema"]
    assert extrema["minimum"]["matching_receipts"] == 2505
    assert len(json.dumps(extrema).encode()) < 2000
    assert aggregate_view(aggregate)["receipt_total_extrema"] == extrema
    provider = MagicMock()
    provider.invoke.return_value = AIMessage(content="Minimum $2.58.")
    holder = {
        "aggregates": [
            {"source": f"merchant-{i}", **aggregate} for i in range(20)
        ]
    }
    qa_graph.create_synthesize_node(provider, holder)(
        QAState(question="Compare each merchant's lowest receipt totals")
    )
    context = _captured_context(provider)
    assert len(context["precomputed_aggregates"]) == 20
    assert all(
        _expanded_scope(scope, context)["receipt_total_extrema"] == extrema
        for scope in context["precomputed_aggregates"]
    )
    ensure_context_budget(provider.invoke.call_args.args[0])


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


def test_synthesis_budget_limit_does_not_reuse_unsupported_agent_answer() -> (
    None
):
    provider = MagicMock()
    node = qa_graph.create_synthesize_node(provider, {})
    answer = "x" * MAX_CONTEXT_BYTES + " Unsupported cheapest purchase: $2.58."
    result = node(
        QAState(
            question="total",
            messages=[AIMessage(content=answer)],
        )
    )
    assert "Please narrow" in result["final_answer"]
    assert "$2.58" not in result["final_answer"]
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
