#!/usr/bin/env python3
"""
Build QA RAG Golden Dataset from test results.

This script converts QA agent test results into a LangSmith dataset
for systematic evaluation. Supports:
- Auto-annotation: Use agent answers as baseline ground truth
- Interactive annotation: Human review and correction
- Merge mode: Add to existing dataset

Usage:
    # Auto-annotation from JSON results
    python scripts/build_qa_golden_dataset.py \\
        --input /tmp/qa_results.json \\
        --auto \\
        --dataset qa-rag-golden

    # Interactive annotation
    python scripts/build_qa_golden_dataset.py \\
        --input /tmp/qa_results.json \\
        --interactive \\
        --dataset qa-rag-golden

    # Output to file instead of uploading
    python scripts/build_qa_golden_dataset.py \\
        --input /tmp/qa_results.json \\
        --auto \\
        --output /tmp/golden_dataset.json

Example input format (from test_qa_marquee_questions.py):
    [
        {
            "question": "How much did I spend on coffee?",
            "question_type": "specific_item",
            "answer": "You spent $15.99 on coffee",
            "total_amount": 15.99,
            "receipt_count": 2,
            "evidence": [...]
        },
        ...
    ]
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_agent.agents.question_answering.dataset_schema import (
    QARAGDatasetExample,
    QARAGDatasetInput,
    QARAGDatasetReference,
    ReceiptIdentifier,
    convert_to_langsmith_dataset,
)
from receipt_agent.agents.question_answering.tracing import (
    log_qa_example_to_dataset,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ==============================================================================
# Question Type Inference
# ==============================================================================


def infer_question_type(question: str) -> str:
    """Infer question type from question text.

    Args:
        question: The question text

    Returns:
        Question type classification
    """
    q_lower = question.lower()

    # Check patterns
    if any(word in q_lower for word in ["how much", "total", "spent", "paid"]):
        if "compare" in q_lower or "vs" in q_lower or "more" in q_lower:
            return "comparison"
        return "aggregation" if "all" in q_lower else "specific_item"

    if any(word in q_lower for word in ["show", "list", "all", "every"]):
        return "list_query"

    if any(word in q_lower for word in ["when", "date", "week", "month", "year"]):
        return "time_based"

    if any(word in q_lower for word in ["which", "what store", "merchant", "where"]):
        return "metadata_query"

    if "compare" in q_lower or "vs" in q_lower or "difference" in q_lower:
        return "comparison"

    return "specific_item"


def infer_difficulty(question: str, receipt_count: int) -> str:
    """Infer question difficulty.

    Args:
        question: The question text
        receipt_count: Number of receipts in answer

    Returns:
        Difficulty level (easy, medium, hard)
    """
    q_lower = question.lower()

    # Hard: comparisons, complex aggregations, time-based
    if any(word in q_lower for word in ["compare", "vs", "difference", "most", "least"]):
        return "hard"

    if receipt_count > 5:
        return "hard"

    # Easy: single item lookups
    if receipt_count <= 1:
        return "easy"

    return "medium"


def infer_tags(question: str, result: dict) -> list[str]:
    """Infer tags from question and result.

    Args:
        question: The question text
        result: Agent result dict

    Returns:
        List of tags
    """
    tags = []
    q_lower = question.lower()

    if result.get("total_amount") is not None:
        tags.append("amount")

    if result.get("receipt_count", 0) > 1:
        tags.append("multi_receipt")

    if "tax" in q_lower:
        tags.append("tax")

    if any(word in q_lower for word in ["coffee", "milk", "food", "grocery"]):
        tags.append("food")

    if "merchant" in q_lower or "store" in q_lower:
        tags.append("merchant")

    return tags


# ==============================================================================
# Dataset Building
# ==============================================================================


def load_results(input_path: str) -> list[dict]:
    """Load test results from JSON file.

    Args:
        input_path: Path to JSON file

    Returns:
        List of result dicts
    """
    with open(input_path) as f:
        data = json.load(f)

    # Handle both array and object formats
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "results" in data:
        return data["results"]
    else:
        raise ValueError(f"Unexpected format in {input_path}")


def create_example_from_result(
    result: dict,
    mode: str = "auto",
) -> Optional[QARAGDatasetExample]:
    """Create dataset example from test result.

    Args:
        result: Test result dict
        mode: "auto" or "interactive"

    Returns:
        QARAGDatasetExample or None if skipped
    """
    question = result.get("question", "")
    if not question:
        logger.warning("Skipping result without question")
        return None

    # Get or infer question type
    question_type = result.get("question_type") or infer_question_type(question)

    # Extract evidence to get relevant receipts
    relevant_ids = []
    for e in result.get("evidence", []):
        if e.get("image_id") and e.get("receipt_id") is not None:
            rid = ReceiptIdentifier(
                image_id=e["image_id"],
                receipt_id=e["receipt_id"],
            )
            # Deduplicate
            if rid not in relevant_ids:
                relevant_ids.append(rid)

    # Infer metadata
    receipt_count = result.get("receipt_count", len(relevant_ids))
    difficulty = infer_difficulty(question, receipt_count)
    tags = infer_tags(question, result)

    # Build example
    example = QARAGDatasetExample(
        example_id=str(uuid.uuid4())[:8],
        inputs=QARAGDatasetInput(
            question=question,
            question_type=question_type,  # type: ignore
        ),
        reference_outputs=QARAGDatasetReference(
            expected_answer=result.get("answer"),
            expected_amount=result.get("total_amount"),
            expected_receipt_count=receipt_count,
            relevant_receipt_ids=relevant_ids,
            expected_tools=result.get("tools_used", []),
            difficulty=difficulty,  # type: ignore
            tags=tags,
        ),
        source="auto" if mode == "auto" else "manual",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    return example


def interactive_review(example: QARAGDatasetExample) -> Optional[QARAGDatasetExample]:
    """Interactive review and editing of an example.

    Args:
        example: Example to review

    Returns:
        Edited example or None if skipped
    """
    print("\n" + "=" * 60)
    print(f"Question: {example.inputs.question}")
    print(f"Type: {example.inputs.question_type}")
    print(f"Answer: {example.reference_outputs.expected_answer}")
    print(f"Amount: {example.reference_outputs.expected_amount}")
    print(f"Receipts: {example.reference_outputs.expected_receipt_count}")
    print(f"Difficulty: {example.reference_outputs.difficulty}")
    print("=" * 60)

    response = input("\n[A]ccept, [E]dit, [S]kip? ").strip().lower()

    if response == "s":
        return None
    elif response == "a":
        return example
    elif response == "e":
        # Edit fields
        new_amount = input(
            f"Expected amount [{example.reference_outputs.expected_amount}]: "
        ).strip()
        if new_amount:
            try:
                example.reference_outputs.expected_amount = float(new_amount)
            except ValueError:
                pass

        new_count = input(
            f"Expected receipt count [{example.reference_outputs.expected_receipt_count}]: "
        ).strip()
        if new_count:
            try:
                example.reference_outputs.expected_receipt_count = int(new_count)
            except ValueError:
                pass

        new_difficulty = input(
            f"Difficulty [easy/medium/hard] [{example.reference_outputs.difficulty}]: "
        ).strip()
        if new_difficulty in ["easy", "medium", "hard"]:
            example.reference_outputs.difficulty = new_difficulty  # type: ignore

        notes = input("Notes (optional): ").strip()
        if notes:
            example.reference_outputs.notes = notes

        example.source = "manual"
        return example

    return example


# ==============================================================================
# LangSmith Upload
# ==============================================================================


def upload_to_langsmith(
    examples: list[QARAGDatasetExample],
    dataset_name: str,
) -> bool:
    """Upload examples to LangSmith dataset.

    Args:
        examples: List of examples to upload
        dataset_name: LangSmith dataset name

    Returns:
        True if successful
    """
    try:
        from langsmith import Client

        api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get(
            "LANGSMITH_API_KEY"
        )
        if not api_key:
            logger.error("No LangSmith API key found")
            return False

        client = Client(api_key=api_key)

        # Get or create dataset
        try:
            dataset = client.read_dataset(dataset_name=dataset_name)
            logger.info("Found existing dataset: %s", dataset_name)
        except Exception:
            dataset = client.create_dataset(
                dataset_name=dataset_name,
                description="QA RAG golden dataset for receipt questions",
            )
            logger.info("Created new dataset: %s", dataset_name)

        # Upload examples
        uploaded = 0
        for example in examples:
            try:
                data = example.to_langsmith_format()
                client.create_example(
                    dataset_id=dataset.id,
                    inputs=data["inputs"],
                    outputs=data["outputs"],
                    metadata=data["metadata"],
                )
                uploaded += 1
            except Exception as e:
                logger.error("Failed to upload example: %s", e)

        logger.info("Uploaded %d/%d examples to %s", uploaded, len(examples), dataset_name)
        return uploaded > 0

    except ImportError:
        logger.error("langsmith package not installed")
        return False
    except Exception as e:
        logger.error("Upload failed: %s", e)
        return False


def save_to_file(
    examples: list[QARAGDatasetExample],
    output_path: str,
) -> None:
    """Save examples to JSON file.

    Args:
        examples: List of examples
        output_path: Output file path
    """
    data = convert_to_langsmith_dataset(examples)

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info("Saved %d examples to %s", len(examples), output_path)


# ==============================================================================
# Main
# ==============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Build QA RAG golden dataset from test results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Input JSON file with test results",
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--auto",
        action="store_true",
        help="Auto-annotation mode (use agent answers as baseline)",
    )
    mode_group.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive annotation mode (human review)",
    )

    parser.add_argument(
        "--dataset",
        "-d",
        default="qa-rag-golden",
        help="LangSmith dataset name (default: qa-rag-golden)",
    )

    parser.add_argument(
        "--output",
        "-o",
        help="Output JSON file (if not uploading to LangSmith)",
    )

    parser.add_argument(
        "--filter-type",
        choices=[
            "specific_item",
            "aggregation",
            "time_based",
            "comparison",
            "list_query",
            "metadata_query",
        ],
        help="Filter to only include questions of this type",
    )

    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="Minimum confidence threshold for auto-annotation",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load results
    logger.info("Loading results from %s", args.input)
    results = load_results(args.input)
    logger.info("Loaded %d results", len(results))

    # Filter if requested
    if args.filter_type:
        results = [
            r for r in results
            if (r.get("question_type") or infer_question_type(r.get("question", "")))
            == args.filter_type
        ]
        logger.info("Filtered to %d results of type %s", len(results), args.filter_type)

    # Build examples
    mode = "auto" if args.auto else "interactive"
    examples = []

    for result in results:
        example = create_example_from_result(result, mode)
        if example is None:
            continue

        if args.interactive:
            example = interactive_review(example)

        if example:
            examples.append(example)

    logger.info("Created %d examples", len(examples))

    if not examples:
        logger.warning("No examples to save")
        return

    # Output
    if args.output:
        save_to_file(examples, args.output)
    else:
        success = upload_to_langsmith(examples, args.dataset)
        if not success:
            # Fall back to local file
            fallback_path = f"/tmp/{args.dataset}.json"
            save_to_file(examples, fallback_path)
            logger.info("Saved to fallback location: %s", fallback_path)


if __name__ == "__main__":
    main()
