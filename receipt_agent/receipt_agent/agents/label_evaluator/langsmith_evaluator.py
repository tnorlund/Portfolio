"""
LangSmith Custom Evaluator for Label Evaluator.

Provides evaluator functions for scoring label predictions against ground truth
using pattern-based evaluation logic from the label_evaluator agent.

Usage:
    from langsmith import evaluate
    from receipt_agent.agents.label_evaluator import (
        create_label_evaluator,
        label_accuracy_evaluator,
    )

    # Simple accuracy evaluation
    results = evaluate(
        my_label_prediction_model,
        data="receipt-labels-test-dataset",
        evaluators=[label_accuracy_evaluator],
    )

    # With pattern-based quality evaluation
    evaluator = create_label_evaluator(patterns=merchant_patterns)
    results = evaluate(
        my_label_prediction_model,
        data="receipt-labels-test-dataset",
        evaluators=[evaluator],
    )
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Optional

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.word_context import (
    assemble_visual_lines,
    build_word_contexts,
)
from receipt_agent.agents.label_evaluator.issue_detection import (
    evaluate_word_contexts,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    MerchantPatterns,
)


@dataclass
class LabelComparisonMetrics:
    """Metrics from comparing predicted vs ground truth labels."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    total_predictions: int = 0
    total_ground_truth: int = 0

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

    @property
    def accuracy(self) -> float:
        """Accuracy = TP / max(total_predictions, total_ground_truth)."""
        total = max(self.total_predictions, self.total_ground_truth)
        if total == 0:
            return 0.0
        return self.true_positives / total


@dataclass
class EvaluationQualityMetrics:
    """Metrics from pattern-based quality evaluation."""

    total_issues: int = 0
    issues_by_type: dict[str, int] = field(default_factory=dict)
    total_words: int = 0

    @property
    def issue_rate(self) -> float:
        """Rate of words with detected issues."""
        if self.total_words == 0:
            return 0.0
        return self.total_issues / self.total_words

    def get_issue_rate(self, issue_type: str) -> float:
        """Get rate for a specific issue type."""
        if self.total_words == 0:
            return 0.0
        return self.issues_by_type.get(issue_type, 0) / self.total_words


def _compare_labels(
    predicted_labels: list[ReceiptWordLabel],
    ground_truth_labels: list[ReceiptWordLabel],
) -> LabelComparisonMetrics:
    """
    Compare predicted labels against ground truth.

    Args:
        predicted_labels: Labels from the model being evaluated
        ground_truth_labels: Ground truth labels (filters to VALID only)

    Returns:
        LabelComparisonMetrics with precision/recall/F1
    """
    # Build lookup: (line_id, word_id) -> label
    pred_by_word: dict[tuple[int, int], str] = {}
    for label in predicted_labels:
        key = (label.line_id, label.word_id)
        pred_by_word[key] = label.label

    # Filter ground truth to only VALID labels
    truth_by_word: dict[tuple[int, int], str] = {}
    for label in ground_truth_labels:
        if label.validation_status == "VALID":
            key = (label.line_id, label.word_id)
            truth_by_word[key] = label.label

    # Calculate metrics
    metrics = LabelComparisonMetrics(
        total_predictions=len(pred_by_word),
        total_ground_truth=len(truth_by_word),
    )

    # Count true positives (predicted correctly)
    for key, pred_label in pred_by_word.items():
        if key in truth_by_word and truth_by_word[key] == pred_label:
            metrics.true_positives += 1
        else:
            metrics.false_positives += 1

    # Count false negatives (ground truth not predicted)
    for key in truth_by_word:
        if key not in pred_by_word:
            metrics.false_negatives += 1
        elif pred_by_word[key] != truth_by_word[key]:
            # Wrong label counts as both FP (above) and FN
            metrics.false_negatives += 1

    return metrics


def _evaluate_prediction_quality(
    words: list[ReceiptWord],
    predicted_labels: list[ReceiptWordLabel],
    patterns: Optional[MerchantPatterns],
) -> EvaluationQualityMetrics:
    """
    Evaluate prediction quality using pattern-based anomaly detection.

    Uses the same validation rules as the Step Function's evaluate_labels Lambda.

    Args:
        words: All words in the receipt
        predicted_labels: Labels from the model being evaluated
        patterns: Pre-computed patterns from merchant's other receipts

    Returns:
        EvaluationQualityMetrics with issue counts and rates
    """
    if not words:
        return EvaluationQualityMetrics(total_words=0)

    # Build word contexts (same as Step Function)
    word_contexts = build_word_contexts(words, predicted_labels)

    # Group into visual lines
    visual_lines = assemble_visual_lines(word_contexts)

    # Run evaluation (same checks as Step Function)
    issues = evaluate_word_contexts(word_contexts, patterns, visual_lines)

    # Aggregate by type
    issues_by_type: dict[str, int] = defaultdict(int)
    for issue in issues:
        issues_by_type[issue.issue_type] += 1

    return EvaluationQualityMetrics(
        total_issues=len(issues),
        issues_by_type=dict(issues_by_type),
        total_words=len(word_contexts),
    )


