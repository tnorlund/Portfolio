"""OpenRouter cost extraction and LangSmith propagation tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_openai import ChatOpenAI

from receipt_agent.utils.llm_factory import CostTrackingCallback


class TestCostTrackingCallback:
    """Tests for OpenRouter cost extraction and LangSmith propagation."""

    @patch("langsmith.run_helpers.get_current_run_tree")
    def test_tracks_openrouter_cost_and_sets_langsmith_usage(
        self, mock_get_current_run_tree
    ):
        run_tree = MagicMock()
        mock_get_current_run_tree.return_value = run_tree
        callback = CostTrackingCallback()
        response = SimpleNamespace(
            llm_output={
                "token_usage": {
                    "prompt_tokens": 125,
                    "completion_tokens": 25,
                    "total_tokens": 150,
                    "cost": 0.00175,
                    "cost_details": {
                        "upstream_inference_cost": 0.0015,
                    },
                }
            }
        )

        callback.handler.on_llm_end(response)

        assert callback.get_stats() == {
            "total_cost": 0.00175,
            "total_tokens": 150,
            "prompt_tokens": 125,
            "completion_tokens": 25,
            "llm_calls": 1,
        }
        run_tree.set.assert_called_once_with(
            usage_metadata={
                "input_tokens": 125,
                "output_tokens": 25,
                "total_tokens": 150,
                "total_cost": 0.00175,
            }
        )

    def test_uses_openrouter_upstream_cost_fallback(self):
        callback = CostTrackingCallback()
        response = SimpleNamespace(
            llm_output={
                "token_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "cost_details": {
                        "upstream_inference_cost": 0.0004,
                    },
                }
            }
        )

        with patch(
            "langsmith.run_helpers.get_current_run_tree",
            return_value=None,
        ):
            callback.handler.on_llm_end(response)

        assert callback.get_stats()["total_cost"] == 0.0004

    def test_langchain_preserves_cost_for_tool_call_response(self):
        """ChatOpenAI must retain OpenRouter's extra usage fields."""
        llm = ChatOpenAI(
            model="test-model",
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )
        response = {
            "id": "gen-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "lookup_place",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": 0.002,
            },
        }

        result = llm._create_chat_result(response)
        callback = CostTrackingCallback()
        with patch(
            "langsmith.run_helpers.get_current_run_tree",
            return_value=None,
        ):
            callback.handler.on_llm_end(result)

        assert result.llm_output["token_usage"]["cost"] == 0.002
        assert callback.get_stats()["total_cost"] == 0.002
