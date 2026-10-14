"""Wire-level routing and tool-continuation regression tests (no network)."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel

from receipt_agent.exceptions import ReceiptAgentConfigurationError
from receipt_agent.subagents.place_finder.graph import (
    create_place_submission_tool,
)
from receipt_agent.utils.llm_factory import create_llm


class Answer(BaseModel):
    valid: bool


def completion(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": "anthropic/claude-haiku-5.5",
            "choices": [
                {"index": 0, "message": message, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
    )


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "extra_body", [{}, {"provider": {"allow_fallbacks": True}}]
)
@pytest.mark.asyncio
async def test_async_per_call_options_cannot_remove_restrictions(
    monkeypatch: pytest.MonkeyPatch, structured: bool, extra_body: dict
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')
    requests_seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_seen.append(body)
        assert body["provider"] == {
            "only": ["anthropic"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert "temperature" not in body
        if not structured:
            assert body["reasoning"] == {"enabled": False}
        return completion({"role": "assistant", "content": '{"valid":true}'})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        llm = create_llm(
            model="anthropic/claude-haiku-5.5",
            api_key="synthetic",
            http_async_client=client,
            max_retries=0,
        )
        bound = (
            llm.with_structured_output(Answer, method="json_schema")
            if structured
            else llm.bind_tools([Answer])
        )
        await bound.ainvoke(
            "Synthetic", extra_body=extra_body, temperature=0.9
        )
    assert len(requests_seen) == 1


@pytest.mark.parametrize("structured", [False, True])
def test_per_call_provider_broadening_fails_before_transport(
    monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')

    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("A broadened route reached the transport")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        llm = create_llm(
            model="anthropic/claude-haiku-5.5",
            api_key="synthetic",
            http_client=client,
        )
        bound = (
            llm.with_structured_output(Answer, method="json_schema")
            if structured
            else llm.bind_tools([Answer])
        )
        with pytest.raises(ReceiptAgentConfigurationError):
            bound.invoke(
                "Synthetic",
                extra_body={"provider": {"only": ["google-vertex"]}},
            )


def test_tool_result_round_trip_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')
    bodies = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        assert body["reasoning"] == {"enabled": False}
        assert body["provider"]["only"] == ["anthropic"]
        if len(bodies) == 1:
            return completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "test-call",
                            "type": "function",
                            "function": {
                                "name": "Answer",
                                "arguments": '{"valid":true}',
                            },
                        }
                    ],
                }
            )
        assert body["messages"][-1]["tool_call_id"] == "test-call"
        return completion({"role": "assistant", "content": "Done"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        llm = create_llm(
            model="anthropic/claude-haiku-5.5",
            api_key="synthetic",
            http_client=client,
        )
        bound = llm.bind_tools([Answer])
        result = bound.invoke("Synthetic")
        bound.invoke(
            [
                ("user", "Synthetic"),
                result,
                ToolMessage(content="Success", tool_call_id="test-call"),
            ]
        )
    assert len(bodies) == 2


@pytest.mark.parametrize("choice", ["Answer", "required"])
def test_forced_single_tool_uses_schema_and_preserves_usage(
    monkeypatch: pytest.MonkeyPatch, choice: str
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "Answer"
        assert body["tool_choice"] == "none"
        assert body["response_format"]["type"] == "json_schema"
        assert body["provider"]["only"] == ["anthropic"]
        return completion({"role": "assistant", "content": '{"valid":true}'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        llm = create_llm(
            model="anthropic/claude-haiku-5.5",
            api_key="synthetic",
            http_client=client,
        )
        result = llm.bind_tools([Answer], tool_choice=choice).invoke(
            "Synthetic"
        )
    assert result.tool_calls[0]["name"] == "Answer"
    assert result.tool_calls[0]["args"] == {"valid": True}
    assert result.usage_metadata["total_tokens"] == 5


def test_ambiguous_forced_tool_choice_fails_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')
    llm = create_llm(model="anthropic/claude-haiku-5.5", api_key="synthetic")
    with pytest.raises(ReceiptAgentConfigurationError):
        llm.bind_tools([Answer, Answer], tool_choice="required")


@pytest.mark.asyncio
async def test_real_submission_preserves_tool_history_and_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", '["anthropic"]')
    submit = create_place_submission_tool({})
    arguments = {
        "merchant_name": "Synthetic Market",
        "reasoning": "Synthetic evidence",
        "field_confidence": {"merchant_name": 0.9},
        "sources": {"merchant_name": "receipt_content"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert {tool["function"]["name"] for tool in body["tools"]} == {
            "Answer",
            "submit_place",
        }
        assert body["tool_choice"] == "none"
        assert body["messages"][-1]["tool_call_id"] == "earlier"
        properties = body["response_format"]["json_schema"]["schema"][
            "properties"
        ]
        for field, value_type in [
            ("field_confidence", "number"),
            ("sources", "string"),
        ]:
            assert set(properties[field]["properties"]) == {
                "place_id",
                "merchant_name",
                "address",
                "phone_number",
            }
            assert (
                properties[field]["properties"]["merchant_name"]["type"]
                == value_type
            )
        assert body["reasoning"] == {"enabled": False}
        return completion(
            {"role": "assistant", "content": json.dumps(arguments)}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        llm = create_llm(
            model="anthropic/claude-haiku-5.5",
            api_key="synthetic",
            http_async_client=client,
        )
        forced = llm.bind_tools([Answer, submit], tool_choice="submit_place")
        result = await forced.ainvoke(
            [
                ("user", "Synthetic test"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "Answer",
                            "args": {"valid": True},
                            "id": "earlier",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content="Synthetic result", tool_call_id="earlier"
                ),
            ],
            extra_body={},
        )
    assert (
        result.tool_calls[0]["args"]["field_confidence"]
        == arguments["field_confidence"]
    )
    assert result.tool_calls[0]["args"]["sources"] == arguments["sources"]
