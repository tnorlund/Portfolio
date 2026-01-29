"""Container Lambda handler: run all 32 marquee questions through the QA graph.

Single Lambda invocation runs all questions concurrently with asyncio
(semaphore-based concurrency control). Writes NDJSON results and extracts
receipt keys to S3. This replaces the separate ListQuestions + Map + Aggregate
pattern with a single invocation to minimize cold starts and Lambda costs.
"""

import asyncio
import json
import logging
import os
import time
from threading import Lock
from typing import Any

import boto3
from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

# All questions from QuestionMarquee.tsx
QUESTIONS = [
    "How much did I spend on groceries last month?",
    "What was my total spending at Costco?",
    "Show me all receipts with dairy products",
    "How much did I spend on coffee this year?",
    "What's my average grocery bill?",
    "Find all receipts from restaurants",
    "How much tax did I pay last quarter?",
    "What was my largest purchase this month?",
    "Show receipts with items over $50",
    "How much did I spend on organic products?",
    "What's the total for all gas station visits?",
    "Find all receipts with produce items",
    "How much did I spend on snacks?",
    "Show me pharmacy receipts from last week",
    "What was my food spending trend over 6 months?",
    "Find duplicate purchases across stores",
    "How much did I spend on beverages?",
    "Show all receipts with discounts applied",
    "What's the breakdown by store category?",
    "Find receipts where I bought milk",
    "How much did I spend on household items?",
    "Show receipts from the past 7 days",
    "What's my monthly spending average?",
    "Find all breakfast item purchases",
    "How much did I spend on pet food?",
    "Show receipts with loyalty points earned",
    "What was my cheapest grocery trip?",
    "Find all receipts with returns or refunds",
    "How much did I spend on frozen foods?",
    "Show me spending patterns by day of week",
    "What's my average tip at restaurants?",
    "Find receipts with handwritten notes",
]

CONCURRENCY = 10


class CostTrackingCallback(BaseCallbackHandler):
    """Callback handler that tracks OpenRouter API costs.

    Extracts cost from OpenRouter's token_usage response field.
    Thread-safe for concurrent use across asyncio tasks.
    """

    def __init__(self):
        self.total_cost = 0.0
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self._lock = Lock()

    def on_llm_end(self, response, **kwargs):
        """Track cost from LLM response and add to LangSmith run."""
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

        # Add cost to LangSmith run via usage_metadata (populates the cost column)
        if cost > 0:
            try:
                from langsmith.run_helpers import get_current_run_tree

                run_tree = get_current_run_tree()
                if run_tree:
                    run_tree.set(
                        usage_metadata={
                            "input_tokens": prompt_tokens,
                            "output_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                            "total_cost": cost,
                        }
                    )
            except Exception:
                logger.debug("Could not add cost to LangSmith run")

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_cost": self.total_cost,
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "llm_calls": self.llm_calls,
            }


async def _run_question(
    semaphore: asyncio.Semaphore,
    answer_question_fn,
    create_qa_graph_fn,
    dynamo_client,
    chroma_client,
    embed_fn,
    question_text: str,
    question_index: int,
) -> dict[str, Any]:
    """Run a single question with semaphore-based concurrency control."""
    async with semaphore:
        graph, state_holder = create_qa_graph_fn(
            dynamo_client=dynamo_client,
            chroma_client=chroma_client,
            embed_fn=embed_fn,
        )

        cost_callback = CostTrackingCallback()
        start_time = time.time()

        try:
            result = await answer_question_fn(
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
                "toolInvocations": len(state_holder.get("searches", [])),
                "durationSeconds": round(duration, 1),
                "success": True,
            }
        except Exception as e:
            logger.exception("Error on question %d: %s", question_index, question_text[:60])
            return {
                "questionIndex": question_index,
                "question": question_text,
                "answer": f"Error: {e}",
                "totalAmount": None,
                "receiptCount": 0,
                "evidence": [],
                "cost": 0,
                "llmCalls": 0,
                "toolInvocations": 0,
                "durationSeconds": round(time.time() - start_time, 1),
                "success": False,
                "error": str(e),
            }


