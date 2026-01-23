"""
LangSmith Custom Evaluators for QA RAG Agent.

Provides evaluator functions for scoring QA agent performance including:
- Retrieval quality (F1)
- Answer groundedness (0-1)
- Amount accuracy (0-1)
- Answer completeness (0-1)
- Agent trajectory efficiency (0-1)
- Tool choice accuracy (F1)
- Error recovery (0/1)
- Combined weighted score

Usage:
    from langsmith import evaluate
    from receipt_agent.agents.question_answering.langsmith_evaluator import (
        create_qa_evaluator,
        retrieval_evaluator,
    )

    # Simple evaluation
    results = evaluate(
        qa_agent_fn,
        data="qa-rag-golden",
        evaluators=[retrieval_evaluator],
    )

    # Full evaluation suite
    evaluators = create_qa_evaluator()
    results = evaluate(qa_agent_fn, data="qa-rag-golden", evaluators=evaluators)
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ==============================================================================
# Metrics Data Classes
# ==============================================================================


@dataclass
class RetrievalMetrics:
    """Metrics for evaluating retrieval quality."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    total_retrieved: int = 0
    total_relevant: int = 0

    @property
    def precision(self) -> float:
        """Precision = TP / (TP + FP)."""
        if self.true_positives + self.false_positives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Recall = TP / (TP + FN)."""
        if self.true_positives + self.false_negatives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_negatives)

    @property
    def f1_score(self) -> float:
        """F1 = 2 * (precision * recall) / (precision + recall)."""
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * (p * r) / (p + r)


@dataclass
class TrajectoryMetrics:
    """Metrics for evaluating agent trajectory efficiency."""

    total_steps: int = 0
    tool_calls: int = 0
    redundant_calls: int = 0
    failed_calls: int = 0
    expected_steps: int = 0

    @property
    def efficiency(self) -> float:
        """Efficiency score (lower steps is better, capped at 1.0)."""
        if self.expected_steps == 0:
            return 1.0 if self.total_steps == 0 else 0.5
        # Ratio of expected to actual (> 1 if faster than expected)
        ratio = self.expected_steps / max(1, self.total_steps)
        # Cap at 1.0, but penalize being slower
        return min(1.0, ratio)

    @property
    def redundancy_rate(self) -> float:
        """Rate of redundant tool calls."""
        if self.tool_calls == 0:
            return 0.0
        return self.redundant_calls / self.tool_calls


@dataclass
class ToolChoiceMetrics:
    """Metrics for evaluating tool selection."""

    correct_tools: int = 0
    incorrect_tools: int = 0
    missed_tools: int = 0
    total_expected: int = 0

    @property
    def precision(self) -> float:
        """Precision of tool choices."""
        total = self.correct_tools + self.incorrect_tools
        if total == 0:
            return 0.0
        return self.correct_tools / total

    @property
    def recall(self) -> float:
        """Recall of expected tool choices."""
        if self.total_expected == 0:
            return 1.0  # No expected tools, so all were used
        return self.correct_tools / self.total_expected

    @property
    def f1_score(self) -> float:
        """F1 score for tool choices."""
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * (p * r) / (p + r)


# ==============================================================================
# Helper Functions
# ==============================================================================


def _extract_receipt_ids(evidence: list[dict]) -> set[tuple]:
    """Extract unique (image_id, receipt_id) tuples from evidence."""
    return {
        (e.get("image_id"), e.get("receipt_id"))
        for e in evidence
        if e.get("image_id") and e.get("receipt_id") is not None
    }


def _extract_amount(text: str) -> Optional[float]:
    """Extract dollar amount from text."""
    # Match patterns like $12.34, 12.34, $1,234.56
    patterns = [
        r"\$?([\d,]+\.?\d*)",  # Basic number
        r"total.*?(\d+\.?\d*)",  # "total: X"
        r"(\d+\.?\d*)\s*dollars?",  # "X dollars"
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            try:
                value = match.group(1).replace(",", "")
                return float(value)
            except (ValueError, IndexError):
                continue
    return None


def _count_tool_calls(messages: list) -> dict[str, int]:
    """Count tool calls by name from message history."""
    counts: dict[str, int] = {}
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "unknown")
                counts[name] = counts.get(name, 0) + 1
    return counts


def _check_groundedness(answer: str, contexts: list[dict]) -> float:
    """Check if answer is grounded in retrieved contexts."""
    if not answer or not contexts:
        return 0.0

    answer_lower = answer.lower()

    # Extract key facts from contexts
    context_facts = set()
    for ctx in contexts:
        text = ctx.get("text", "") or ctx.get("formatted_receipt", "")
        # Extract amounts
        for match in re.findall(r"\d+\.\d{2}", text):
            context_facts.add(match)
        # Extract merchant names (words with MERCHANT_NAME label)
        for match in re.findall(r"(\w+)\[MERCHANT_NAME\]", text):
            context_facts.add(match.lower())

    if not context_facts:
        return 0.5  # Can't verify, give neutral score

    # Check how many facts appear in answer
    found = sum(1 for fact in context_facts if fact in answer_lower)
    return min(1.0, found / len(context_facts)) if context_facts else 0.5


# ==============================================================================
# Evaluator Functions
# ==============================================================================


def retrieval_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate retrieval quality using F1 score.

    Compares retrieved receipts against expected relevant receipts.

    Args:
        inputs: Dataset inputs (question, question_type)
        outputs: Model outputs with 'evidence' key
        reference_outputs: Ground truth with 'relevant_receipt_ids' key

    Returns:
        Dict with 'key', 'score', 'comment', and 'metadata'
    """
    # Get retrieved receipts
    evidence = outputs.get("evidence", [])
    retrieved_ids = _extract_receipt_ids(evidence)

    # Get expected relevant receipts
    relevant_ids = set()
    for rid in reference_outputs.get("relevant_receipt_ids", []):
        if isinstance(rid, dict):
            relevant_ids.add((rid.get("image_id"), rid.get("receipt_id")))
        elif isinstance(rid, tuple):
            relevant_ids.add(rid)

    # Calculate metrics
    true_positives = len(retrieved_ids & relevant_ids)
    false_positives = len(retrieved_ids - relevant_ids)
    false_negatives = len(relevant_ids - retrieved_ids)

    metrics = RetrievalMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        total_retrieved=len(retrieved_ids),
        total_relevant=len(relevant_ids),
    )

    return {
        "key": "retrieval_f1",
        "score": metrics.f1_score,
        "comment": (
            f"F1: {metrics.f1_score:.2%}, "
            f"Precision: {metrics.precision:.2%}, "
            f"Recall: {metrics.recall:.2%}"
        ),
        "metadata": {
            "precision": metrics.precision,
            "recall": metrics.recall,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "retrieved_count": len(retrieved_ids),
            "relevant_count": len(relevant_ids),
        },
    }


