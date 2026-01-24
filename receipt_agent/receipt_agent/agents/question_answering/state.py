"""State definition for question-answering agent.

This module defines the state schema for the ReAct RAG QA agent, including:
- QuestionClassification: Routing decisions for retrieval strategy
- RetrievedContext: Chunk with relevance metadata
- QuestionAnsweringState: Main workflow state (backwards compatible)
- EnhancedQAState: Extended state for 5-node ReAct graph
"""

from typing import Annotated, Literal, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# ==============================================================================
# Classification and Context Models
# ==============================================================================


class QuestionClassification(BaseModel):
    """Classification for routing decisions.

    Used by the plan node to determine retrieval strategy and tools.
    """

    question_type: Literal[
        "specific_product",  # "How much was the Kirkland Olive Oil?"
        "category_query",  # "How much on coffee?" - needs hybrid search
        "aggregation",  # "What's my total spending this month?"
        "time_based",  # "Show receipts from last week"
        "comparison",  # "Did I spend more on groceries or dining?"
        "list_query",  # "List all receipts with dairy"
        "metadata_query",  # "Which merchants have I visited?"
    ] = Field(description="Type of question for routing")

    retrieval_strategy: Literal[
        "text_only",  # Exact product name search
        "semantic_first",  # Concept/category - semantic search primary
        "hybrid_comprehensive",  # Both text AND semantic for complete coverage
        "aggregation_direct",  # Use get_receipt_summaries directly
    ] = Field(description="Strategy for retrieving relevant receipts")

    text_search_terms: list[str] = Field(
        default_factory=list,
        description="Exact terms to search with text search (e.g., COFFEE, ESPRESSO)",
    )

    semantic_queries: list[str] = Field(
        default_factory=list,
        description="Natural language queries for semantic search (e.g., 'coffee drinks')",
    )

    tools_to_use: list[str] = Field(
        default_factory=list,
        description="Recommended tools based on question type",
    )


class RetrievedContext(BaseModel):
    """Chunk with relevance metadata.

    Represents a retrieved receipt or receipt segment with scoring.
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


# ==============================================================================
# Workflow State Models
# ==============================================================================


class QuestionAnsweringState(BaseModel):
    """State for the question-answering workflow (backwards compatible).

    This is the original 2-node graph state. Use EnhancedQAState for the
    5-node ReAct graph.

    Attributes:
        question: The user's question to answer
        messages: Conversation history with the LLM
        search_results: Results from ChromaDB searches
        receipt_details: Fetched receipt details from DynamoDB
        answer: The final answer to the question
        evidence: Supporting evidence for the answer
    """

    question: str = Field(description="The user's question")
    messages: Annotated[list, add_messages] = Field(default_factory=list)
    search_results: list[dict] = Field(default_factory=list)
    receipt_details: list[dict] = Field(default_factory=list)
    answer: Optional[str] = None
    evidence: list[dict] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


class EnhancedQAState(BaseModel):
    """Enhanced state for the 5-node ReAct RAG workflow.

    Flow: START → plan → agent ⟷ tools → shape → synthesize → END

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
    shaped_context: list[RetrievedContext] = Field(
        default_factory=list,
        description="Filtered and reranked contexts for synthesis",
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

    # Backwards compatibility fields
    search_results: list[dict] = Field(default_factory=list)
    receipt_details: list[dict] = Field(default_factory=list)
    answer: Optional[str] = Field(
        default=None,
        description="Alias for final_answer (backwards compatibility)",
    )
    evidence: list[dict] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    @property
    def is_retrieval_complete(self) -> bool:
        """Check if retrieval phase has sufficient context."""
        return len(self.retrieved_contexts) > 0

    @property
    def should_retry_retrieval(self) -> bool:
        """Check if retrieval should retry (empty context after shaping)."""
        return (
            len(self.retrieved_contexts) > 0
            and len(self.shaped_context) == 0
            and self.iteration_count < 15
        )
