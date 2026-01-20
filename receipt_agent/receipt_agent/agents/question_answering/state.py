"""State definition for question-answering agent."""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class QuestionAnsweringState(BaseModel):
    """State for the question-answering workflow.

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
