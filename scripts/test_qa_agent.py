#!/usr/bin/env python3
"""
Test script for the question-answering agent.

Usage:
    # Ask a specific question
    python scripts/test_qa_agent.py --env dev --question "How much did I spend on coffee?"

    # Run predefined test questions
    python scripts/test_qa_agent.py --env dev --test-all

    # Interactive mode
    python scripts/test_qa_agent.py --env dev --interactive

Environment variables:
    OPENROUTER_API_KEY: Required for LLM inference
"""

import argparse
import json
import logging
import os
import sys

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
from receipt_dynamo.data._pulumi import load_env

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

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check for OpenRouter API key
    if not os.environ.get("OPENROUTER_API_KEY"):
        logger.error(
            "OPENROUTER_API_KEY environment variable is required. "
            "Get one at https://openrouter.ai/"
        )
        sys.exit(1)

    # Load environment config
    logger.info("Loading %s environment config...", args.env.upper())
    config = load_env(env=args.env)

    # Create clients
    logger.info("Creating clients...")
    dynamo_client = create_dynamo_client(table_name=config["dynamodb_table_name"])

    # Set up ChromaDB paths from config
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

    # Create the graph
    logger.info("Creating QA graph...")
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

        result = answer_question_sync(graph, state_holder, question)

        print(f"\nAnswer: {result['answer']}")
        if result.get("total_amount") is not None:
            print(f"Total Amount: ${result['total_amount']:.2f}")
        if result.get("receipt_count"):
            print(f"Receipt Count: {result['receipt_count']}")
        if result.get("evidence"):
            print(f"\nEvidence ({len(result['evidence'])} items):")
            for i, e in enumerate(result["evidence"][:5], 1):
                print(f"  {i}. {json.dumps(e, default=str)[:100]}...")

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
