"""Question-answering agent for receipt data.

This agent answers questions about receipts using:
- ChromaDB for semantic search over receipt lines/words
- DynamoDB for receipt details and prices
- LangGraph for orchestration
- OpenRouter for LLM inference

Simple ReAct workflow:
- agent: LLM decides to call tools or respond
- tools: Execute tool calls
- synthesize: Format final answer with evidence

Example questions:
- "How much did I spend on coffee this year?"
- "Show me all receipts with dairy products"
- "How much tax did I pay last quarter?"
"""

# Core workflow
from receipt_agent.agents.question_answering.graph import (
    answer_question,
    answer_question_sync,
    create_qa_graph,
    SYNTHESIZE_PROMPT,
)

# State schemas
from receipt_agent.agents.question_answering.state import (
    AnswerWithEvidence,
    QAState,
)

# Tools
from receipt_agent.agents.question_answering.tools import (
    create_qa_tools,
    SYSTEM_PROMPT,
)

# Evaluation
from receipt_agent.agents.question_answering.langsmith_evaluator import (
    create_qa_evaluator,
    retrieval_evaluator,
    answer_groundedness_evaluator,
    answer_amount_accuracy_evaluator,
    answer_completeness_evaluator,
    agent_trajectory_evaluator,
    tool_choice_evaluator,
    error_recovery_evaluator,
    qa_combined_evaluator,
)

# Tracing
from receipt_agent.agents.question_answering.tracing import (
    trace_qa_run,
    log_qa_feedback,
    log_qa_example_to_dataset,
    QARunContext,
    QARunMetadata,
    trace_qa_batch,
    QA_PROJECT_NAME,
)

# Dataset schemas
from receipt_agent.agents.question_answering.dataset_schema import (
    QARAGDatasetInput,
    QARAGDatasetOutput,
    QARAGDatasetReference,
    QARAGDatasetExample,
    ReceiptIdentifier,
    EvidenceItem,
    convert_to_langsmith_dataset,
    parse_from_langsmith_dataset,
)

__all__ = [
    # Core workflow
    "answer_question",
    "answer_question_sync",
    "create_qa_graph",
    "SYNTHESIZE_PROMPT",
    # State schemas
    "AnswerWithEvidence",
    "QAState",
    # Tools
    "create_qa_tools",
    "SYSTEM_PROMPT",
    # Evaluation
    "create_qa_evaluator",
    "retrieval_evaluator",
    "answer_groundedness_evaluator",
    "answer_amount_accuracy_evaluator",
    "answer_completeness_evaluator",
    "agent_trajectory_evaluator",
    "tool_choice_evaluator",
    "error_recovery_evaluator",
    "qa_combined_evaluator",
    # Tracing
    "trace_qa_run",
    "log_qa_feedback",
    "log_qa_example_to_dataset",
    "QARunContext",
    "QARunMetadata",
    "trace_qa_batch",
    "QA_PROJECT_NAME",
    # Dataset schemas
    "QARAGDatasetInput",
    "QARAGDatasetOutput",
    "QARAGDatasetReference",
    "QARAGDatasetExample",
    "ReceiptIdentifier",
    "EvidenceItem",
    "convert_to_langsmith_dataset",
    "parse_from_langsmith_dataset",
]
