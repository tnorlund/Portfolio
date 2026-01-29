"""
LangSmith Tracing Integration for QA RAG Agent.

Provides tracing utilities for dataset building and feedback collection:
- trace_qa_run: @traceable decorator for comprehensive run logging
- log_qa_feedback: Record human corrections for ground truth
- QARunContext: Context manager for tracking runs

Project: question-answering-rag

Usage:
    from receipt_agent.agents.question_answering.tracing import (
        trace_qa_run,
        log_qa_feedback,
        QARunContext,
    )

    # Trace a QA run
    @trace_qa_run
    async def answer_question(question: str) -> dict:
        ...

    # Log feedback for a run
    log_qa_feedback(
        run_id="abc123",
        correction="The correct amount is $15.99",
        score=0.5,
    )

    # Context manager
    with QARunContext(question="How much for coffee?") as ctx:
        result = qa_agent(question)
        ctx.set_result(result)
"""

import functools
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# LangSmith project name for QA RAG
QA_PROJECT_NAME = "question-answering-rag"

# Type variable for generic decorators
F = TypeVar("F", bound=Callable[..., Any])


# ==============================================================================
# LangSmith Integration
# ==============================================================================


def _get_langsmith_client() -> Any:
    """Get LangSmith client if available."""
    try:
        from langsmith import Client

        api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get(
            "LANGSMITH_API_KEY"
        )
        if not api_key:
            logger.debug("No LangSmith API key found")
            return None

        return Client(api_key=api_key)
    except ImportError:
        logger.debug("langsmith package not installed")
        return None
    except Exception as e:
        logger.warning("Failed to create LangSmith client: %s", e)
        return None


def _get_traceable_decorator() -> Optional[Callable]:
    """Get langsmith.traceable decorator if available."""
    try:
        from langsmith import traceable

        return traceable
    except ImportError:
        return None


# ==============================================================================
# Run Context
# ==============================================================================


