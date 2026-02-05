#!/usr/bin/env python3
"""
LangSmith evaluation script for the question-answering agent.

Evaluates:
- Groundedness: Is the answer supported by retrieved evidence?
- Retrieval Relevance: Are the retrieved documents relevant to the question?
- Tool Choice Correctness: Did the agent use appropriate tools?
- Loop Detection: Did the agent get stuck in repetitive patterns?

Usage:
    # Create a dataset first (one-time)
    python scripts/evaluate_qa_agent.py --create-dataset

    # Run evaluation
    python scripts/evaluate_qa_agent.py --env dev --evaluate

    # Run evaluation with specific model
    python scripts/evaluate_qa_agent.py --env dev --model x-ai/grok-4.1-fast --evaluate
"""

import argparse
import json
import logging
import os
import sys
from typing import Any

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_agent"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_chroma"))

from langsmith import Client, evaluate
from langsmith.schemas import Example, Run

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Golden dataset for evaluation
GOLDEN_EXAMPLES = [
    {
        "question": "How much did I spend on coffee?",
        "expected_answer_contains": ["26.98", "27"],  # $15.99 + $10.99
        "expected_tools": ["search_product_lines", "get_receipt"],
        "notes": "Should find BIRCHWOOD COFFEE ($15.99) and FRENCH ROAST COFFEE ($10.99)",
    },
    {
        "question": "What's my total spending at Costco?",
        "expected_tools": [
            "get_receipt_summaries",
            "get_receipts_by_merchant",
        ],
        "notes": "Should use merchant filtering",
    },
    {
        "question": "How much tax did I pay last month?",
        "expected_tools": ["get_receipt_summaries"],
        "notes": "Should use date filtering with tax aggregation",
    },
    {
        "question": "Show me receipts with dairy products",
        "expected_tools": ["search_product_lines", "search_receipts"],
        "notes": "Should use semantic search for dairy",
    },
]


def create_dataset():
    """Create a LangSmith dataset from golden examples."""
    client = Client()

    dataset_name = "qa-agent-golden"

    # Check if dataset exists
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
        logger.info(
            "Dataset '%s' already exists with %d examples",
            dataset_name,
            dataset.example_count,
        )
        return dataset
    except Exception:
        pass

    # Create new dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Golden test cases for QA agent evaluation",
    )

    # Add examples
    for example in GOLDEN_EXAMPLES:
        client.create_example(
            dataset_id=dataset.id,
            inputs={"question": example["question"]},
            outputs={
                "expected_answer_contains": example.get(
                    "expected_answer_contains", []
                ),
                "expected_tools": example.get("expected_tools", []),
                "notes": example.get("notes", ""),
            },
        )

    logger.info(
        "Created dataset '%s' with %d examples",
        dataset_name,
        len(GOLDEN_EXAMPLES),
    )
    return dataset


# ============================================================================
# Custom Evaluators
# ============================================================================


def groundedness_evaluator(run: Run, example: Example) -> dict:
    """
    Evaluate if the answer is grounded in the retrieved evidence.

    Checks that claims in the answer can be traced back to tool outputs.
    """
    score = 1.0
    reasoning = []

    # Get the final answer from run outputs
    answer = run.outputs.get("answer", "") if run.outputs else ""
    evidence = run.outputs.get("evidence", []) if run.outputs else []

    if not answer:
        return {
            "key": "groundedness",
            "score": 0.0,
            "comment": "No answer provided",
        }

    if not evidence:
        # Check if answer admits no data found
        if any(
            phrase in answer.lower()
            for phrase in ["no data", "couldn't find", "not found"]
        ):
            return {
                "key": "groundedness",
                "score": 1.0,
                "comment": "Correctly indicated no evidence",
            }
        return {
            "key": "groundedness",
            "score": 0.5,
            "comment": "Answer provided without evidence",
        }

    # Check if monetary amounts in answer appear in evidence
    import re

    amounts_in_answer = re.findall(r"\$?(\d+\.?\d*)", answer)

    evidence_text = json.dumps(evidence)
    grounded_amounts = sum(
        1 for amt in amounts_in_answer if amt in evidence_text
    )

    if amounts_in_answer:
        score = grounded_amounts / len(amounts_in_answer)
        reasoning.append(
            f"{grounded_amounts}/{len(amounts_in_answer)} amounts found in evidence"
        )

    return {
        "key": "groundedness",
        "score": score,
        "comment": (
            "; ".join(reasoning) if reasoning else "Answer appears grounded"
        ),
    }


