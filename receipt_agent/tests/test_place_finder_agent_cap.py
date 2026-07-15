"""Tests for the constrained Tier 3 place agent."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from receipt_agent.subagents.place_finder.graph import (
    create_receipt_place_finder_graph,
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
