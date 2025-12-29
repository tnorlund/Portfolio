"""
Label Evaluator Agent

A two-phase agent that validates receipt word labels:
1. Deterministic evaluation: Analyzes spatial patterns to flag potential issues
2. LLM review: Uses a cheap LLM (Haiku) to make semantic decisions on flagged
   issues

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
    result = await run_label_evaluator(
        graph,
        image_id,
        receipt_id,
        skip_llm_review=True,
    )

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
- Position anomalies: Labels appearing in unexpected positions based on
  merchant patterns
- Geometric anomalies: Label types with unusual angle/distance relationships
  compared to learned merchant patterns
- Constellation anomalies: Unusual n-tuple spatial patterns beyond pairwise
  label relationships
- Missing labels in clusters: Unlabeled words surrounded by consistently
  labeled words
- Missing constellation members: Unlabeled words at expected positions for
  missing labels in partial constellations
- Text-label conflicts: Same word text with different labels at different
  positions

Results are written as new ReceiptWordLabel entries with validation_status and
reasoning, creating an audit trail of label changes.

For LangSmith evaluations:
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

from receipt_agent.agents.label_evaluator.currency_subagent import (
    CurrencyWord,
    LineItemRow,
    build_currency_evaluation_prompt,
    collect_currency_words,
    convert_to_evaluation_issues,
    evaluate_currency_labels,
    evaluate_currency_labels_sync,
    identify_line_item_rows,
    parse_currency_evaluation_response,
)
from receipt_agent.agents.label_evaluator.financial_subagent import (
    FinancialValue,
    MathIssue,
    build_financial_validation_prompt,
    check_grand_total_math,
    check_line_item_math,
    check_subtotal_math,
    detect_math_issues,
    evaluate_financial_math,
    evaluate_financial_math_sync,
    extract_financial_values,
    extract_number,
    parse_financial_evaluation_response,
)
from receipt_agent.agents.label_evaluator.geometry import (
    angle_difference,
    calculate_angle_degrees,
    calculate_distance,
    convert_polar_to_cartesian,
)
from receipt_agent.agents.label_evaluator.graph import (
    create_compute_only_graph,
    create_label_evaluator_graph,
    run_compute_only,
    run_compute_only_sync,
    run_label_evaluator,
    run_label_evaluator_sync,
)
from receipt_agent.agents.label_evaluator.issue_detection import (
    check_constellation_anomaly,
    check_geometric_anomaly,
    check_missing_constellation_member,
    check_missing_label_in_cluster,
    check_position_anomaly,
    check_text_label_conflict,
    check_unexpected_label_pair,
    evaluate_word_contexts,
)
from receipt_agent.agents.label_evaluator.langsmith_evaluator import (
    EvaluationQualityMetrics,
    LabelComparisonMetrics,
    create_label_evaluator,
    label_accuracy_evaluator,
    label_quality_evaluator,
)
from receipt_agent.agents.label_evaluator.llm_review import (
    CURRENCY_LABELS,
    apply_llm_decisions,
    assemble_receipt_text,
    build_review_context,
    extract_receipt_currency_context,
    format_receipt_text,
    gather_evidence_for_issue,
    is_currency_amount,
    parse_currency_value,
    review_all_issues,
    review_issues_batch,
    review_issues_with_receipt_context,
    review_single_issue,
)
from receipt_agent.agents.label_evaluator.metadata_subagent import (
    FORMAT_VALIDATED_LABELS,
    MAX_RECEIPT_LINES_FOR_PROMPT,
    PLACE_VALIDATED_LABELS,
    MetadataWord,
    build_metadata_evaluation_prompt,
    collect_metadata_words,
    evaluate_metadata_labels,
    parse_metadata_evaluation_response,
)
from receipt_agent.agents.label_evaluator.pattern_discovery import (
    PatternDiscoveryConfig,
    build_discovery_prompt,
    build_receipt_structure,
    discover_patterns_with_llm,
    get_default_patterns,
)
from receipt_agent.agents.label_evaluator.patterns import (
    batch_receipts_by_quality,
    classify_conflicts_with_llm,
    compute_merchant_patterns,
    detect_label_conflicts,
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
from receipt_agent.agents.label_evaluator.word_context import (
    assemble_visual_lines,
    build_word_contexts,
    get_same_line_words,
)
from receipt_agent.utils.chroma_helpers import (
    SimilarWordResult,
    build_word_chroma_id,
    format_similar_words_for_prompt,
    query_similar_validated_words,
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
    "get_same_line_words",
    "compute_merchant_patterns",
    "batch_receipts_by_quality",
    "detect_label_conflicts",
    "classify_conflicts_with_llm",
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
    # Geometry helpers
    "angle_difference",
    "calculate_angle_degrees",
    "calculate_distance",
    "convert_polar_to_cartesian",
    # ChromaDB similarity search
    "build_word_chroma_id",
    "query_similar_validated_words",
    "format_similar_words_for_prompt",
    # Receipt text assembly for LLM review
    "CURRENCY_LABELS",
    "assemble_receipt_text",
    "extract_receipt_currency_context",
    "is_currency_amount",
    "parse_currency_value",
    # LLM review functions
    "apply_llm_decisions",
    "gather_evidence_for_issue",
    "review_all_issues",
    "review_issues_batch",
    "review_issues_with_receipt_context",
    "review_single_issue",
    # LangSmith custom evaluators
    "create_label_evaluator",
    "label_accuracy_evaluator",
    "label_quality_evaluator",
    "LabelComparisonMetrics",
    "EvaluationQualityMetrics",
    # Pattern discovery (for line item structure analysis)
    "PatternDiscoveryConfig",
    "build_receipt_structure",
    "build_discovery_prompt",
    "discover_patterns_with_llm",
    "get_default_patterns",
    # Currency subagent (for currency label validation)
    "CurrencyWord",
    "LineItemRow",
    "build_currency_evaluation_prompt",
    "collect_currency_words",
    "convert_to_evaluation_issues",
    "evaluate_currency_labels",
    "evaluate_currency_labels_sync",
    "identify_line_item_rows",
    "parse_currency_evaluation_response",
    # Metadata subagent (for metadata label validation)
    "MetadataWord",
    "PLACE_VALIDATED_LABELS",
    "FORMAT_VALIDATED_LABELS",
    "MAX_RECEIPT_LINES_FOR_PROMPT",
    "build_metadata_evaluation_prompt",
    "collect_metadata_words",
    "evaluate_metadata_labels",
    "parse_metadata_evaluation_response",
    # Financial subagent (for financial math validation)
    "FinancialValue",
    "MathIssue",
    "build_financial_validation_prompt",
    "check_grand_total_math",
    "check_line_item_math",
    "check_subtotal_math",
    "detect_math_issues",
    "evaluate_financial_math",
    "evaluate_financial_math_sync",
    "extract_financial_values",
    "extract_number",
    "parse_financial_evaluation_response",
]
