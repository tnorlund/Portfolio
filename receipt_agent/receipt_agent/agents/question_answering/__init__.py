"""Question-answering agent for receipt data.

This agent answers questions about receipts using:
- ChromaDB for semantic search over receipt lines/words
- DynamoDB for receipt details and prices
- LangGraph for orchestration
- OpenRouter for LLM inference

Example questions:
- "How much did I spend on coffee this year?"
- "Show me all receipts with dairy products"
- "How much tax did I pay last quarter?"
"""

from receipt_agent.agents.question_answering.graph import (
    answer_question,
    answer_question_sync,
    create_qa_graph,
)
from receipt_agent.agents.question_answering.state import QuestionAnsweringState
from receipt_agent.agents.question_answering.tools import (
    QuestionContext,
    create_qa_tools,
)

__all__ = [
    "answer_question",
    "answer_question_sync",
    "create_qa_graph",
    "QuestionAnsweringState",
    "QuestionContext",
    "create_qa_tools",
]