@dataclass
class QARunMetadata:
    """Metadata for a QA run."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    question: str = ""
    question_type: Optional[str] = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None

    # Inputs
    retrieval_strategy: Optional[str] = None
    tools_used: list[str] = field(default_factory=list)
    iteration_count: int = 0

    # Outputs
    answer: Optional[str] = None
    total_amount: Optional[float] = None
    receipt_count: int = 0
    evidence: list[dict] = field(default_factory=list)

    # Quality indicators
    error: Optional[str] = None
    success: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "run_id": self.run_id,
            "question": self.question,
            "question_type": self.question_type,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "retrieval_strategy": self.retrieval_strategy,
            "tools_used": self.tools_used,
            "iteration_count": self.iteration_count,
            "answer": self.answer,
            "total_amount": self.total_amount,
            "receipt_count": self.receipt_count,
            "evidence_count": len(self.evidence),
            "error": self.error,
            "success": self.success,
        }


class QARunContext:
    """Context manager for tracking QA runs.

    Usage:
        with QARunContext(question="How much for coffee?") as ctx:
            result = qa_agent(question)
            ctx.set_result(result)
            ctx.add_tool("search_receipts")

        # Access metadata after context
        print(ctx.metadata.duration_ms)
    """

    def __init__(
        self,
        question: str,
        question_type: Optional[str] = None,
        project: str = QA_PROJECT_NAME,
    ):
        """Initialize run context.

        Args:
            question: The question being answered
            question_type: Optional classification (specific_item, aggregation, etc.)
            project: LangSmith project name
        """
        self.metadata = QARunMetadata(
            question=question,
            question_type=question_type,
        )
        self.project = project
        self._client = _get_langsmith_client()
        self._run = None

    def __enter__(self) -> "QARunContext":
        """Start tracking the run."""
        self.metadata.start_time = datetime.now(timezone.utc)

        # Start LangSmith run if available
        if self._client:
            try:
                self._run = self._client.create_run(
                    name="qa_answer_question",
                    run_type="chain",
                    project_name=self.project,
                    inputs={"question": self.metadata.question},
                    extra={"metadata": {"question_type": self.metadata.question_type}},
                )
            except Exception as e:
                logger.warning("Failed to create LangSmith run: %s", e)

        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """End tracking and log results."""
        self.metadata.end_time = datetime.now(timezone.utc)
        self.metadata.duration_ms = (
            self.metadata.end_time - self.metadata.start_time
        ).total_seconds() * 1000

        if exc_type:
            self.metadata.error = str(exc_val)
            self.metadata.success = False

        # End LangSmith run if available
        if self._client and self._run:
            try:
                self._client.update_run(
                    self._run.id,
                    outputs={
                        "answer": self.metadata.answer,
                        "total_amount": self.metadata.total_amount,
                        "receipt_count": self.metadata.receipt_count,
                        "evidence": self.metadata.evidence,
                    },
                    end_time=self.metadata.end_time,
                    error=self.metadata.error,
                    extra={
                        "metadata": {
                            "tools_used": self.metadata.tools_used,
                            "iteration_count": self.metadata.iteration_count,
                            "duration_ms": self.metadata.duration_ms,
                        }
                    },
                )
            except Exception as e:
                logger.warning("Failed to update LangSmith run: %s", e)

        # Log locally
        logger.info(
            "QA Run completed: %s",
            self.metadata.to_dict(),
        )

    def set_result(self, result: dict) -> None:
        """Set the result from the QA agent.

        Args:
            result: Dict with answer, total_amount, receipt_count, evidence
        """
        self.metadata.answer = result.get("answer")
        self.metadata.total_amount = result.get("total_amount")
        self.metadata.receipt_count = result.get("receipt_count", 0)
        self.metadata.evidence = result.get("evidence", [])

    def add_tool(self, tool_name: str) -> None:
        """Record a tool that was used.

        Args:
            tool_name: Name of the tool (e.g., "search_receipts")
        """
        if tool_name not in self.metadata.tools_used:
            self.metadata.tools_used.append(tool_name)

    def set_iteration_count(self, count: int) -> None:
        """Set the iteration count.

        Args:
            count: Number of agent iterations
        """
        self.metadata.iteration_count = count

    def set_retrieval_strategy(self, strategy: str) -> None:
        """Set the retrieval strategy used.

        Args:
            strategy: Strategy name (simple_lookup, multi_source, etc.)
        """
        self.metadata.retrieval_strategy = strategy


# ==============================================================================
# Traceable Decorator
# ==============================================================================


def trace_qa_run(
    func: Optional[F] = None,
    *,
    name: Optional[str] = None,
    project: str = QA_PROJECT_NAME,
) -> F:
    """Decorator to trace QA agent runs with LangSmith.

    Wraps a function to automatically log inputs, outputs, and timing
    to LangSmith for dataset building and evaluation.

    Args:
        func: The function to wrap
        name: Optional custom name for the trace
        project: LangSmith project name

    Returns:
        Wrapped function with tracing

    Usage:
        @trace_qa_run
        async def answer_question(question: str) -> dict:
            ...

        @trace_qa_run(name="custom_qa")
        def sync_answer(question: str) -> dict:
            ...
    """

    def decorator(fn: F) -> F:
        trace_name = name or fn.__name__

        # Check if langsmith.traceable is available
        traceable = _get_traceable_decorator()

        if traceable:
            # Use LangSmith's traceable decorator
            traced_fn = traceable(
                name=trace_name,
                project_name=project,
                metadata={"agent": "qa_rag"},
            )(fn)

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return traced_fn(*args, **kwargs)

            return wrapper  # type: ignore
        else:
            # Fallback: just log locally
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                start = datetime.now(timezone.utc)
                try:
                    result = fn(*args, **kwargs)
                    duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                    logger.info(
                        "QA trace [%s]: duration=%.1fms, success=True",
                        trace_name,
                        duration,
                    )
                    return result
                except Exception as e:
                    duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                    logger.error(
                        "QA trace [%s]: duration=%.1fms, error=%s",
                        trace_name,
                        duration,
                        e,
                    )
                    raise

            return wrapper  # type: ignore

    if func is None:
        return decorator  # type: ignore
    return decorator(func)


# ==============================================================================
# Feedback Logging
# ==============================================================================


def log_qa_feedback(
    run_id: str,
    correction: Optional[str] = None,
    score: Optional[float] = None,
    expected_amount: Optional[float] = None,
    expected_receipt_count: Optional[int] = None,
    relevant_receipt_ids: Optional[list[dict]] = None,
    comment: Optional[str] = None,
    source: str = "human",
) -> bool:
    """Log feedback/correction for a QA run.

    Used to build ground truth datasets from human corrections.

    Args:
        run_id: LangSmith run ID to attach feedback to
        correction: Corrected answer text
        score: Overall score (0.0 to 1.0)
        expected_amount: Correct total amount
        expected_receipt_count: Correct receipt count
        relevant_receipt_ids: List of {image_id, receipt_id} for relevant receipts
        comment: Human comment/notes
        source: Feedback source ("human", "auto", "model")

    Returns:
        True if feedback was logged successfully

    Usage:
        log_qa_feedback(
            run_id="abc123",
            correction="You spent $15.99 on coffee",
            score=0.8,
            expected_amount=15.99,
            expected_receipt_count=2,
        )
    """
    client = _get_langsmith_client()

    if not client:
        logger.warning("Cannot log feedback: LangSmith client unavailable")
        return False

    try:
        # Build feedback data
        feedback_value = {}
        if correction:
            feedback_value["correction"] = correction
        if expected_amount is not None:
            feedback_value["expected_amount"] = expected_amount
        if expected_receipt_count is not None:
            feedback_value["expected_receipt_count"] = expected_receipt_count
        if relevant_receipt_ids:
            feedback_value["relevant_receipt_ids"] = relevant_receipt_ids

        # Create feedback
        client.create_feedback(
            run_id=run_id,
            key="qa_correction" if correction else "qa_score",
            score=score,
            value=feedback_value if feedback_value else None,
            comment=comment,
            source_type=source,
        )

        logger.info(
            "Logged feedback for run %s: score=%s, has_correction=%s",
            run_id,
            score,
            bool(correction),
        )
        return True

    except Exception as e:
        logger.error("Failed to log feedback: %s", e)
        return False


def log_qa_example_to_dataset(
    dataset_name: str,
    question: str,
    question_type: str,
    expected_answer: str,
    expected_amount: Optional[float] = None,
    expected_receipt_count: Optional[int] = None,
    relevant_receipt_ids: Optional[list[dict]] = None,
    expected_tools: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> bool:
    """Log a QA example directly to a LangSmith dataset.

    Args:
        dataset_name: Name of the LangSmith dataset
        question: The question
        question_type: Classification (specific_item, aggregation, etc.)
        expected_answer: Ground truth answer
        expected_amount: Expected total amount (if applicable)
        expected_receipt_count: Expected number of receipts
        relevant_receipt_ids: List of {image_id, receipt_id}
        expected_tools: List of expected tool names
        metadata: Additional metadata

    Returns:
        True if example was added successfully
    """
    client = _get_langsmith_client()

    if not client:
        logger.warning("Cannot log example: LangSmith client unavailable")
        return False

    try:
        # Get or create dataset
        try:
            dataset = client.read_dataset(dataset_name=dataset_name)
        except Exception:
            dataset = client.create_dataset(
                dataset_name=dataset_name,
                description="QA RAG golden dataset for receipt questions",
            )

        # Create example
        client.create_example(
            dataset_id=dataset.id,
            inputs={
                "question": question,
                "question_type": question_type,
            },
            outputs={
                "expected_answer": expected_answer,
            },
            metadata={
                "expected_amount": expected_amount,
                "expected_receipt_count": expected_receipt_count,
                "relevant_receipt_ids": relevant_receipt_ids or [],
                "expected_tools": expected_tools or [],
                **(metadata or {}),
            },
        )

        logger.info("Added example to dataset %s: %s", dataset_name, question[:50])
        return True

    except Exception as e:
        logger.error("Failed to log example: %s", e)
        return False


# ==============================================================================
# Batch Tracing
# ==============================================================================


@contextmanager
def trace_qa_batch(
    batch_name: str,
    questions: list[str],
    project: str = QA_PROJECT_NAME,
):
    """Context manager for tracing a batch of QA runs.

    Args:
        batch_name: Name for this batch of questions
        questions: List of questions to be run
        project: LangSmith project name

    Yields:
        BatchContext with run tracking

    Usage:
        with trace_qa_batch("marquee_questions", questions) as batch:
            for q in questions:
                result = qa_agent(q)
                batch.record_result(q, result)
    """

    @dataclass
    class BatchContext:
        batch_id: str
        total_questions: int
        results: list[dict] = field(default_factory=list)
        errors: list[dict] = field(default_factory=list)

        def record_result(self, question: str, result: dict) -> None:
            """Record a successful result."""
            self.results.append({
                "question": question,
                "answer": result.get("answer"),
                "total_amount": result.get("total_amount"),
                "receipt_count": result.get("receipt_count"),
            })

        def record_error(self, question: str, error: str) -> None:
            """Record an error."""
            self.errors.append({
                "question": question,
                "error": error,
            })

        @property
        def success_rate(self) -> float:
            """Calculate success rate."""
            total = len(self.results) + len(self.errors)
            if total == 0:
                return 0.0
            return len(self.results) / total

    batch_id = str(uuid.uuid4())[:8]
    context = BatchContext(
        batch_id=batch_id,
        total_questions=len(questions),
    )

    start_time = datetime.now(timezone.utc)
    logger.info(
        "Starting QA batch %s: %d questions",
        batch_name,
        len(questions),
    )

    try:
        yield context
    finally:
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(
            "QA batch %s completed: %d/%d success (%.1f%%), %.1fs",
            batch_name,
            len(context.results),
            context.total_questions,
            context.success_rate * 100,
            duration,
        )

        # Log to LangSmith if available
        client = _get_langsmith_client()
        if client:
            try:
                client.create_run(
                    name=f"qa_batch_{batch_name}",
                    run_type="chain",
                    project_name=project,
                    inputs={"questions": questions},
                    outputs={
                        "results": context.results,
                        "errors": context.errors,
                        "success_rate": context.success_rate,
                    },
                    extra={
                        "metadata": {
                            "batch_id": batch_id,
                            "total_questions": context.total_questions,
                            "duration_seconds": duration,
                        }
                    },
                )
            except Exception as e:
                logger.warning("Failed to log batch to LangSmith: %s", e)
