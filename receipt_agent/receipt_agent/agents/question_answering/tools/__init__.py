"""Tools for the question-answering agent."""

from receipt_agent.agents.question_answering.tools.search import (
    SYSTEM_PROMPT,
    create_qa_tools,
)

try:
    from receipt_agent.agents.question_answering.tools.neo4j_search import (
        NEO4J_SYSTEM_PROMPT,
        create_neo4j_qa_tools,
    )
except ImportError:
    # receipt-neo4j not installed — Neo4j tools unavailable
    create_neo4j_qa_tools = None  # type: ignore[assignment]
    NEO4J_SYSTEM_PROMPT = None  # type: ignore[assignment]

__all__ = [
    "create_qa_tools",
    "create_neo4j_qa_tools",
    "SYSTEM_PROMPT",
    "NEO4J_SYSTEM_PROMPT",
]