def retrieval_relevance_evaluator(run: Run, example: Example) -> dict:
    """
    Evaluate if retrieved documents are relevant to the question.

    Checks that search queries and retrieved content match the user's intent.
    """
    question = example.inputs.get("question", "")

    # Extract tool calls from run
    tool_calls = []
    if run.child_runs:
        for child in run.child_runs:
            if child.run_type == "tool":
                tool_calls.append(
                    {
                        "name": child.name,
                        "inputs": child.inputs,
                        "outputs": child.outputs,
                    }
                )

    if not tool_calls:
        return {
            "key": "retrieval_relevance",
            "score": 0.5,
            "comment": "No tool calls found",
        }

    # Check if search queries relate to the question
    question_lower = question.lower()
    relevant_searches = 0
    total_searches = 0

    for call in tool_calls:
        if "search" in call["name"].lower():
            total_searches += 1
            query = str(call.get("inputs", {}).get("query", "")).lower()

            # Check keyword overlap
            question_words = set(question_lower.split())
            query_words = set(query.split())

            if question_words & query_words:
                relevant_searches += 1

    if total_searches == 0:
        return {
            "key": "retrieval_relevance",
            "score": 0.7,
            "comment": "No search calls (may be aggregation query)",
        }

    score = relevant_searches / total_searches
    return {
        "key": "retrieval_relevance",
        "score": score,
        "comment": f"{relevant_searches}/{total_searches} searches relevant to question",
    }


def tool_choice_evaluator(run: Run, example: Example) -> dict:
    """
    Evaluate if the agent chose appropriate tools for the task.
    """
    expected_tools = (
        example.outputs.get("expected_tools", []) if example.outputs else []
    )

    if not expected_tools:
        return {
            "key": "tool_choice",
            "score": 1.0,
            "comment": "No expected tools specified",
        }

    # Extract actual tool names from run
    actual_tools = set()
    if run.child_runs:
        for child in run.child_runs:
            if child.run_type == "tool":
                actual_tools.add(child.name)

    # Calculate overlap
    expected_set = set(expected_tools)
    matched = actual_tools & expected_set

    if not expected_set:
        return {
            "key": "tool_choice",
            "score": 1.0,
            "comment": "No expected tools",
        }

    score = len(matched) / len(expected_set)
    missing = expected_set - actual_tools
    extra = actual_tools - expected_set

    comment_parts = [f"Used {len(matched)}/{len(expected_set)} expected tools"]
    if missing:
        comment_parts.append(f"Missing: {missing}")
    if extra:
        comment_parts.append(f"Extra: {extra}")

    return {
        "key": "tool_choice",
        "score": score,
        "comment": "; ".join(comment_parts),
    }


def loop_detection_evaluator(run: Run, example: Example) -> dict:
    """
    Detect if the agent got stuck in repetitive patterns.

    Checks for:
    - Same tool called multiple times with same inputs
    - Excessive number of tool calls
    - Repeated error patterns
    """
    MAX_REASONABLE_CALLS = 15

    if not run.child_runs:
        return {
            "key": "loop_detection",
            "score": 1.0,
            "comment": "No child runs",
        }

    tool_calls = []
    for child in run.child_runs:
        if child.run_type == "tool":
            tool_calls.append(
                {
                    "name": child.name,
                    "inputs_hash": hash(
                        json.dumps(child.inputs, sort_keys=True, default=str)
                    ),
                }
            )

    total_calls = len(tool_calls)

    # Check for excessive calls
    if total_calls > MAX_REASONABLE_CALLS:
        return {
            "key": "loop_detection",
            "score": 0.3,
            "comment": f"Excessive tool calls: {total_calls} (max recommended: {MAX_REASONABLE_CALLS})",
        }

    # Check for duplicate calls (same tool + same inputs)
    seen = set()
    duplicates = 0
    for call in tool_calls:
        key = (call["name"], call["inputs_hash"])
        if key in seen:
            duplicates += 1
        seen.add(key)

    if duplicates > 2:
        score = max(0.0, 1.0 - (duplicates * 0.2))
        return {
            "key": "loop_detection",
            "score": score,
            "comment": f"Detected {duplicates} duplicate tool calls (possible loop)",
        }

    return {
        "key": "loop_detection",
        "score": 1.0,
        "comment": f"No loops detected ({total_calls} tool calls)",
    }


