"""Container Lambda handler: run one question through the 5-node QA graph.

This handler is invoked by the Step Function Map state, once per question.
It creates a fresh QA graph instance and runs the question through it.
LangSmith tracing is enabled with the 'qa-agent-marquee' project.
"""

import asyncio
import logging
import os
import time
from threading import Lock
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class CostTrackingCallback:
    """Callback handler that tracks OpenRouter API costs.

    Extracts cost from OpenRouter's token_usage response field.
    """

    def __init__(self):
        self.total_cost = 0.0
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self._lock = Lock()

    def on_llm_end(self, response, **kwargs):
        """Track cost from LLM response."""
        cost = 0.0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
            if token_usage:
                cost = token_usage.get("cost", 0) or 0
                if cost == 0:
                    cost_details = token_usage.get("cost_details", {})
                    cost = cost_details.get("upstream_inference_cost", 0) or 0
                total_tokens = token_usage.get("total_tokens", 0) or 0
                prompt_tokens = token_usage.get("prompt_tokens", 0) or 0
                completion_tokens = token_usage.get("completion_tokens", 0) or 0

        with self._lock:
            self.llm_calls += 1
            self.total_cost += cost
            self.total_tokens += total_tokens
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_cost": self.total_cost,
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "llm_calls": self.llm_calls,
            }


async def _run(
    question_text: str, question_index: int, execution_id: str
) -> dict[str, Any]:
    """Run a single question through the QA graph."""
    from receipt_agent.agents.question_answering import (
        answer_question,
        create_qa_graph,
    )
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_dynamo_client,
        create_embed_fn,
    )

    # Enable LangSmith tracing
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = "qa-agent-marquee"

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "")
    dynamo_client = create_dynamo_client(table_name=table_name)
    chroma_client = create_chroma_client(mode="read")
    embed_fn = create_embed_fn()

    graph, state_holder = create_qa_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    cost_callback = CostTrackingCallback()
    start_time = time.time()

    result = await answer_question(
        graph,
        state_holder,
        question_text,
        callbacks=[cost_callback],
    )

    duration = time.time() - start_time
    stats = cost_callback.get_stats()

    return {
        "questionIndex": question_index,
        "question": question_text,
        "answer": result.get("answer", ""),
        "totalAmount": result.get("total_amount"),
        "receiptCount": result.get("receipt_count", 0),
        "evidence": result.get("evidence", []),
        "cost": stats["total_cost"],
        "llmCalls": stats["llm_calls"],
        "toolInvocations": stats["total_tokens"],
        "durationSeconds": round(duration, 1),
        "success": True,
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Lambda entry point. Runs one question through the QA graph.

    Args:
        event: Contains 'question' (dict with text/index) and 'execution_id'.

    Returns:
        dict with question results, answer, evidence, and stats.
    """
    question = event["question"]
    execution_id = event.get("execution_id", "unknown")

    logger.info(
        "Running question %d: %s",
        question["index"],
        question["text"][:60],
    )

    try:
        result = asyncio.run(
            _run(question["text"], question["index"], execution_id)
        )
        logger.info(
            "Question %d completed: cost=$%.6f, receipts=%d",
            question["index"],
            result["cost"],
            result["receiptCount"],
        )
        return result
    except Exception as e:
        logger.exception("Error running question %d", question["index"])
        return {
            "questionIndex": question["index"],
            "question": question["text"],
            "answer": f"Error: {str(e)}",
            "totalAmount": None,
            "receiptCount": 0,
            "evidence": [],
            "cost": 0,
            "llmCalls": 0,
            "toolInvocations": 0,
            "durationSeconds": 0,
            "success": False,
            "error": str(e),
        }