def label_accuracy_evaluator(
    inputs: dict[str, Any],  # noqa: ARG001 - Required by LangSmith API
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Simple accuracy evaluator comparing predicted vs ground truth labels.

    This is a LangSmith custom evaluator function.

    Args:
        inputs: Dataset inputs (required by LangSmith API, unused here)
        outputs: Model predictions with 'labels' key
        reference_outputs: Ground truth with 'labels' key

    Returns:
        Dict with 'key', 'score', and 'comment' for LangSmith
    """
    predicted = outputs.get("labels", [])
    ground_truth = reference_outputs.get("labels", [])

    # Handle both native objects and dicts
    if predicted and isinstance(predicted[0], dict):
        predicted = [ReceiptWordLabel(**lbl) for lbl in predicted]
    if ground_truth and isinstance(ground_truth[0], dict):
        ground_truth = [ReceiptWordLabel(**lbl) for lbl in ground_truth]

    metrics = _compare_labels(predicted, ground_truth)

    return {
        "key": "label_accuracy",
        "score": metrics.f1_score,
        "comment": (
            f"F1: {metrics.f1_score:.2%}, "
            f"Precision: {metrics.precision:.2%}, "
            f"Recall: {metrics.recall:.2%}"
        ),
    }


def label_quality_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
    *,
    patterns: Optional[MerchantPatterns] = None,
) -> dict[str, Any]:
    """
    Pattern-based quality evaluator using Step Function logic.

    Combines label accuracy with spatial pattern anomaly detection.

    Args:
        inputs: Dataset inputs with 'words' key
        outputs: Model predictions with 'labels' key
        reference_outputs: Ground truth with 'labels' key
        patterns: Pre-computed patterns from merchant's other receipts

    Returns:
        Dict with 'key', 'score', 'comment', and 'metadata' for LangSmith
    """
    words = inputs.get("words", [])
    predicted = outputs.get("labels", [])
    ground_truth = reference_outputs.get("labels", [])

    # Handle both native objects and dicts
    if words and isinstance(words[0], dict):
        words = [ReceiptWord(**w) for w in words]
    if predicted and isinstance(predicted[0], dict):
        predicted = [ReceiptWordLabel(**lbl) for lbl in predicted]
    if ground_truth and isinstance(ground_truth[0], dict):
        ground_truth = [ReceiptWordLabel(**lbl) for lbl in ground_truth]

    # Compare labels
    comparison = _compare_labels(predicted, ground_truth)

    # Evaluate prediction quality
    quality = _evaluate_prediction_quality(words, predicted, patterns)

    # Combined score: 70% F1, 30% quality (fewer issues = better)
    # These weights prioritize label accuracy over pattern conformity since
    # patterns may not capture all valid label variations.
    quality_score = 1.0 - quality.issue_rate
    combined_score = 0.7 * comparison.f1_score + 0.3 * quality_score

    return {
        "key": "label_quality",
        "score": combined_score,
        "comment": (
            f"F1: {comparison.f1_score:.2%}, "
            f"Issues: {quality.total_issues}/{quality.total_words}"
        ),
        "metadata": {
            "precision": comparison.precision,
            "recall": comparison.recall,
            "f1_score": comparison.f1_score,
            "true_positives": comparison.true_positives,
            "false_positives": comparison.false_positives,
            "false_negatives": comparison.false_negatives,
            "total_issues": quality.total_issues,
            "issues_by_type": quality.issues_by_type,
            "issue_rate": quality.issue_rate,
            "position_anomaly_rate": quality.get_issue_rate("position_anomaly"),
            "geometric_anomaly_rate": quality.get_issue_rate("geometric_anomaly"),
            "constellation_anomaly_rate": quality.get_issue_rate(
                "constellation_anomaly"
            ),
        },
    }


def create_label_evaluator(
    patterns: Optional[MerchantPatterns] = None,
    enable_quality_check: bool = True,
) -> Callable:
    """
    Factory to create a configured label evaluator.

    Args:
        patterns: Pre-computed patterns from merchant's other receipts.
                  If None, quality evaluation runs without pattern checks.
        enable_quality_check: If True, returns label_quality_evaluator.
                              If False, returns label_accuracy_evaluator.

    Returns:
        Evaluator function compatible with LangSmith's evaluate()

    Usage:
        from langsmith import evaluate
        from receipt_agent.agents.label_evaluator import create_label_evaluator

        # With patterns
        evaluator = create_label_evaluator(patterns=merchant_patterns)
        results = evaluate(model_fn, data="dataset", evaluators=[evaluator])

        # Without patterns (accuracy only)
        evaluator = create_label_evaluator(enable_quality_check=False)
        results = evaluate(model_fn, data="dataset", evaluators=[evaluator])
    """
    if not enable_quality_check:
        return label_accuracy_evaluator

    return partial(label_quality_evaluator, patterns=patterns)
