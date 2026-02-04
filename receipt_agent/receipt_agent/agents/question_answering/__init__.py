"""Question-answering agent for receipt data.

This agent answers questions about receipts using:
- ChromaDB for semantic search over receipt lines/words
- DynamoDB for receipt details and prices
- LangGraph for orchestration
- OpenRouter for LLM inference

5-node ReAct RAG workflow:
- plan: Classify question, determine retrieval strategy
- agent: ReAct tool loop with classification context
- tools: Execute tool calls
- shape: Post-retrieval context processing
- synthesize: Dedicated answer generation

Example questions:
- "How much did I spend on coffee this year?"
- "Show me all receipts with dairy products"
- "How much tax did I pay last quarter?"
"""

# Dataset schemas
from receipt_agent.agents.question_answering.dataset_schema import (
    EvidenceItem,
    QARAGDatasetExample,
    QARAGDatasetInput,
    QARAGDatasetOutput,
    QARAGDatasetReference,
    ReceiptIdentifier,
    convert_to_langsmith_dataset,
    parse_from_langsmith_dataset,
)

# Core workflow
from receipt_agent.agents.question_answering.graph import (
    PLAN_SYSTEM_PROMPT,
    SYNTHESIZE_PROMPT,
    SYNTHESIZE_SYSTEM_PROMPT,
    answer_question,
    answer_question_sync,
    create_qa_graph,
)

# Evaluation
from receipt_agent.agents.question_answering.langsmith_evaluator import (
    agent_trajectory_evaluator,
    answer_amount_accuracy_evaluator,
    answer_completeness_evaluator,
    answer_groundedness_evaluator,
    create_qa_evaluator,
    error_recovery_evaluator,
    qa_combined_evaluator,
    retrieval_evaluator,
    tool_choice_evaluator,
)

# State schemas
from receipt_agent.agents.question_answering.state import (
    AmountItem,
    AnswerWithEvidence,
    QAState,
    QuestionClassification,
    ReceiptSummary,
    RetrievedContext,
)

# Tools
from receipt_agent.agents.question_answering.tools import (
    SYSTEM_PROMPT,
    create_qa_tools,
)

# Tracing
from receipt_agent.agents.question_answering.tracing import (
    QA_PROJECT_NAME,
    QARunContext,
    QARunMetadata,
    log_qa_example_to_dataset,
    log_qa_feedback,
    trace_qa_batch,
    trace_qa_run,
)

__all__ = [
    # Core workflow
    "answer_question",
    "answer_question_sync",
    "create_qa_graph",
    "SYNTHESIZE_PROMPT",
    "PLAN_SYSTEM_PROMPT",
    "SYNTHESIZE_SYSTEM_PROMPT",
    # State schemas
    "AmountItem",
    "AnswerWithEvidence",
    "QAState",
    "QuestionClassification",
    "ReceiptSummary",
    "RetrievedContext",
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
