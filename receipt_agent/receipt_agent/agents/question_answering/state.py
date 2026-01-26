"""State definition for question-answering agent.

This module defines the state schema for the ReAct QA agent.
Simple design: messages for the ReAct loop, tracking for efficiency.
"""

from typing import Annotated, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class QAState(BaseModel):
    """State for the ReAct question-answering workflow.

    Flow: agent ⟷ tools → synthesize → END

    The agent loops until it stops calling tools, then synthesize
    formats the final answer with structured output.

    Attributes:
        question: The user's question to answer
        messages: Conversation history with the LLM (ReAct loop)
        iteration_count: Safety limit on tool loop iterations
    """

    question: str = Field(description="The user's question")
    messages: Annotated[list, add_messages] = Field(default_factory=list)
    iteration_count: int = Field(default=0, description="Tool loop iterations (max 10)")

    class Config:
        arbitrary_types_allowed = True


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
