"""
Traced runners for Label Evaluator with rich LangSmith integration.

These runners wrap the compute-only graph with explicit LangSmith tracing,
logging the full state at each step for visibility in the LangSmith dashboard.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# LangSmith tracing
try:
    from langsmith import traceable
    from langsmith.run_trees import RunTree

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    RunTree = None

    def traceable(**_kwargs):
        """No-op fallback when LangSmith is unavailable."""

        def decorator(f):
            return f

        return decorator


def _serialize_issue(issue: Any) -> dict:
    """Serialize an EvaluationIssue to a JSON-safe dict."""
    return {
        "type": issue.issue_type,
        "word_text": issue.word.text,
        "line_id": issue.word.line_id,
        "word_id": issue.word.word_id,
        "current_label": issue.current_label,
        "suggested_status": issue.suggested_status,
        "suggested_label": issue.suggested_label,
        "reasoning": issue.reasoning[:200] if issue.reasoning else None,
    }


def _serialize_patterns(patterns: Any) -> Optional[dict]:
    """Serialize MerchantPatterns to a JSON-safe dict."""
    if not patterns:
        return None
    return {
        "merchant_name": patterns.merchant_name,
        "receipt_count": patterns.receipt_count,
        "label_pair_count": len(patterns.label_pair_geometry)
        if patterns.label_pair_geometry
        else 0,
        "constellation_count": len(patterns.constellation_geometries)
        if patterns.constellation_geometries
        else 0,
    }


def _serialize_state_summary(state: Any) -> dict:
    """Create a summary of state for tracing."""
    return {
        "image_id": state.image_id,
        "receipt_id": state.receipt_id,
        "words_count": len(state.words) if state.words else 0,
        "labels_count": len(state.labels) if state.labels else 0,
        "visual_lines_count": len(state.visual_lines)
        if state.visual_lines
        else 0,
        "word_contexts_count": len(state.word_contexts)
        if state.word_contexts
        else 0,
        "issues_found_count": len(state.issues_found)
        if state.issues_found
        else 0,
        "has_patterns": state.merchant_patterns is not None,
        "patterns": _serialize_patterns(state.merchant_patterns),
        "error": state.error,
    }


@traceable(
    name="label_evaluator",
    run_type="chain",
    tags=["label-evaluator", "receipt"],
)
def run_compute_only_traced(
    graph: Any,
    initial_state: Any,
    execution_id: Optional[str] = None,
) -> dict:
    """
    Run the compute-only graph with rich LangSmith tracing.

    This logs the full state at each step, making it visible in LangSmith:
    - Input state (words, labels, patterns)
    - Each node's output
    - Issues found with full details
    - Final evaluation result

    Args:
        graph: Compiled compute-only workflow graph
        initial_state: Pre-loaded EvaluatorState
        execution_id: Optional execution ID for correlation

    Returns:
        Evaluation result dict with issues found
    """
    import asyncio

    from receipt_agent.agents.label_evaluator.state import EvaluatorState

    # Log initial state
    logger.info(
        "Starting traced evaluation for %s#%s",
        initial_state.image_id,
        initial_state.receipt_id,
    )

    # Run the graph with streaming to capture intermediate states
    config = {"recursion_limit": 10}

    if execution_id:
        config["metadata"] = {"execution_id": execution_id}

    # Run graph and capture all states
    final_state = None
    step_states = []

    try:
        # Stream through the graph to capture each step
        for step_output in graph.stream(
            initial_state,
            config=config,
            stream_mode="updates",
        ):
            step_states.append(step_output)
            # The last state is the final one
            for node_name, node_output in step_output.items():
                logger.info(
                    "Step '%s' completed: %s",
                    node_name,
                    {
                        k: len(v) if isinstance(v, list) else v
                        for k, v in node_output.items()
                        if k != "word_contexts"  # Skip verbose field
                    },
                )

        # Get final state from graph
        # The stream updates the state, so we need to get the final result
        final_state_dict = graph.invoke(initial_state, config=config)

        # Build result
        issues_found = final_state_dict.get("issues_found", [])
        merchant_patterns = final_state_dict.get("merchant_patterns")

        result = {
            "image_id": initial_state.image_id,
            "receipt_id": initial_state.receipt_id,
            "issues_found": len(issues_found),
            "issues": [_serialize_issue(issue) for issue in issues_found],
            "patterns": _serialize_patterns(merchant_patterns),
            "error": final_state_dict.get("error"),
            "steps_completed": len(step_states),
        }

        logger.info(
            "Evaluation complete: %s issues found in %s steps",
            result["issues_found"],
            result["steps_completed"],
        )

        return result

    except Exception as e:
        logger.error("Error in traced evaluation: %s", e, exc_info=True)
        return {
            "image_id": initial_state.image_id,
            "receipt_id": initial_state.receipt_id,
            "issues_found": 0,
            "issues": [],
            "error": str(e),
        }


@traceable(
    name="label_evaluator_batch",
    run_type="chain",
    tags=["label-evaluator", "batch"],
)
def run_batch_traced(
    graph: Any,
    states: list[Any],
    execution_id: Optional[str] = None,
) -> list[dict]:
    """
    Run the compute-only graph on multiple receipts with batch tracing.

    Creates a parent trace with child traces for each receipt, making it
    easy to see all evaluations in a single LangSmith session.

    Args:
        graph: Compiled compute-only workflow graph
        states: List of pre-loaded EvaluatorState objects
        execution_id: Optional execution ID for correlation

    Returns:
        List of evaluation result dicts
    """
    results = []

    for i, state in enumerate(states):
        logger.info(
            "Processing receipt %d/%d: %s#%s",
            i + 1,
            len(states),
            state.image_id,
            state.receipt_id,
        )

        result = run_compute_only_traced(
            graph=graph,
            initial_state=state,
            execution_id=execution_id,
        )
        results.append(result)

    total_issues = sum(r.get("issues_found", 0) for r in results)
    logger.info(
        "Batch complete: %d receipts, %d total issues",
        len(results),
        total_issues,
    )

    return results


def create_traced_run_tree(
    name: str,
    run_type: str = "chain",
    metadata: Optional[dict] = None,
) -> Optional["RunTree"]:
    """
    Create a LangSmith RunTree for manual trace management.

    Use this when you need more control over trace hierarchy, such as
    creating a parent trace that spans multiple Lambda invocations.

    Args:
        name: Name for the trace
        run_type: Type of run ("chain", "llm", "tool", etc.)
        metadata: Optional metadata to attach

    Returns:
        RunTree if LangSmith is available, None otherwise

    Example:
        ```python
        # In Step Function coordinator Lambda
        parent_tree = create_traced_run_tree(
            name="batch_evaluation",
            metadata={"execution_id": exec_id, "receipt_count": len(items)},
        )

        if parent_tree:
            parent_tree.post()  # Start the trace

            for item in items:
                # Each receipt evaluation will be a child trace
                child = parent_tree.create_child(
                    name=f"evaluate_{item['image_id']}",
                    run_type="chain",
                )
                child.post()

                # ... run evaluation ...

                child.patch(outputs={"issues_found": result["issues_found"]})
                child.end()

            parent_tree.end()
        ```
    """
    if not HAS_LANGSMITH or RunTree is None:
        return None

    try:
        return RunTree(
            name=name,
            run_type=run_type,
            extra={"metadata": metadata or {}},
        )
    except Exception as e:
        logger.warning("Failed to create RunTree: %s", e)
        return None