def answer_correctness_evaluator(run: Run, example: Example) -> dict:
    """
    Check if the answer contains expected values.
    """
    expected_contains = (
        example.outputs.get("expected_answer_contains", [])
        if example.outputs
        else []
    )

    if not expected_contains:
        return {
            "key": "answer_correctness",
            "score": 1.0,
            "comment": "No expected values specified",
        }

    answer = run.outputs.get("answer", "") if run.outputs else ""

    if not answer:
        return {
            "key": "answer_correctness",
            "score": 0.0,
            "comment": "No answer provided",
        }

    found = sum(1 for val in expected_contains if val in answer)
    score = found / len(expected_contains)

    return {
        "key": "answer_correctness",
        "score": score,
        "comment": f"Found {found}/{len(expected_contains)} expected values in answer",
    }


def run_evaluation(env: str, model: str):
    """Run evaluation on the golden dataset."""
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    from receipt_agent.agents.question_answering import (
        answer_question_sync,
        create_qa_graph,
    )
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_dynamo_client,
        create_embed_fn,
    )

    # Load environment config
    logger.info("Loading %s environment config...", env.upper())
    config = load_env(env=env)
    secrets = load_secrets(env=env)

    # Merge secrets into config
    for key, value in secrets.items():
        normalized_key = (
            key.replace("portfolio:", "").lower().replace("-", "_")
        )
        config[normalized_key] = value

    # Set API keys
    if not os.environ.get("OPENROUTER_API_KEY"):
        openrouter_key = config.get("openrouter_api_key")
        if openrouter_key:
            os.environ["OPENROUTER_API_KEY"] = openrouter_key

    if not os.environ.get("RECEIPT_AGENT_OPENAI_API_KEY"):
        openai_key = config.get("openai_api_key")
        if openai_key:
            os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key

    # Set LangSmith
    langchain_key = config.get("langchain_api_key")
    if langchain_key:
        os.environ["LANGCHAIN_API_KEY"] = langchain_key
        os.environ["LANGSMITH_API_KEY"] = langchain_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "qa-agent-evaluation"

    # Set model
    os.environ["OPENROUTER_MODEL"] = model
    os.environ["RECEIPT_AGENT_OPENROUTER_MODEL"] = model
    logger.info("Using model: %s", model)

    # Create clients
    dynamo_client = create_dynamo_client(
        table_name=config["dynamodb_table_name"]
    )

    chroma_cloud_api_key = config.get("chroma_cloud_api_key")
    chroma_cloud_enabled = (
        config.get("chroma_cloud_enabled", "false").lower() == "true"
    )

    if chroma_cloud_enabled and chroma_cloud_api_key:
        chroma_client = ChromaClient(
            cloud_api_key=chroma_cloud_api_key,
            cloud_tenant=config.get("chroma_cloud_tenant"),
            cloud_database=config.get("chroma_cloud_database"),
            mode="read",
        )
    else:
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = config.get(
            "chroma_lines_directory", "/tmp/chroma_lines"
        )
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = config.get(
            "chroma_words_directory", "/tmp/chroma_words"
        )
        chroma_client = create_chroma_client(mode="read")

    embed_fn = create_embed_fn()

    # Create graph (5-node ReAct RAG: plan -> agent <-> tools -> shape -> synthesize)
    graph, state_holder = create_qa_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    def target_function(inputs: dict) -> dict:
        """The function being evaluated."""
        question = inputs["question"]
        result = answer_question_sync(
            graph,
            state_holder,
            question,
        )
        return result

    # Run evaluation
    client = Client()

    results = evaluate(
        target_function,
        data="qa-agent-golden",
        evaluators=[
            groundedness_evaluator,
            retrieval_relevance_evaluator,
            tool_choice_evaluator,
            loop_detection_evaluator,
            answer_correctness_evaluator,
        ],
        experiment_prefix=f"qa-eval-{model.replace('/', '-')}",
        metadata={"model": model, "env": env},
    )

    logger.info("Evaluation complete. View results in LangSmith.")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate QA agent with LangSmith"
    )
    parser.add_argument(
        "--env", type=str, choices=["dev", "prod"], help="Environment"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="x-ai/grok-4.1-fast",
        help="Model to evaluate",
    )
    parser.add_argument(
        "--create-dataset", action="store_true", help="Create golden dataset"
    )
    parser.add_argument(
        "--evaluate", action="store_true", help="Run evaluation"
    )

    args = parser.parse_args()

    if args.create_dataset:
        create_dataset()

    if args.evaluate:
        if not args.env:
            parser.error("--env is required for evaluation")
        run_evaluation(args.env, args.model)

    if not args.create_dataset and not args.evaluate:
        parser.print_help()


if __name__ == "__main__":
    main()
