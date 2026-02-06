"""State definition for question-answering agent.

This module defines the state schema for the 5-node ReAct RAG QA agent:
- QuestionClassification: Routing decisions for retrieval strategy
- RetrievedContext: Chunk with relevance metadata
- QAState: Main workflow state for 5-node graph

Flow: START -> plan -> agent <-> tools -> shape -> synthesize -> END
"""

from typing import Annotated, Literal, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class QuestionClassification(BaseModel):
    """Classification for routing decisions.

    Used by the plan node to determine retrieval strategy and tools.
    """

    question_type: Literal[
        "specific_item",  # "How much did I spend on coffee?"
        "aggregation",  # "What's my total spending this month?"
        "time_based",  # "Show receipts from last week"
        "comparison",  # "Did I spend more on groceries or dining?"
        "list_query",  # "List all receipts with dairy"
        "metadata_query",  # "Which merchants have I visited?"
    ] = Field(description="Type of question for routing")

    retrieval_strategy: Literal[
        "simple_lookup",  # Single search, few receipts expected
        "multi_source",  # Multiple searches needed (e.g., different terms)
        "exhaustive_scan",  # Need to check many/all receipts
        "semantic_hybrid",  # Combine text and semantic search
    ] = Field(description="Strategy for retrieving relevant receipts")

    query_rewrites: list[str] = Field(
        default_factory=list,
        description="Alternative queries to try if initial search fails",
    )

    tools_to_use: list[str] = Field(
        default_factory=list,
        description="Recommended tools based on question type",
    )


class AmountItem(BaseModel):
    """A labeled amount from a receipt."""

    label: str = Field(
        description="Label type: LINE_TOTAL, GRAND_TOTAL, TAX, etc."
    )
    amount: float = Field(description="Dollar amount")
    item_text: Optional[str] = Field(
        default=None,
        description="Associated item text (e.g., 'Grande Latte')",
    )


class ReceiptSummary(BaseModel):
    """Structured summary of a receipt for synthesis.

    This is the output of the shape node - contains only what synthesize needs.
    Much more compact than raw receipt text (~200 chars vs ~2000 chars).
    """

    image_id: str = Field(description="S3 image identifier")
    receipt_id: int = Field(description="Receipt number within image")
    merchant: str = Field(description="Merchant name")
    grand_total: Optional[float] = Field(
        default=None,
        description="Receipt total amount",
    )
    tax: Optional[float] = Field(
        default=None,
        description="Tax amount",
    )
    tip: Optional[float] = Field(
        default=None,
        description="Tip amount (from summary records)",
    )
    date: Optional[str] = Field(
        default=None,
        description="Receipt date as ISO string",
    )
    item_count: Optional[int] = Field(
        default=None,
        description="Number of items on the receipt",
    )
    line_items: list[AmountItem] = Field(
        default_factory=list,
        description="Individual line items with amounts",
    )
    labels_found: list[str] = Field(
        default_factory=list,
        description="Label types present on this receipt",
    )


class RetrievedContext(BaseModel):
    """Chunk with relevance metadata.

    Represents a retrieved receipt or receipt segment with scoring.
    Used during retrieval phase before shaping.
    """

    image_id: str = Field(description="S3 image identifier")
    receipt_id: int = Field(description="Receipt number within image")
    text: str = Field(description="Retrieved text content")
    relevance_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Relevance score (1.0 = highly relevant)",
    )
    labels_found: list[str] = Field(
        default_factory=list,
        description="Labels present in this context (e.g., TAX, PRODUCT_NAME)",
    )
    amounts: list[dict] = Field(
        default_factory=list,
        description="Extracted amounts with labels and values",
    )

    class Config:
        arbitrary_types_allowed = True


class QAState(BaseModel):
    """State for the 5-node ReAct RAG question-answering workflow.

    Flow: START -> plan -> agent <-> tools -> shape -> synthesize -> END

    Attributes:
        question: The user's question to answer
        messages: Conversation history with the LLM
        classification: Question classification from plan node
        retrieved_contexts: All retrieved chunks with metadata
        shaped_context: Filtered/reranked contexts for synthesis
        final_answer: The generated answer
        total_amount: Computed total for aggregation questions
        receipt_count: Number of receipts in the answer
        current_phase: Current workflow phase for routing
        iteration_count: Tool loop iterations for limiting
    """

    # Core fields
    question: str = Field(description="The user's question")
    messages: Annotated[list, add_messages] = Field(default_factory=list)

    # Planning phase
    classification: Optional[QuestionClassification] = Field(
        default=None,
        description="Question classification from plan node",
    )

    # Retrieval phase
    retrieved_contexts: list[RetrievedContext] = Field(
        default_factory=list,
        description="All retrieved chunks with metadata",
    )

    # Shaping phase
    shaped_summaries: list[ReceiptSummary] = Field(
        default_factory=list,
        description="Structured receipt summaries for synthesis",
    )

    # Synthesis phase
    final_answer: Optional[str] = Field(
        default=None,
        description="The generated answer",
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Computed total for 'how much' questions",
    )
    receipt_count: int = Field(
        default=0,
        description="Number of receipts supporting the answer",
    )
    evidence: list[dict] = Field(
        default_factory=list,
        description="Supporting evidence: [{image_id, receipt_id, item, amount}, ...]",
    )

    # Workflow control
    current_phase: Literal[
        "plan",
        "retrieve",
        "shape",
        "synthesize",
        "complete",
    ] = Field(
        default="plan",
        description="Current workflow phase",
    )
    iteration_count: int = Field(
        default=0,
        description="Tool loop iterations (max 15)",
    )

    class Config:
        arbitrary_types_allowed = True

    @property
    def is_retrieval_complete(self) -> bool:
        """Check if retrieval phase has sufficient context."""
        return len(self.retrieved_contexts) > 0

    @property
    def should_retry_retrieval(self) -> bool:
        """Check if retrieval should retry (empty summaries after shaping)."""
        return (
            len(self.retrieved_contexts) > 0
            and len(self.shaped_summaries) == 0
            and self.iteration_count < 15
        )


class AnswerWithEvidence(BaseModel):
    """Structured output for the synthesize node.

    This is the final response format returned to the user.
    """

    answer: str = Field(description="Natural language answer to the question")
    total_amount: Optional[float] = Field(
        default=None,
        description="Total dollar amount for 'how much' questions",
    )
    receipt_count: int = Field(
        default=0,
        description="Number of receipts supporting this answer",
    )
    evidence: list[dict] = Field(
        default_factory=list,
        description="Supporting evidence: [{image_id, receipt_id, item, amount}, ...]",
    )
