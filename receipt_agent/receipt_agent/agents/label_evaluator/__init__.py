"""
Label Evaluator Agent

A two-phase agent that validates receipt word labels:
1. Deterministic evaluation: Analyzes spatial patterns to flag potential issues
2. LLM review: Uses a cheap LLM (Haiku) to make semantic decisions on flagged issues

Usage:
    from receipt_agent.agents.label_evaluator import (
        create_label_evaluator_graph,
        run_label_evaluator,
        run_label_evaluator_sync,
    )

    # Create the graph with a DynamoDB client
    graph = create_label_evaluator_graph(dynamo_client)

    # Run evaluation with LLM review (default)
    result = await run_label_evaluator(graph, image_id, receipt_id)

    # Run without LLM review (faster, cheaper)
    result = await run_label_evaluator(graph, image_id, receipt_id, skip_llm_review=True)

    # Or synchronously
    result = run_label_evaluator_sync(graph, image_id, receipt_id)

The agent detects:
- Position anomalies: Labels appearing in unexpected positions based on merchant patterns
- Same-line conflicts: Incompatible labels on the same visual line (e.g., MERCHANT_NAME + PRODUCT_NAME)
- Missing labels in clusters: Unlabeled words surrounded by consistently labeled words
- Text-label conflicts: Same word text with different labels at different positions

Results are written as new ReceiptWordLabel entries with validation_status and reasoning,
creating an audit trail of label changes.
"""

from receipt_agent.agents.label_evaluator.graph import (
    create_label_evaluator_graph,
    run_label_evaluator,
    run_label_evaluator_sync,
)
from receipt_agent.agents.label_evaluator.helpers import (
    assemble_visual_lines,
    build_review_context,
    build_word_contexts,
    check_missing_label_in_cluster,
    check_position_anomaly,
    check_same_line_conflict,
    check_text_label_conflict,
    compute_merchant_patterns,
    evaluate_word_contexts,
    format_receipt_text,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    EvaluatorState,
    MerchantPatterns,
    OtherReceiptData,
    ReviewContext,
    ReviewResult,
    VisualLine,
    WordContext,
)

__all__ = [
    # Graph and runners
    "create_label_evaluator_graph",
    "run_label_evaluator",
    "run_label_evaluator_sync",
    # State classes
    "EvaluatorState",
    "WordContext",
    "VisualLine",
    "MerchantPatterns",
    "EvaluationIssue",
    "OtherReceiptData",
    "ReviewContext",
    "ReviewResult",
    # Helper functions (for testing and extension)
    "build_word_contexts",
    "assemble_visual_lines",
    "compute_merchant_patterns",
    "evaluate_word_contexts",
    "check_position_anomaly",
    "check_same_line_conflict",
    "check_text_label_conflict",
    "check_missing_label_in_cluster",
    "build_review_context",
    "format_receipt_text",
]
