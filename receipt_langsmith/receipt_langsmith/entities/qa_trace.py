"""Pydantic models for QA Agent trace data from LangSmith.

These models parse QA-specific trace data from the LangSmith Parquet
exports. The QA Agent uses a 5-node workflow:
  plan → agent ⟷ tools → shape → synthesize

Each node produces typed outputs that are stored in the trace's
`outputs` JSON field.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class QATraceRoot(BaseModel):
    """Root run: qa_answer_question.

    Extracted from the root trace of a QA agent execution.
    """

    question: str = ""
    question_index: int = -1
    execution_id: str = ""
    answer: Optional[str] = None
    total_amount: Optional[float] = None
    receipt_count: int = 0
    evidence: list[dict] = Field(default_factory=list)


class QAPlanOutput(BaseModel):
    """Output of the plan node.

    Classifies the question and determines retrieval strategy.
    """

    question_type: str = ""
    retrieval_strategy: str = ""
    query_rewrites: list[str] = Field(default_factory=list)
    tools_to_use: list[str] = Field(default_factory=list)


class QAShapeOutput(BaseModel):
    """Output of the shape node.

    Transforms raw receipt data into structured summaries.
    """

    shaped_summaries: list[dict] = Field(default_factory=list)


class QASynthesizeOutput(BaseModel):
    """Output of the synthesize node.

    Generates the final answer with evidence.
    """

    final_answer: str = ""
    total_amount: Optional[float] = None
    receipt_count: int = 0
    evidence: list[dict] = Field(default_factory=list)


QAStepType = Literal["plan", "agent", "tools", "shape", "synthesize"]


class QATraceStep(BaseModel):
    """A single step in the QA trace visualization.

    Used by the React component to render the trace timeline.
    """

    type: QAStepType
    content: str = ""
    detail: str = ""
    structured_data: Optional[list[dict]] = None
    receipts: Optional[list[dict]] = None


class QATraceStats(BaseModel):
    """Statistics for a single question's trace."""

    llm_calls: int = 0
    tool_invocations: int = 0
    receipts_processed: int = 0
    cost: float = 0.0


class QAQuestionCache(BaseModel):
    """Per-question cache JSON written to S3.

    This is the format consumed by the React QAAgentFlow component.
    """

    question: str
    question_index: int
    trace_id: str = ""
    trace: list[QATraceStep] = Field(default_factory=list)
    stats: QATraceStats = Field(default_factory=QATraceStats)


class QACacheMetadata(BaseModel):
    """Aggregate metadata for the QA viz cache."""

    total_questions: int = 0
    success_count: int = 0
    total_cost: float = 0.0
    avg_cost_per_question: float = 0.0
    generated_at: str = ""
    execution_id: str = ""
    langsmith_project: str = "qa-agent-marquee"