async def _run_all(
    execution_id: str, batch_bucket: str, langchain_project: str
) -> dict[str, Any]:
    """Run all questions concurrently and write results to S3."""
    from receipt_agent.agents.question_answering import (
        answer_question,
        create_qa_graph,
    )
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_dynamo_client,
        create_embed_fn,
    )

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = langchain_project

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "")
    dynamo_client = create_dynamo_client(table_name=table_name)
    chroma_client = create_chroma_client(mode="read")
    embed_fn = create_embed_fn()

    semaphore = asyncio.Semaphore(CONCURRENCY)

    tasks = [
        _run_question(
            semaphore,
            answer_question,
            create_qa_graph,
            dynamo_client,
            chroma_client,
            embed_fn,
            question_text,
            i,
        )
        for i, question_text in enumerate(QUESTIONS)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results: handle any exceptions from gather
    processed: list[dict[str, Any]] = []
    receipt_keys: set[tuple[str, str]] = set()

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            processed.append(
                {
                    "questionIndex": i,
                    "question": QUESTIONS[i],
                    "answer": f"Error: {result}",
                    "totalAmount": None,
                    "receiptCount": 0,
                    "evidence": [],
                    "cost": 0,
                    "llmCalls": 0,
                    "toolInvocations": 0,
                    "durationSeconds": 0,
                    "success": False,
                    "error": str(result),
                }
            )
        else:
            processed.append(result)
            for e in result.get("evidence", []):
                image_id = e.get("imageId") or e.get("image_id")
                receipt_id = e.get("receiptId") or e.get("receipt_id")
                if image_id and receipt_id:
                    receipt_keys.add((image_id, str(receipt_id)))

    # Write NDJSON (one JSON line per question result)
    ndjson_key = f"qa-runs/{execution_id}/question-results.ndjson"
    ndjson_lines = "\n".join(json.dumps(r, default=str) for r in processed)

    s3_client.put_object(
        Bucket=batch_bucket,
        Key=ndjson_key,
        Body=ndjson_lines.encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    success_count = sum(1 for r in processed if r.get("success"))
    total_cost = sum(r.get("cost", 0) for r in processed)

    logger.info(
        "Completed %d/%d questions ($%.4f total cost)",
        success_count,
        len(QUESTIONS),
        total_cost,
    )

    # Flush LangSmith traces before Lambda terminates
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        logger.info("Flushing LangSmith traces...")
        wait_for_all_tracers()
        logger.info("LangSmith traces flushed")
    except Exception:
        logger.exception("Failed to flush LangSmith traces")

    return {
        "execution_id": execution_id,
        "batch_bucket": batch_bucket,
        "langchain_project": langchain_project,
        "receipt_keys": [{"image_id": k[0], "receipt_id": k[1]} for k in receipt_keys],
        "total_questions": len(QUESTIONS),
        "success_count": success_count,
        "results_ndjson_key": ndjson_key,
        "total_cost": total_cost,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point. Runs all 32 questions through the QA graph.

    Args:
        event: Contains optional 'execution_id' and 'langchain_project'.
               If langchain_project is not provided, defaults to
               'qa-agent-marquee-YYYYMMDD-HHMMSS'.

    Returns:
        dict with receipt_keys, question counts, NDJSON path, and total cost.
    """
    execution_id = event.get("execution_id") or context.aws_request_id
    batch_bucket = os.environ["BATCH_BUCKET"]

    langchain_project = event.get("langchain_project")
    if not langchain_project:
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        langchain_project = f"qa-agent-marquee-{timestamp}"

    logger.info(
        "Starting QA pipeline: %d questions, concurrency=%d, project=%s",
        len(QUESTIONS),
        CONCURRENCY,
        langchain_project,
    )

    return asyncio.run(_run_all(execution_id, batch_bucket, langchain_project))
