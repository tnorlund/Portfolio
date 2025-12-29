"""Close receipt trace after parallel evaluation completes.

This Lambda runs when EvaluateLabels found 0 issues (LLMReview is skipped).
It closes the parent receipt trace that was left open by EvaluateLabels,
ensuring the trace is only closed AFTER all parallel branches complete.

Flow:
    ParallelEvaluation (3 branches) → CheckForIssues
        → (issues > 0) → LLMReviewReceipt (closes trace) → ReturnResult
        → (issues == 0) → CloseReceiptTrace (closes trace) → ReturnResult
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import logging
import os
import sys
from typing import Any

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TRACING_VERSION,
        end_receipt_trace_by_id,
        flush_langsmith_traces,
    )

    _tracing_import_source = "container"
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from tracing import (
        TRACING_VERSION,
        end_receipt_trace_by_id,
        flush_langsmith_traces,
    )

    _tracing_import_source = "local"

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Close the receipt trace when no issues were found.

    This handler runs after all parallel evaluation branches complete,
    ensuring Currency and Metadata evaluators have finished before
    closing the parent trace.

    Input (from Step Function):
    {
        "trace_id": "...",
        "root_run_id": "...",
        "image_id": "...",
        "receipt_id": 1,
        "issues_found": 0,
        "currency_decisions": {"VALID": 8, "INVALID": 2, "NEEDS_REVIEW": 0},
        "metadata_decisions": {"VALID": 29, "INVALID": 7, "NEEDS_REVIEW": 0},
        "enable_tracing": true
    }

    Output: Same as input (pass-through for downstream aggregation)
    """
    logger.info(
        "[CloseReceiptTrace] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    trace_id = event.get("trace_id")
    root_run_id = event.get("root_run_id")
    image_id = event.get("image_id", "")
    receipt_id = event.get("receipt_id", 0)
    enable_tracing = event.get("enable_tracing", True)

    if not enable_tracing:
        logger.info("Tracing disabled, skipping trace close")
        return event

    if trace_id and root_run_id:
        logger.info(
            "Closing receipt trace (image_id=%s, receipt_id=%s, trace_id=%s)",
            image_id[:8] if len(str(image_id)) > 8 else image_id,
            receipt_id,
            trace_id[:8] if len(trace_id) > 8 else trace_id,
        )

        # Build outputs summary from all parallel evaluators
        outputs = {
            "status": "completed",
            "issues_found": event.get("issues_found", 0),
            "llm_review": "skipped",
            # Currency evaluation results
            "currency_words_evaluated": event.get(
                "currency_words_evaluated", 0
            ),
            "currency_decisions": event.get("currency_decisions"),
            # Metadata evaluation results
            "metadata_words_evaluated": event.get(
                "metadata_words_evaluated", 0
            ),
            "metadata_decisions": event.get("metadata_decisions"),
        }

        try:
            end_receipt_trace_by_id(trace_id, root_run_id, outputs=outputs)
            logger.info("Receipt trace closed successfully")
        except Exception as e:
            logger.warning("Failed to close receipt trace: %s", e)

        # Flush traces to LangSmith
        flush_langsmith_traces()
    else:
        logger.warning(
            "No trace_id/root_run_id provided (trace_id=%s, root_run_id=%s), skipping trace close",
            trace_id,
            root_run_id,
        )

    # Pass through all results unchanged
    return event
