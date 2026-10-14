"""Enforce deployment routing after LangChain merges per-call options."""

import json
from typing import Any
from uuid import uuid4

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from receipt_agent.exceptions import ReceiptAgentConfigurationError


def restrict_provider_options(
    extra_body: dict[str, Any], allowed: tuple[str, ...]
) -> dict[str, Any]:
    """Permit narrower requests, never removal or broadening of the route."""
    provider = extra_body.get("provider", {})
    if not isinstance(provider, dict):
        raise ReceiptAgentConfigurationError(
            "OpenRouter provider options must be an object"
        )
    requested = provider.get("only", list(allowed))
    if (
        not isinstance(requested, list)
        or not requested
        or any(item not in allowed for item in requested)
    ):
        raise ReceiptAgentConfigurationError(
            "Request provider allowlist exceeds OPENROUTER_PROVIDER_ONLY"
        )
    return {
        **extra_body,
        "provider": {
            **provider,
            "only": list(requested),
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


class RestrictedOpenRouterChat(ChatOpenAI):
    """Keep routing restrictions on sync, async, structured and bound calls."""

    allowed_openrouter_providers: tuple[str, ...] = Field(exclude=True)

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        forced_format = kwargs.pop("_forced_response_format", None)
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        extra_body = dict(payload.get("extra_body") or {})
        if forced_format is not None:
            # Keep schema serialization out of the SDK's parse helper, which
            # rejects non-strict historical tool definitions. The wire body
            # still contains the standard response_format field.
            payload.pop("response_format", None)
            extra_body["response_format"] = forced_format
            extra_body.pop("tool_choice", None)
            payload["tool_choice"] = "none"
        payload["extra_body"] = restrict_provider_options(
            extra_body, self.allowed_openrouter_providers
        )
        if payload.get("model") == "anthropic/claude-haiku-5.5":
            payload.pop("temperature", None)
            # ChatOpenAI does not round-trip OpenRouter reasoning_details.
            # Keep every turn (including structured final submissions) free of
            # thinking metadata that the next tool continuation would lose.
            payload["extra_body"]["reasoning"] = {"enabled": False}
        return payload

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Runnable:
        """Use structured output for Haiku's forced single-tool submission.

        Its Anthropic endpoint currently supports only auto/none tool_choice.
        A single forced function is equivalent to generating that function's
        arguments under its JSON schema, then exposing the validated result as
        a tool call. Multi-tool forced choices are not silently weakened.
        """
        if self.model_name != "anthropic/claude-haiku-5.5" or tool_choice in (
            None,
            "auto",
            "none",
            False,
        ):
            return super().bind_tools(
                tools, tool_choice=tool_choice, strict=strict, **kwargs
            )
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        selected = (
            tool_choice.get("function", {}).get("name")
            if isinstance(tool_choice, dict)
            else tool_choice
        )
        if selected in ("any", "required", True):
            candidates = list(range(len(formatted)))
        else:
            candidates = [
                index
                for index, tool in enumerate(formatted)
                if tool.get("function", {}).get("name") == selected
            ]
        if len(candidates) != 1:
            raise ReceiptAgentConfigurationError(
                "Haiku forced submission must identify one function tool"
            )
        index = candidates[0]
        function = formatted[index]["function"]
        name = function["name"]
        original = tools[index]
        argument_model = getattr(original, "args_schema", None)
        if isinstance(original, type) and issubclass(original, BaseModel):
            argument_model = original
        schema = {"title": name, **function["parameters"]}
        # Both strict=True and SDK parsing of a Pydantic response_format
        # rewrite typed additionalProperties to false. Send the original JSON
        # schema and validate with the tool's Pydantic model after the call.
        structured = self.bind(
            _forced_response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "schema": schema,
                    "strict": False,
                },
            },
            tools=formatted,
            tool_choice="none",
            **kwargs,
        )

        def to_tool_call(raw: AIMessage) -> AIMessage:
            if not isinstance(raw.content, str):
                raise OutputParserException(
                    "Haiku forced submission did not return JSON arguments"
                )
            try:
                parsed = json.loads(raw.content)
            except ValueError as exc:
                raise OutputParserException(
                    "Haiku forced submission did not return JSON arguments"
                ) from exc
            if not isinstance(parsed, dict):
                raise OutputParserException(
                    "Haiku forced submission arguments must be an object"
                )
            if isinstance(argument_model, type) and issubclass(
                argument_model, BaseModel
            ):
                parsed = argument_model.model_validate(parsed)
            arguments = (
                parsed.model_dump(mode="json")
                if isinstance(parsed, BaseModel)
                else parsed
            )
            return raw.model_copy(
                update={
                    "content": "",
                    "tool_calls": [
                        {
                            "name": name,
                            "args": arguments,
                            "id": "call_" + uuid4().hex,
                            "type": "tool_call",
                        }
                    ],
                    "invalid_tool_calls": [],
                }
            )

        return structured | RunnableLambda(to_tool_call)
