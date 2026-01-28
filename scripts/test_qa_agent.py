#!/usr/bin/env python3
"""
Test script for the question-answering agent.

Simple ReAct workflow:
  START → agent ⟷ tools → synthesize → END

Usage:
    # Ask a specific question
    python scripts/test_qa_agent.py --env dev --question "How much did I spend on coffee?"

    # Use Grok 4.1 Fast (good tool calling, accurate)
    python scripts/test_qa_agent.py --env dev --model x-ai/grok-4.1-fast --question "..."

    # Run predefined test questions
    python scripts/test_qa_agent.py --env dev --test-all

    # Interactive mode
    python scripts/test_qa_agent.py --env dev --interactive

Environment variables:
    OPENROUTER_API_KEY: Required for LLM inference
    LANGCHAIN_API_KEY: Optional for LangSmith tracing
"""

import argparse
import json
import logging
import os
import sys
from threading import Lock
from typing import Any

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_agent"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_chroma"))

from receipt_agent.agents.question_answering import (
    answer_question_sync,
    create_qa_graph,
)
from receipt_agent.clients.factory import (
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
)
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class CostTrackingCallback(BaseCallbackHandler):
    """Callback handler that tracks OpenRouter API costs and adds them to LangSmith.

    OpenRouter returns cost directly in llm_output['token_usage']['cost'].
    This callback extracts the cost and adds it to the LangSmith run metadata.
    """

    def __init__(self):
        self.total_cost = 0.0
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self._lock = Lock()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Track cost from LLM response and add to LangSmith run."""
        cost = 0.0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        # OpenRouter returns cost in llm_output['token_usage']
        # For BYOK (Bring Your Own Key), cost=0 but actual cost is in cost_details
        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
            if token_usage:
                cost = token_usage.get("cost", 0) or 0
                # BYOK mode: cost is in cost_details.upstream_inference_cost
                if cost == 0:
                    cost_details = token_usage.get("cost_details", {})
                    cost = cost_details.get("upstream_inference_cost", 0) or 0
                total_tokens = token_usage.get("total_tokens", 0) or 0
                prompt_tokens = token_usage.get("prompt_tokens", 0) or 0
                completion_tokens = token_usage.get("completion_tokens", 0) or 0

        # Update local counters
        with self._lock:
            self.llm_calls += 1
            self.total_cost += cost
            self.total_tokens += total_tokens
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens

        # Add cost to LangSmith run via usage_metadata (this populates the cost column)
        if cost > 0:
            try:
                from langsmith.run_helpers import get_current_run_tree
                run_tree = get_current_run_tree()
                if run_tree:
                    # usage_metadata with total_cost is what LangSmith uses for cost tracking
                    run_tree.set(usage_metadata={
                        "input_tokens": prompt_tokens,
                        "output_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "total_cost": cost,
                    })
            except Exception as e:
                # Don't fail if LangSmith integration doesn't work
                logging.debug("Could not add cost to LangSmith run: %s", e)

    def reset(self):
        """Reset all counters."""
        with self._lock:
            self.total_cost = 0.0
            self.total_tokens = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.llm_calls = 0

    def get_stats(self) -> dict:
        """Get current stats."""
        with self._lock:
            return {
                "total_cost": self.total_cost,
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "llm_calls": self.llm_calls,
            }


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Test questions
TEST_QUESTIONS = [
    "How much did I spend on coffee this year?",
    "Show me all receipts with dairy products",
    "How much tax did I pay last quarter?",
]


def main():
    parser = argparse.ArgumentParser(
        description="Test the question-answering agent"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--question",
        type=str,
        help="Question to ask",
    )
    parser.add_argument(
        "--test-all",
        action="store_true",
        help="Run all test questions",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive mode - ask questions in a loop",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="x-ai/grok-4.1-fast",
        help="OpenRouter model to use (default: x-ai/grok-4.1-fast)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment config and secrets
    logger.info("Loading %s environment config...", args.env.upper())
    config = load_env(env=args.env)
    secrets = load_secrets(env=args.env)

    # Merge secrets into config (normalize keys: portfolio:KEY -> key)
    for key, value in secrets.items():
        # Remove 'portfolio:' prefix and convert to snake_case
        normalized_key = key.replace("portfolio:", "").lower().replace("-", "_")
        config[normalized_key] = value

    # Set API keys from secrets if not already in environment
    if not os.environ.get("OPENROUTER_API_KEY"):
        openrouter_key = config.get("openrouter_api_key")
        if openrouter_key:
            os.environ["OPENROUTER_API_KEY"] = openrouter_key
            logger.info("Loaded OPENROUTER_API_KEY from Pulumi secrets")

    if not os.environ.get("RECEIPT_AGENT_OPENAI_API_KEY"):
        openai_key = config.get("openai_api_key")
        if openai_key:
            os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
            logger.info("Loaded OPENAI_API_KEY from Pulumi secrets")

    # Set LangSmith API key for tracing
    langchain_key = config.get("langchain_api_key")
    if langchain_key:
        os.environ["LANGCHAIN_API_KEY"] = langchain_key
        os.environ["LANGSMITH_API_KEY"] = langchain_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "question-answering-rag"
        logger.info("LangSmith tracing ENABLED (project: question-answering-rag)")
    else:
        logger.warning("LANGCHAIN_API_KEY not found - LangSmith tracing disabled")

    # Set OpenRouter model from args
    os.environ["OPENROUTER_MODEL"] = args.model
    os.environ["RECEIPT_AGENT_OPENROUTER_MODEL"] = args.model
    logger.info("Using OpenRouter model: %s", args.model)

    # Check for OpenRouter API key
    if not os.environ.get("OPENROUTER_API_KEY"):
        logger.error(
            "OPENROUTER_API_KEY not found in environment or Pulumi secrets. "
            "Get one at https://openrouter.ai/"
        )
        sys.exit(1)

    # Create clients
    logger.info("Creating clients...")
    dynamo_client = create_dynamo_client(table_name=config["dynamodb_table_name"])

    # Check for Chroma Cloud config first
    chroma_cloud_api_key = config.get("chroma_cloud_api_key")
    chroma_cloud_tenant = config.get("chroma_cloud_tenant")
    chroma_cloud_database = config.get("chroma_cloud_database")
    chroma_cloud_enabled = config.get("chroma_cloud_enabled", "false").lower() == "true"

    if chroma_cloud_enabled and chroma_cloud_api_key:
        logger.info("Using Chroma Cloud: tenant=%s, database=%s",
                    chroma_cloud_tenant, chroma_cloud_database)
        try:
            chroma_client = ChromaClient(
                cloud_api_key=chroma_cloud_api_key,
                cloud_tenant=chroma_cloud_tenant,
                cloud_database=chroma_cloud_database,
                mode="read",
            )
        except Exception as e:
            logger.error("Failed to create Chroma Cloud client: %s", e)
            sys.exit(1)
    else:
        # Fall back to local ChromaDB paths
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = config.get(
            "chroma_lines_directory", "/tmp/chroma_lines"
        )
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = config.get(
            "chroma_words_directory", "/tmp/chroma_words"
        )

        try:
            chroma_client = create_chroma_client(mode="read")
        except Exception as e:
            logger.error(
                "Failed to create ChromaDB client. Make sure you have downloaded "
                "the ChromaDB snapshots. Error: %s",
                e,
            )
            logger.info(
                "Tip: Download snapshots with: "
                "aws s3 sync s3://<bucket>/lines/snapshot/ /tmp/chroma_lines/ && "
                "aws s3 sync s3://<bucket>/words/snapshot/ /tmp/chroma_words/"
            )
            sys.exit(1)

    embed_fn = create_embed_fn()

    # Create cost tracking callback
    cost_callback = CostTrackingCallback()

    # Create the graph
    logger.info("Creating QA graph (ReAct + synthesize)...")
    graph, state_holder = create_qa_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    def ask_question(question: str) -> dict:
        """Ask a question and print the result."""
        print(f"\n{'=' * 60}")
        print(f"Question: {question}")
        print(f"{'=' * 60}")

        # Reset cost tracking for this question
        cost_callback.reset()

        result = answer_question_sync(
            graph, state_holder, question,
            callbacks=[cost_callback],
        )

        print(f"\nAnswer: {result['answer']}")
        if result.get("total_amount") is not None:
            print(f"Total Amount: ${result['total_amount']:.2f}")
        if result.get("receipt_count"):
            print(f"Receipt Count: {result['receipt_count']}")
        if result.get("evidence"):
            print(f"\nEvidence ({len(result['evidence'])} items):")
            for i, e in enumerate(result["evidence"][:5], 1):
                print(f"  {i}. {json.dumps(e, default=str)[:100]}...")

        # Display cost info
        stats = cost_callback.get_stats()
        print(f"\n--- Cost ---")
        print(f"LLM Calls: {stats['llm_calls']}")
        print(f"Tokens: {stats['total_tokens']} (prompt: {stats['prompt_tokens']}, completion: {stats['completion_tokens']})")
        print(f"Cost: ${stats['total_cost']:.6f}")

        return result

    # Run based on mode
    if args.question:
        ask_question(args.question)

    elif args.test_all:
        print(f"\nRunning {len(TEST_QUESTIONS)} test questions...\n")
        results = []
        for q in TEST_QUESTIONS:
            result = ask_question(q)
            results.append({"question": q, "result": result})

        print(f"\n{'=' * 60}")
        print("Summary")
        print(f"{'=' * 60}")
        for r in results:
            answer = r["result"]["answer"][:80] + "..." if len(r["result"]["answer"]) > 80 else r["result"]["answer"]
            print(f"Q: {r['question'][:50]}...")
            print(f"A: {answer}")
            print()

    elif args.interactive:
        print("\nInteractive mode. Type 'quit' to exit.\n")
        while True:
            try:
                question = input("Question: ").strip()
                if question.lower() in ("quit", "exit", "q"):
                    break
                if not question:
                    continue
                ask_question(question)
            except KeyboardInterrupt:
                print("\n")
                break

    else:
        parser.print_help()
        print("\nSpecify --question, --test-all, or --interactive")


if __name__ == "__main__":
    main()
