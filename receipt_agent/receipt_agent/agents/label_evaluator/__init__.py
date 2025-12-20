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

For AWS Lambda / Step Functions (compute-only, no I/O):
    from receipt_agent.agents.label_evaluator import (
        create_compute_only_graph,
        run_compute_only,
        EvaluatorState,
        OtherReceiptData,
    )

    # Create pre-loaded state from S3 data
    state = EvaluatorState(
        image_id=image_id,
        receipt_id=receipt_id,
        words=deserialized_words,
        labels=deserialized_labels,
        other_receipt_data=[OtherReceiptData(...), ...],
    )

    # Run pure computation (no DynamoDB, no LLM)
    graph = create_compute_only_graph()
    result = await run_compute_only(graph, state)

The agent detects:
- Position anomalies: Labels appearing in unexpected positions based on merchant patterns
- Geometric anomalies: Label types with unusual angle/distance relationships compared to learned merchant patterns
- Constellation anomalies: Unusual n-tuple spatial patterns beyond pairwise label relationships
- Missing labels in clusters: Unlabeled words surrounded by consistently labeled words
- Missing constellation members: Unlabeled words at expected positions for missing labels in partial constellations
- Text-label conflicts: Same word text with different labels at different positions

Results are written as new ReceiptWordLabel entries with validation_status and reasoning,
creating an audit trail of label changes.
"""

from receipt_agent.agents.label_evaluator.graph import (
    create_compute_only_graph,
    create_label_evaluator_graph,
    run_compute_only,
    run_compute_only_sync,
    run_label_evaluator,
    run_label_evaluator_sync,
)
from receipt_agent.agents.label_evaluator.helpers import (
    SimilarWordResult,
    assemble_visual_lines,
    build_review_context,
    build_word_chroma_id,
    build_word_contexts,
    check_constellation_anomaly,
    check_geometric_anomaly,
    check_missing_constellation_member,
    check_missing_label_in_cluster,
    check_position_anomaly,
    check_text_label_conflict,
    check_unexpected_label_pair,
    compute_merchant_patterns,
    evaluate_word_contexts,
    format_receipt_text,
    format_similar_words_for_prompt,
    query_similar_validated_words,
)
from receipt_agent.agents.label_evaluator.state import (
    ConstellationGeometry,
    EvaluationIssue,
    EvaluatorState,
    LabelRelativePosition,
    MerchantPatterns,
    OtherReceiptData,
    ReviewContext,
    ReviewResult,
    VisualLine,
    WordContext,
)

__all__ = [
    # Graph and runners (full workflow with I/O)
    "create_label_evaluator_graph",
    "run_label_evaluator",
    "run_label_evaluator_sync",
    # Compute-only graph (for Lambda/Step Functions)
    "create_compute_only_graph",
    "run_compute_only",
    "run_compute_only_sync",
    # State classes
    "EvaluatorState",
    "WordContext",
    "VisualLine",
    "MerchantPatterns",
    "ConstellationGeometry",
    "LabelRelativePosition",
    "EvaluationIssue",
    "OtherReceiptData",
    "ReviewContext",
    "ReviewResult",
    "SimilarWordResult",
    # Helper functions (for testing and extension)
    "build_word_contexts",
    "assemble_visual_lines",
    "compute_merchant_patterns",
    "evaluate_word_contexts",
    "check_position_anomaly",
    "check_geometric_anomaly",
    "check_constellation_anomaly",
    "check_missing_constellation_member",
    "check_text_label_conflict",
    "check_unexpected_label_pair",
    "check_missing_label_in_cluster",
    "build_review_context",
    "format_receipt_text",
    # ChromaDB similarity search
    "build_word_chroma_id",
    "query_similar_validated_words",
    "format_similar_words_for_prompt",
]