def answer_groundedness_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """
    Evaluate if the answer is grounded in retrieved context.

    Checks that claims in the answer can be traced to retrieved receipts.

    Args:
        inputs: Dataset inputs (unused)
        outputs: Model outputs with 'answer' and 'evidence' keys
        reference_outputs: Ground truth (unused for groundedness)

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    answer = outputs.get("answer", "")
    evidence = outputs.get("evidence", [])

    # Convert evidence to context format
    contexts = []
    for e in evidence:
        contexts.append({
            "text": f"{e.get('item', '')} {e.get('amount', '')}",
        })

    score = _check_groundedness(answer, contexts)

    return {
        "key": "groundedness",
        "score": score,
        "comment": (
            f"Groundedness: {score:.2%} "
            f"({len(evidence)} evidence items)"
        ),
    }


def answer_amount_accuracy_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate accuracy of numeric amounts in the answer.

    Compares total_amount against expected_amount with tolerance.

    Args:
        inputs: Dataset inputs (unused)
        outputs: Model outputs with 'total_amount' key
        reference_outputs: Ground truth with 'expected_amount' key

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    output_amount = outputs.get("total_amount")
    expected_amount = reference_outputs.get("expected_amount")

    # Handle missing amounts
    if expected_amount is None:
        # No expected amount - if output has none, that's correct
        if output_amount is None:
            return {
                "key": "amount_accuracy",
                "score": 1.0,
                "comment": "No amount expected, none provided",
            }
        else:
            return {
                "key": "amount_accuracy",
                "score": 0.5,
                "comment": f"No amount expected, but got ${output_amount:.2f}",
            }

    if output_amount is None:
        # Expected but not provided - try extracting from answer text
        answer = outputs.get("answer", "")
        output_amount = _extract_amount(answer)

        if output_amount is None:
            return {
                "key": "amount_accuracy",
                "score": 0.0,
                "comment": f"Expected ${expected_amount:.2f}, but no amount found",
            }

    # Calculate accuracy with tolerance
    if expected_amount == 0:
        if output_amount == 0:
            return {"key": "amount_accuracy", "score": 1.0, "comment": "Both zero"}
        else:
            return {
                "key": "amount_accuracy",
                "score": 0.0,
                "comment": f"Expected $0, got ${output_amount:.2f}",
            }

    # Relative error
    error = abs(output_amount - expected_amount) / expected_amount
    # Convert to score (0% error = 1.0, 100% error = 0.0)
    score = max(0.0, 1.0 - error)

    return {
        "key": "amount_accuracy",
        "score": score,
        "comment": (
            f"Expected ${expected_amount:.2f}, got ${output_amount:.2f} "
            f"(error: {error:.1%})"
        ),
        "metadata": {
            "expected_amount": expected_amount,
            "output_amount": output_amount,
            "absolute_error": abs(output_amount - expected_amount),
            "relative_error": error,
        },
    }


def answer_completeness_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate if the answer fully addresses the question.

    Checks receipt count and presence of expected elements.

    Args:
        inputs: Dataset inputs with 'question_type'
        outputs: Model outputs with 'answer', 'receipt_count', 'evidence'
        reference_outputs: Ground truth with 'expected_receipt_count'

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    answer = outputs.get("answer", "")
    output_count = outputs.get("receipt_count", 0)
    expected_count = reference_outputs.get("expected_receipt_count")
    question_type = inputs.get("question_type", "specific_item")

    scores = []
    comments = []

    # Check receipt count if expected
    if expected_count is not None:
        if expected_count == 0:
            count_score = 1.0 if output_count == 0 else 0.5
        else:
            count_score = min(1.0, output_count / expected_count)
        scores.append(count_score)
        comments.append(f"Count: {output_count}/{expected_count}")

    # Check for common completeness indicators
    if question_type == "aggregation":
        # Should have a total
        has_total = bool(
            outputs.get("total_amount")
            or re.search(r"total|spent|paid", answer.lower())
        )
        scores.append(1.0 if has_total else 0.0)
        comments.append("Has total" if has_total else "Missing total")

    elif question_type == "list_query":
        # Should have multiple items listed
        evidence = outputs.get("evidence", [])
        has_list = len(evidence) > 1 or answer.count("\n") > 1
        scores.append(1.0 if has_list else 0.5)
        comments.append("Has list" if has_list else "Single item")

    # Check answer is not empty or error
    is_valid = len(answer) > 20 and "error" not in answer.lower()
    scores.append(1.0 if is_valid else 0.0)
    comments.append("Valid answer" if is_valid else "Invalid/empty")

    final_score = sum(scores) / len(scores) if scores else 0.0

    return {
        "key": "completeness",
        "score": final_score,
        "comment": "; ".join(comments),
    }


def agent_trajectory_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate agent trajectory efficiency.

    Checks if the agent took a reasonable number of steps.

    Args:
        inputs: Dataset inputs (unused)
        outputs: Model outputs with 'messages' or 'iteration_count'
        reference_outputs: Ground truth with 'expected_steps' (optional)

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    messages = outputs.get("messages", [])
    iteration_count = outputs.get("iteration_count", len(messages) // 2)
    expected_steps = reference_outputs.get("expected_steps", 5)

    # Count tool calls
    tool_counts = _count_tool_calls(messages)
    total_tool_calls = sum(tool_counts.values())

    # Check for redundant calls (same tool called > 3 times)
    redundant = sum(max(0, count - 3) for count in tool_counts.values())

    metrics = TrajectoryMetrics(
        total_steps=iteration_count,
        tool_calls=total_tool_calls,
        redundant_calls=redundant,
        expected_steps=expected_steps,
    )

    return {
        "key": "trajectory_efficiency",
        "score": metrics.efficiency,
        "comment": (
            f"Steps: {iteration_count} (expected {expected_steps}), "
            f"Tools: {total_tool_calls}, Redundant: {redundant}"
        ),
        "metadata": {
            "total_steps": iteration_count,
            "tool_calls": total_tool_calls,
            "redundant_calls": redundant,
            "tool_counts": tool_counts,
            "efficiency": metrics.efficiency,
        },
    }


def tool_choice_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate tool selection accuracy.

    Compares tools used against expected tools.

    Args:
        inputs: Dataset inputs (unused)
        outputs: Model outputs with 'messages'
        reference_outputs: Ground truth with 'expected_tools'

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    messages = outputs.get("messages", [])
    tool_counts = _count_tool_calls(messages)
    used_tools = set(tool_counts.keys())

    expected_tools = set(reference_outputs.get("expected_tools", []))

    # If no expected tools specified, just check reasonable usage
    if not expected_tools:
        # Minimum: should use search and get_receipt
        basic_tools = {"search_receipts", "get_receipt"}
        has_basics = bool(used_tools & basic_tools)
        return {
            "key": "tool_choice_f1",
            "score": 1.0 if has_basics else 0.5,
            "comment": f"Used tools: {', '.join(used_tools) or 'none'}",
        }

    # Calculate metrics
    correct = len(used_tools & expected_tools)
    incorrect = len(used_tools - expected_tools)
    missed = len(expected_tools - used_tools)

    metrics = ToolChoiceMetrics(
        correct_tools=correct,
        incorrect_tools=incorrect,
        missed_tools=missed,
        total_expected=len(expected_tools),
    )

    return {
        "key": "tool_choice_f1",
        "score": metrics.f1_score,
        "comment": (
            f"Correct: {correct}, Incorrect: {incorrect}, Missed: {missed}"
        ),
        "metadata": {
            "used_tools": list(used_tools),
            "expected_tools": list(expected_tools),
            "correct_tools": correct,
            "incorrect_tools": incorrect,
            "missed_tools": missed,
            "precision": metrics.precision,
            "recall": metrics.recall,
        },
    }


def error_recovery_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """
    Evaluate if agent recovered from errors gracefully.

    Checks for error messages and whether agent still produced output.

    Args:
        inputs: Dataset inputs (unused)
        outputs: Model outputs with 'answer' and 'messages'
        reference_outputs: Ground truth (unused)

    Returns:
        Dict with 'key', 'score', and 'comment'
    """
    answer = outputs.get("answer", "")
    messages = outputs.get("messages", [])

    # Check for error indicators
    has_error_in_answer = "error" in answer.lower()
    tool_errors = 0

    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if isinstance(content, str) and "error" in content.lower():
            tool_errors += 1

    # Scoring
    if tool_errors == 0:
        # No errors encountered
        score = 1.0
        comment = "No errors encountered"
    elif has_error_in_answer:
        # Had errors and couldn't recover
        score = 0.0
        comment = f"Failed to recover from {tool_errors} error(s)"
    else:
        # Had errors but produced valid answer
        score = 1.0
        comment = f"Recovered from {tool_errors} error(s)"

    return {
        "key": "error_recovery",
        "score": score,
        "comment": comment,
        "metadata": {
            "tool_errors": tool_errors,
            "has_error_in_answer": has_error_in_answer,
        },
    }


def qa_combined_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
    *,
    weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """
    Combined weighted evaluator for overall QA quality.

    Default weights:
    - Retrieval: 25%
    - Groundedness: 20%
    - Amount accuracy: 20%
    - Completeness: 15%
    - Trajectory: 10%
    - Tool choice: 10%

    Args:
        inputs: Dataset inputs
        outputs: Model outputs
        reference_outputs: Ground truth
        weights: Optional custom weights

    Returns:
        Dict with 'key', 'score', 'comment', and 'metadata'
    """
    default_weights = {
        "retrieval_f1": 0.25,
        "groundedness": 0.20,
        "amount_accuracy": 0.20,
        "completeness": 0.15,
        "trajectory_efficiency": 0.10,
        "tool_choice_f1": 0.10,
    }
    weights = weights or default_weights

    # Run all evaluators
    evaluators = {
        "retrieval_f1": retrieval_evaluator,
        "groundedness": answer_groundedness_evaluator,
        "amount_accuracy": answer_amount_accuracy_evaluator,
        "completeness": answer_completeness_evaluator,
        "trajectory_efficiency": agent_trajectory_evaluator,
        "tool_choice_f1": tool_choice_evaluator,
    }

    results = {}
    for name, evaluator in evaluators.items():
        result = evaluator(inputs, outputs, reference_outputs)
        results[name] = result

    # Calculate weighted score
    weighted_sum = 0.0
    total_weight = 0.0
    comments = []

    for name, weight in weights.items():
        if name in results:
            score = results[name]["score"]
            weighted_sum += score * weight
            total_weight += weight
            comments.append(f"{name}: {score:.2f}")

    final_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    return {
        "key": "qa_combined",
        "score": final_score,
        "comment": "; ".join(comments),
        "metadata": {
            "individual_scores": {
                name: result["score"] for name, result in results.items()
            },
            "weights": weights,
            "individual_results": results,
        },
    }


# ==============================================================================
# Factory Function
# ==============================================================================


def create_qa_evaluator(
    evaluators: Optional[list[str]] = None,
    combined_weights: Optional[dict[str, float]] = None,
) -> list[Callable]:
    """
    Factory to create a list of QA evaluators.

    Args:
        evaluators: List of evaluator names to include. If None, includes all.
            Options: "retrieval", "groundedness", "amount", "completeness",
                    "trajectory", "tool_choice", "error_recovery", "combined"
        combined_weights: Custom weights for combined evaluator

    Returns:
        List of evaluator functions for use with LangSmith evaluate()

    Usage:
        from langsmith import evaluate
        from receipt_agent.agents.question_answering.langsmith_evaluator import (
            create_qa_evaluator,
        )

        evaluators = create_qa_evaluator()
        results = evaluate(qa_agent_fn, data="qa-rag-golden", evaluators=evaluators)

        # Custom selection
        evaluators = create_qa_evaluator(["retrieval", "amount", "combined"])
    """
    all_evaluators = {
        "retrieval": retrieval_evaluator,
        "groundedness": answer_groundedness_evaluator,
        "amount": answer_amount_accuracy_evaluator,
        "completeness": answer_completeness_evaluator,
        "trajectory": agent_trajectory_evaluator,
        "tool_choice": tool_choice_evaluator,
        "error_recovery": error_recovery_evaluator,
        "combined": partial(qa_combined_evaluator, weights=combined_weights),
    }

    if evaluators is None:
        # Return all evaluators
        return list(all_evaluators.values())

    return [
        all_evaluators[name]
        for name in evaluators
        if name in all_evaluators
    ]
