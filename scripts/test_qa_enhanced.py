#!/usr/bin/env python3
"""
Run all QuestionMarquee questions through the ENHANCED QA agent with LangSmith tracing.

This script uses the new 5-node ReAct RAG workflow:
- plan: Question classification
- agent: Tool loop with classification context
- tools: Tool execution
- shape: Context processing
- synthesize: Answer generation

Usage:
    python scripts/test_qa_enhanced.py --env dev

    # Run with verbose logging
    python scripts/test_qa_enhanced.py --env dev --verbose

    # Run only first N questions
    python scripts/test_qa_enhanced.py --env dev --limit 5
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
from receipt_agent.clients.factory import (
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
)
from receipt_agent.config.settings import get_settings
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
    question_type: Optional[str]
    retrieval_strategy: Optional[str]
    answer: str
    total_amount: Optional[float]
    receipt_count: int
    duration_seconds: float
    iteration_count: int
    error: Optional[str] = None


# Semaphore for controlling concurrency
_semaphore: Optional[asyncio.Semaphore] = None


def setup_langsmith_tracing(config: dict, secrets: dict, project_name: str) -> None:
    """Set up LangSmith tracing environment variables."""
    # Enable tracing
    os.environ["LANGCHAIN_TRACING_V2"] = "true"

    # Set API key
    langchain_api_key = secrets.get("portfolio:LANGCHAIN_API_KEY") or secrets.get(
        "LANGCHAIN_API_KEY"
    )
    if langchain_api_key:
        os.environ["LANGCHAIN_API_KEY"] = langchain_api_key
        logger.info("LangSmith tracing enabled with API key from Pulumi")
    else:
        logger.warning("LANGCHAIN_API_KEY not found in secrets")

    # Set project name
    os.environ["LANGCHAIN_PROJECT"] = project_name
    logger.info("LangSmith project: %s", project_name)

    # Set tenant ID if available
    tenant_id = config.get("langsmith_tenant_id")
    if tenant_id:
        os.environ["LANGSMITH_TENANT_ID"] = tenant_id


async def run_question_with_enhanced_graph(
    question: str,
    index: int,
    total: int,
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Any,
    settings: Any,
) -> QuestionResult:
    """Run a single question with enhanced ReAct RAG graph."""
    global _semaphore

    async with _semaphore:
        logger.info("Question %d/%d: %s", index, total, question[:50])
        start_time = time.time()

        try:
            # Create enhanced graph (use_enhanced=True)
            graph, state_holder = create_qa_graph(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                settings=settings,
            )

            result = await answer_question(graph, state_holder, question)
            duration = time.time() - start_time

            # Extract classification info - check both dict and object forms
            classification = state_holder.get("classification")
            question_type = None
            retrieval_strategy = None
            if classification:
                if isinstance(classification, dict):
                    question_type = classification.get("question_type")
                    retrieval_strategy = classification.get("retrieval_strategy")
                else:
                    question_type = getattr(classification, "question_type", None)
                    retrieval_strategy = getattr(
                        classification, "retrieval_strategy", None
                    )

            # Also check result for classification data
            if not question_type and isinstance(result, dict):
                question_type = result.get("question_type")

            iteration_count = state_holder.get("iteration_count", 0)

            logger.info(
                "Question %d/%d completed in %.1fs (type=%s, iterations=%d)",
                index,
                total,
                duration,
                question_type,
                iteration_count,
            )

            return QuestionResult(
                question=question,
                question_type=question_type,
                retrieval_strategy=retrieval_strategy,
                answer=result.get("answer", "No answer"),
                total_amount=result.get("total_amount"),
                receipt_count=result.get("receipt_count", 0),
                duration_seconds=duration,
                iteration_count=iteration_count,
                error=None,
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error("Error running question %d '%s': %s", index, question[:50], e)
            import traceback

            traceback.print_exc()
            return QuestionResult(
                question=question,
                question_type=None,
                retrieval_strategy=None,
                answer=f"Error: {str(e)}",
                total_amount=None,
                receipt_count=0,
                duration_seconds=duration,
                iteration_count=0,
                error=str(e),
            )


def print_result(result: QuestionResult, index: int) -> None:
    """Print a single question result."""
    print(f"\n{'='*70}")
    print(f"Question {index}: {result.question}")
    print(f"{'='*70}")
    print(f"Type: {result.question_type or 'N/A'}")
    print(f"Strategy: {result.retrieval_strategy or 'N/A'}")
    print(f"Answer: {result.answer[:500]}{'...' if len(result.answer) > 500 else ''}")

    if result.total_amount is not None:
        print(f"Total Amount: ${result.total_amount:.2f}")

    print(f"Receipt Count: {result.receipt_count}")
    print(f"Iterations: {result.iteration_count}")
    print(f"Duration: {result.duration_seconds:.1f}s")

    if result.error:
        print(f"Error: {result.error}")


def print_summary(results: list[QuestionResult]) -> None:
    """Print summary of all results."""
    print("\n" + "=" * 70)
    print("ENHANCED REACT RAG SUMMARY")
    print("=" * 70)

    total_questions = len(results)
    errors = sum(1 for r in results if r.error)
    answered = sum(1 for r in results if not r.error and "Error" not in r.answer)
    with_amounts = sum(1 for r in results if r.total_amount is not None)

    avg_duration = sum(r.duration_seconds for r in results) / total_questions
    total_duration = sum(r.duration_seconds for r in results)
    avg_iterations = sum(r.iteration_count for r in results) / total_questions

    print(f"\nTotal Questions: {total_questions}")
    print(f"Successfully Answered: {answered}")
    print(f"With Amounts: {with_amounts}")
    print(f"Errors: {errors}")
    print(f"\nAvg Duration: {avg_duration:.1f}s")
    print(f"Total Duration: {total_duration:.1f}s ({total_duration/60:.1f}m)")
    print(f"Avg Iterations: {avg_iterations:.1f}")

    # Question type breakdown
    type_counts = {}
    for r in results:
        t = r.question_type or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"\nQuestion Types: {type_counts}")

    print(f"\nLangSmith Project: {os.environ.get('LANGCHAIN_PROJECT', 'N/A')}")
    print("View traces at: https://smith.langchain.com/")

    # Quick results table
    print("\n" + "-" * 80)
    print("QUICK RESULTS")
    print("-" * 80)
    print(
        f"{'#':<3} {'Question':<35} {'Type':<12} {'Amount':<10} {'Iter':<5} {'Time':<6}"
    )
    print("-" * 80)

    for i, r in enumerate(results, 1):
        q_short = r.question[:32] + "..." if len(r.question) > 35 else r.question
        amount = f"${r.total_amount:.2f}" if r.total_amount else "-"
        q_type = (r.question_type or "?")[:10]
        status = "ERR" if r.error else ""
        print(
            f"{i:<3} {q_short:<35} {q_type:<12} {amount:<10} {r.iteration_count:<5} {r.duration_seconds:.1f}s {status}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run QuestionMarquee questions through ENHANCED ReAct RAG QA agent"
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
        default=3,
        help="Number of questions to run in parallel (default: 3)",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="qa-react-rag-enhanced",
        help="LangSmith project name (default: qa-react-rag-enhanced)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment config and secrets
    logger.info("Loading %s environment config...", args.env.upper())
    config = load_env(env=args.env)
    secrets = load_secrets(env=args.env)

    # Set up LangSmith tracing with custom project
    setup_langsmith_tracing(config, secrets, args.project)

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
    settings = get_settings()

    # Determine questions to run
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS

    print(f"\n{'='*70}")
    print("ENHANCED REACT RAG QA AGENT TEST")
    print(f"{'='*70}")
    print(f"Questions: {len(questions)}")
    print(f"Project: {args.project}")
    print(f"Tracing: {os.environ.get('LANGCHAIN_TRACING_V2')}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Workflow: 5-node ReAct RAG (plan → agent ⟷ tools → shape → synthesize)")
    print(f"{'='*70}")

    # Run all questions in parallel
    async def run_all_questions():
        global _semaphore
        _semaphore = asyncio.Semaphore(args.concurrency)

        tasks = [
            run_question_with_enhanced_graph(
                question=q,
                index=i,
                total=len(questions),
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                settings=settings,
            )
            for i, q in enumerate(questions, 1)
        ]

        return await asyncio.gather(*tasks)

    logger.info(
        "Starting enhanced ReAct RAG execution with concurrency=%d", args.concurrency
    )
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
                "question_type": r.question_type,
                "retrieval_strategy": r.retrieval_strategy,
                "answer": r.answer,
                "total_amount": r.total_amount,
                "receipt_count": r.receipt_count,
                "duration_seconds": r.duration_seconds,
                "iteration_count": r.iteration_count,
                "error": r.error,
            }
            for r in results
        ]

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)

        logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
