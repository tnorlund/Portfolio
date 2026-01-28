#!/usr/bin/env python3
"""
Run all QuestionMarquee questions through the QA agent with LangSmith tracing.

This script:
1. Loads LangSmith API key from Pulumi secrets
2. Enables LangSmith tracing for all runs
3. Runs all 33 questions from QuestionMarquee.tsx
4. Outputs results in a formatted table

Usage:
    python scripts/test_qa_marquee_questions.py --env dev

    # Run with verbose logging
    python scripts/test_qa_marquee_questions.py --env dev --verbose

    # Run only first N questions
    python scripts/test_qa_marquee_questions.py --env dev --limit 5
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_agent"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_chroma"))

from receipt_agent.agents.question_answering.graph import (
    answer_question,
    create_qa_graph,
)
from scripts.test_qa_agent import CostTrackingCallback
from receipt_agent.clients.factory import (
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
)
# Removed get_settings import - using enhanced graph with defaults
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo.data._pulumi import load_env, load_secrets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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


@dataclass
class QuestionResult:
    """Results from a single question run."""

    question: str
    answer: str
    total_amount: Optional[float]
    receipt_count: int
    duration_seconds: float
    cost: float = 0.0
    llm_calls: int = 0
    tokens: int = 0
    error: Optional[str] = None


# Semaphore for controlling concurrency
_semaphore: Optional[asyncio.Semaphore] = None


def setup_langsmith_tracing(config: dict, secrets: dict, project_name: str = "question-answering-marquee") -> None:
    """Set up LangSmith tracing environment variables."""
    # Enable tracing
    os.environ["LANGCHAIN_TRACING_V2"] = "true"

    # Set API key
    langchain_api_key = secrets.get("portfolio:LANGCHAIN_API_KEY") or secrets.get(
        "LANGCHAIN_API_KEY"
    )
    if langchain_api_key:
        os.environ["LANGCHAIN_API_KEY"] = langchain_api_key
        os.environ["LANGSMITH_API_KEY"] = langchain_api_key
        logger.info("LangSmith tracing enabled with API key from Pulumi")
    else:
        logger.warning("LANGCHAIN_API_KEY not found in secrets")

    # Set project name
    os.environ["LANGCHAIN_PROJECT"] = project_name

    # Set tenant ID if available
    tenant_id = config.get("langsmith_tenant_id")
    if tenant_id:
        os.environ["LANGSMITH_TENANT_ID"] = tenant_id


async def run_question_with_own_graph(
    question: str,
    index: int,
    total: int,
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Any,
) -> QuestionResult:
    """Run a single question with its own graph instance (for parallel execution)."""
    global _semaphore

    async with _semaphore:
        logger.info("Question %d/%d: %s", index, total, question[:50])
        start_time = time.time()

        # Create cost tracking callback for this question
        cost_callback = CostTrackingCallback()

        try:
            # Create a fresh graph for this question (thread-safe)
            # Always use enhanced 5-node ReAct RAG workflow
            graph, state_holder = create_qa_graph(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                use_enhanced=True,
            )

            result = await answer_question(
                graph, state_holder, question,
                use_enhanced=True,
                callbacks=[cost_callback],
            )
            duration = time.time() - start_time

            # Get cost stats
            stats = cost_callback.get_stats()

            logger.info(
                "Question %d/%d completed in %.1fs (cost: $%.6f)",
                index, total, duration, stats["total_cost"]
            )
            return QuestionResult(
                question=question,
                answer=result.get("answer", "No answer"),
                total_amount=result.get("total_amount"),
                receipt_count=result.get("receipt_count", 0),
                duration_seconds=duration,
                cost=stats["total_cost"],
                llm_calls=stats["llm_calls"],
                tokens=stats["total_tokens"],
                error=None,
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error("Error running question %d '%s': %s", index, question[:50], e)
            return QuestionResult(
                question=question,
                answer=f"Error: {str(e)}",
                total_amount=None,
                receipt_count=0,
                duration_seconds=duration,
                error=str(e),
            )


def print_result(result: QuestionResult, index: int) -> None:
    """Print a single question result."""
    print(f"\n{'='*70}")
    print(f"Question {index}: {result.question}")
    print(f"{'='*70}")
    print(f"Answer: {result.answer[:500]}{'...' if len(result.answer) > 500 else ''}")

    if result.total_amount is not None:
        print(f"Total Amount: ${result.total_amount:.2f}")

    print(f"Receipt Count: {result.receipt_count}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Cost: ${result.cost:.6f} ({result.llm_calls} calls, {result.tokens} tokens)")

    if result.error:
        print(f"Error: {result.error}")


def print_summary(results: list[QuestionResult]) -> None:
    """Print summary of all results."""
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_questions = len(results)
    errors = sum(1 for r in results if r.error)
    answered = sum(1 for r in results if not r.error and "Error" not in r.answer)
    with_amounts = sum(1 for r in results if r.total_amount is not None)

    avg_duration = sum(r.duration_seconds for r in results) / total_questions
    total_duration = sum(r.duration_seconds for r in results)
    total_cost = sum(r.cost for r in results)
    total_tokens = sum(r.tokens for r in results)
    total_llm_calls = sum(r.llm_calls for r in results)

    print(f"\nTotal Questions: {total_questions}")
    print(f"Successfully Answered: {answered}")
    print(f"With Amounts: {with_amounts}")
    print(f"Errors: {errors}")
    print(f"\nAvg Duration: {avg_duration:.1f}s")
    print(f"Total Duration: {total_duration:.1f}s ({total_duration/60:.1f}m)")
    print(f"\nTotal Cost: ${total_cost:.4f}")
    print(f"Total LLM Calls: {total_llm_calls}")
    print(f"Total Tokens: {total_tokens:,}")
    print(f"Avg Cost per Question: ${total_cost/total_questions:.6f}")

    print(f"\nLangSmith Project: {os.environ.get('LANGCHAIN_PROJECT', 'N/A')}")
    print("View traces at: https://smith.langchain.com/")

    # Quick results table
    print("\n" + "-" * 85)
    print("QUICK RESULTS")
    print("-" * 85)
    print(f"{'#':<3} {'Question':<40} {'Amount':<10} {'Time':<6} {'Cost':<10}")
    print("-" * 85)

    for i, r in enumerate(results, 1):
        q_short = r.question[:37] + "..." if len(r.question) > 40 else r.question
        amount = f"${r.total_amount:.2f}" if r.total_amount else "-"
        cost_str = f"${r.cost:.4f}" if r.cost > 0 else "-"
        status = " ERR" if r.error else ""
        print(f"{i:<3} {q_short:<40} {amount:<10} {r.duration_seconds:.1f}s  {cost_str:<10}{status}")


def main():
    parser = argparse.ArgumentParser(
        description="Run QuestionMarquee questions through QA agent with LangSmith tracing"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of questions to run",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output results to JSON file",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of questions to run in parallel (default: 5)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="google/gemini-2.5-flash",
        help="OpenRouter model to use (default: google/gemini-2.5-flash)",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="qa-marquee-hybrid-search",
        help="LangSmith project name (default: qa-marquee-hybrid-search)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment config and secrets
    logger.info("Loading %s environment config...", args.env.upper())
    config = load_env(env=args.env)
    secrets = load_secrets(env=args.env)

    # Set up LangSmith tracing
    setup_langsmith_tracing(config, secrets, project_name=args.project)

    # Merge secrets into config for API keys
    for key, value in secrets.items():
        normalized_key = key.replace("portfolio:", "").lower().replace("-", "_")
        config[normalized_key] = value

    # Set OpenRouter API key
    if not os.environ.get("OPENROUTER_API_KEY"):
        openrouter_key = config.get("openrouter_api_key")
        if openrouter_key:
            os.environ["OPENROUTER_API_KEY"] = openrouter_key
            logger.info("Loaded OPENROUTER_API_KEY from Pulumi secrets")

    # Set OpenAI API key for embeddings
    if not os.environ.get("RECEIPT_AGENT_OPENAI_API_KEY"):
        openai_key = config.get("openai_api_key")
        if openai_key:
            os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key

    if not os.environ.get("OPENROUTER_API_KEY"):
        logger.error("OPENROUTER_API_KEY not found")
        sys.exit(1)

    # Set OpenRouter model
    os.environ["OPENROUTER_MODEL"] = args.model
    os.environ["RECEIPT_AGENT_OPENROUTER_MODEL"] = args.model
    logger.info("Using model: %s", args.model)

    # Create clients
    logger.info("Creating clients...")
    dynamo_client = create_dynamo_client(table_name=config["dynamodb_table_name"])

    # Check for Chroma Cloud config
    chroma_cloud_api_key = config.get("chroma_cloud_api_key")
    chroma_cloud_tenant = config.get("chroma_cloud_tenant")
    chroma_cloud_database = config.get("chroma_cloud_database")
    chroma_cloud_enabled = config.get("chroma_cloud_enabled", "false").lower() == "true"

    if chroma_cloud_enabled and chroma_cloud_api_key:
        logger.info("Using Chroma Cloud")
        chroma_client = ChromaClient(
            cloud_api_key=chroma_cloud_api_key,
            cloud_tenant=chroma_cloud_tenant,
            cloud_database=chroma_cloud_database,
            mode="read",
        )
    else:
        logger.info("Using local ChromaDB")
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = config.get(
            "chroma_lines_directory", "/tmp/chroma_lines"
        )
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = config.get(
            "chroma_words_directory", "/tmp/chroma_words"
        )
        chroma_client = create_chroma_client(mode="read")

    embed_fn = create_embed_fn()

    # Determine questions to run
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS

    print(f"\nRunning {len(questions)} questions with LangSmith tracing...")
    print(f"Project: {os.environ.get('LANGCHAIN_PROJECT')}")
    print(f"Model: {args.model}")
    print(f"Tracing: {os.environ.get('LANGCHAIN_TRACING_V2')}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Mode: Enhanced 5-node ReAct RAG with hybrid search")

    # Run all questions in parallel
    async def run_all_questions():
        global _semaphore
        _semaphore = asyncio.Semaphore(args.concurrency)

        tasks = [
            run_question_with_own_graph(
                question=q,
                index=i,
                total=len(questions),
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
            )
            for i, q in enumerate(questions, 1)
        ]

        return await asyncio.gather(*tasks)

    logger.info("Starting parallel execution with concurrency=%d", args.concurrency)
    start_time = time.time()
    results = asyncio.run(run_all_questions())
    wall_time = time.time() - start_time
    logger.info("All questions completed in %.1fs wall time", wall_time)

    # Print individual results
    for i, result in enumerate(results, 1):
        print_result(result, i)

    # Print summary
    print_summary(results)

    # Save to JSON if requested
    if args.output:
        output_data = [
            {
                "question": r.question,
                "answer": r.answer,
                "total_amount": r.total_amount,
                "receipt_count": r.receipt_count,
                "duration_seconds": r.duration_seconds,
                "cost": r.cost,
                "llm_calls": r.llm_calls,
                "tokens": r.tokens,
                "error": r.error,
            }
            for r in results
        ]

        # Add summary
        total_cost = sum(r.cost for r in results)
        total_tokens = sum(r.tokens for r in results)
        output_with_summary = {
            "summary": {
                "total_questions": len(results),
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "model": args.model,
                "project": args.project,
            },
            "results": output_data,
        }

        with open(args.output, "w") as f:
            json.dump(output_with_summary, f, indent=2)

        logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
