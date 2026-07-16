"""Tests for constrained and legacy place-agent execution."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from receipt_agent.agents.place_id_finder.graph import run_place_id_finder
from receipt_agent.subagents.place_finder.graph import (
    _compact_agent_messages,
    create_receipt_place_finder_graph,
    run_receipt_place_finder,
)
from receipt_agent.subagents.place_finder.state import ReceiptPlaceFinderState


def _settings():
    return SimpleNamespace(
        openrouter_model="test-model",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_api_key=SimpleNamespace(
            get_secret_value=lambda: "test-key"
        ),
    )


def _submit_message():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "submit_place",
                "args": {
                    "place_id": "ChIJ-test",
                    "merchant_name": "Test Market",
                    "confidence": 0.9,
                    "reasoning": "Best available evidence.",
                },
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )


def test_agent_forces_submit_after_configured_rounds(monkeypatch):
    monkeypatch.setenv("FIX_PLACE_RESOLUTION_MODE", "tiered")
    monkeypatch.setenv("FIX_PLACE_AGENT_MAX_ROUNDS", "3")
    normal_llm = MagicMock()
    normal_llm.invoke.side_effect = lambda _messages: AIMessage(
        content="Still searching"
    )
    forced_llm = MagicMock()
    forced_llm.invoke.return_value = _submit_message()
    base_llm = MagicMock()
    base_llm.bind_tools.side_effect = [normal_llm, forced_llm]

    with (
        patch(
            "receipt_agent.subagents.place_finder.graph.create_agentic_tools",
            return_value=([], {}),
        ),
        patch(
            "receipt_agent.subagents.place_finder.graph.create_llm",
            return_value=base_llm,
        ),
    ):
        graph, state_holder = create_receipt_place_finder_graph(
            dynamo_client=None,
            chroma_client=None,
            embed_fn=lambda _texts: [],
            settings=_settings(),
        )
        graph.invoke(
            ReceiptPlaceFinderState(
                image_id="image-1",
                receipt_id=1,
                messages=[HumanMessage(content="Find the place")],
            ),
            config={"recursion_limit": 12},
        )

    assert normal_llm.invoke.call_count == 3
    forced_llm.invoke.assert_called_once()
    assert state_holder["place_result"]["place_id"] == "ChIJ-test"


def test_submit_tool_ends_without_an_extra_llm_turn():
    normal_llm = MagicMock()
    normal_llm.invoke.return_value = _submit_message()
    forced_llm = MagicMock()
    base_llm = MagicMock()
    base_llm.bind_tools.side_effect = [normal_llm, forced_llm]

    with (
        patch(
            "receipt_agent.subagents.place_finder.graph.create_agentic_tools",
            return_value=([], {}),
        ),
        patch(
            "receipt_agent.subagents.place_finder.graph.create_llm",
            return_value=base_llm,
        ),
    ):
        graph, state_holder = create_receipt_place_finder_graph(
            dynamo_client=None,
            chroma_client=None,
            embed_fn=lambda _texts: [],
            settings=_settings(),
        )
        graph.invoke(
            ReceiptPlaceFinderState(
                image_id="image-1",
                receipt_id=1,
                messages=[HumanMessage(content="Find the place")],
            ),
            config={"recursion_limit": 12},
        )

    normal_llm.invoke.assert_called_once()
    forced_llm.invoke.assert_not_called()
    assert state_holder["place_result"]["found"] is True


def test_legacy_agent_is_not_forced_to_submit_after_three_rounds(monkeypatch):
    monkeypatch.setenv("FIX_PLACE_RESOLUTION_MODE", "agent")
    monkeypatch.setenv("FIX_PLACE_AGENT_MAX_ROUNDS", "3")
    normal_llm = MagicMock()
    normal_llm.invoke.side_effect = [
        AIMessage(content=f"Still searching, round {round_number}")
        for round_number in range(1, 5)
    ] + [_submit_message()]
    forced_llm = MagicMock()
    base_llm = MagicMock()
    base_llm.bind_tools.side_effect = [normal_llm, forced_llm]

    with (
        patch(
            "receipt_agent.subagents.place_finder.graph.create_agentic_tools",
            return_value=([], {}),
        ),
        patch(
            "receipt_agent.subagents.place_finder.graph.create_llm",
            return_value=base_llm,
        ),
    ):
        graph, state_holder = create_receipt_place_finder_graph(
            dynamo_client=None,
            chroma_client=None,
            embed_fn=lambda _texts: [],
            settings=_settings(),
        )
        graph.invoke(
            ReceiptPlaceFinderState(
                image_id="image-1",
                receipt_id=1,
                messages=[HumanMessage(content="Find the place")],
            ),
            config={"recursion_limit": 20},
        )

    assert normal_llm.invoke.call_count == 5
    forced_llm.invoke.assert_not_called()
    assert state_holder["place_result"]["place_id"] == "ChIJ-test"


def test_legacy_agent_ends_after_twenty_stalled_messages(monkeypatch):
    monkeypatch.setenv("FIX_PLACE_RESOLUTION_MODE", "agent")
    normal_llm = MagicMock()
    normal_llm.invoke.side_effect = lambda _messages: AIMessage(
        content="Still searching"
    )
    forced_llm = MagicMock()
    base_llm = MagicMock()
    base_llm.bind_tools.side_effect = [normal_llm, forced_llm]

    with (
        patch(
            "receipt_agent.subagents.place_finder.graph.create_agentic_tools",
            return_value=([], {}),
        ),
        patch(
            "receipt_agent.subagents.place_finder.graph.create_llm",
            return_value=base_llm,
        ),
    ):
        graph, state_holder = create_receipt_place_finder_graph(
            dynamo_client=None,
            chroma_client=None,
            embed_fn=lambda _texts: [],
            settings=_settings(),
        )
        graph.invoke(
            ReceiptPlaceFinderState(
                image_id="image-1",
                receipt_id=1,
                messages=[HumanMessage(content="Find the place")],
            ),
            config={"recursion_limit": 100},
        )

    assert normal_llm.invoke.call_count == 20
    forced_llm.invoke.assert_not_called()
    assert state_holder.get("place_result") is None


def test_compaction_keeps_tool_calls_paired_with_all_results():
    old_call_ids = [f"old-{index}" for index in range(3)]
    recent_call_ids = [f"recent-{index}" for index in range(3)]

    def tool_call_message(call_ids):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup",
                    "args": {"value": call_id},
                    "id": call_id,
                    "type": "tool_call",
                }
                for call_id in call_ids
            ],
        )

    prefix = [
        SystemMessage(content="system"),
        HumanMessage(content="request"),
    ]
    old_group = [tool_call_message(old_call_ids)] + [
        ToolMessage(content="result", tool_call_id=call_id)
        for call_id in old_call_ids
    ]
    recent_group = [tool_call_message(recent_call_ids)] + [
        ToolMessage(content="result", tool_call_id=call_id)
        for call_id in recent_call_ids
    ]

    compacted = _compact_agent_messages(prefix + old_group + recent_group)

    assert compacted == prefix + recent_group
    available_call_ids = {
        tool_call["id"]
        for message in compacted
        if isinstance(message, AIMessage)
        for tool_call in message.tool_calls
    }
    assert {
        message.tool_call_id
        for message in compacted
        if isinstance(message, ToolMessage)
    } <= available_call_ids


def test_receipt_place_runner_preserves_legacy_recursion_budget(monkeypatch):
    monkeypatch.setenv("FIX_PLACE_RESOLUTION_MODE", "agent")
    monkeypatch.setenv("FIX_PLACE_AGENT_RECURSION_LIMIT", "12")
    graph = MagicMock()
    graph.ainvoke = AsyncMock()
    state_holder = {"place_result": None}

    asyncio.run(
        run_receipt_place_finder(
            graph,
            state_holder,
            image_id="image-1",
            receipt_id=1,
        )
    )

    assert graph.ainvoke.await_args.kwargs["config"]["recursion_limit"] == 100


def test_upload_place_id_runner_preserves_legacy_recursion_budget():
    graph = MagicMock()
    graph.ainvoke = AsyncMock()
    state_holder = {"place_id_result": None}

    asyncio.run(
        run_place_id_finder(
            graph,
            state_holder,
            image_id="image-1",
            receipt_id=1,
        )
    )

    assert graph.ainvoke.await_args.kwargs["config"]["recursion_limit"] == 100
