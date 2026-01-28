"""
Dataset schemas for QA RAG evaluation.

Defines Pydantic models for LangSmith dataset structure:
- QARAGDatasetInput: Question and metadata
- QARAGDatasetOutput: Agent outputs
- QARAGDatasetReference: Ground truth for evaluation

These schemas ensure consistency between:
- Dataset building (build_qa_golden_dataset.py)
- Agent outputs (graph.py)
- Evaluators (langsmith_evaluator.py)

Usage:
    from receipt_agent.agents.question_answering.dataset_schema import (
        QARAGDatasetInput,
        QARAGDatasetOutput,
        QARAGDatasetReference,
    )

    # Validate input
    input_data = QARAGDatasetInput(
        question="How much did I spend on coffee?",
        question_type="specific_item",
    )

    # Validate reference
    reference = QARAGDatasetReference(
        expected_amount=15.99,
        expected_receipt_count=2,
    )
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ==============================================================================
# Receipt Identifier
# ==============================================================================


class ReceiptIdentifier(BaseModel):
    """Unique identifier for a receipt."""

    image_id: str = Field(description="S3 image identifier")
    receipt_id: int = Field(description="Receipt number within image")

    def __hash__(self) -> int:
        """Make hashable for set operations."""
        return hash((self.image_id, self.receipt_id))

    def __eq__(self, other: Any) -> bool:
        """Check equality."""
        if isinstance(other, ReceiptIdentifier):
            return (
                self.image_id == other.image_id
                and self.receipt_id == other.receipt_id
            )
        if isinstance(other, tuple):
            return (self.image_id, self.receipt_id) == other
        if isinstance(other, dict):
            return (
                self.image_id == other.get("image_id")
                and self.receipt_id == other.get("receipt_id")
            )
        return False


# ==============================================================================
# Evidence Item
# ==============================================================================


class EvidenceItem(BaseModel):
    """Evidence item from a receipt."""

    image_id: str = Field(description="Receipt image ID")
    receipt_id: int = Field(description="Receipt number")
    item: Optional[str] = Field(
        default=None,
        description="Item description or text",
    )
    amount: Optional[float] = Field(
        default=None,
        description="Dollar amount",
    )

    class Config:
        extra = "allow"  # Allow additional fields


# ==============================================================================
# Dataset Input Schema
# ==============================================================================


class QARAGDatasetInput(BaseModel):
    """Input schema for QA RAG dataset examples.

    This is what the agent receives as input.
    """

    question: str = Field(
        description="The user's question about receipts",
        min_length=5,
    )

    question_type: Literal[
        "specific_item",
        "aggregation",
        "time_based",
        "comparison",
        "list_query",
        "metadata_query",
    ] = Field(
        default="specific_item",
        description="Classification of question type",
    )

    # Optional context hints
    time_range: Optional[str] = Field(
        default=None,
        description="Time range hint (e.g., 'last week', '2024-01')",
    )

    category_hint: Optional[str] = Field(
        default=None,
        description="Category hint (e.g., 'groceries', 'dining')",
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "question": "How much did I spend on coffee this month?",
                    "question_type": "specific_item",
                },
                {
                    "question": "What's my total spending at Trader Joe's?",
                    "question_type": "aggregation",
                },
                {
                    "question": "Show me all receipts with dairy products",
                    "question_type": "list_query",
                },
            ]
        }


# ==============================================================================
# Dataset Output Schema
# ==============================================================================


class QARAGDatasetOutput(BaseModel):
    """Output schema for QA RAG agent results.

    This is what the agent produces.
    """

    answer: str = Field(
        description="Natural language answer to the question",
    )

    total_amount: Optional[float] = Field(
        default=None,
        description="Total dollar amount for 'how much' questions",
    )

    receipt_count: int = Field(
        default=0,
        description="Number of receipts in the answer",
    )

    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Supporting evidence from receipts",
    )

    # Optional: agent trace data
    messages: Optional[list[Any]] = Field(
        default=None,
        description="LLM message history (for trajectory evaluation)",
    )

    iteration_count: Optional[int] = Field(
        default=None,
        description="Number of agent iterations",
    )

    tools_used: Optional[list[str]] = Field(
        default=None,
        description="List of tools called during execution",
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "answer": "You spent $15.99 on coffee across 2 receipts",
                    "total_amount": 15.99,
                    "receipt_count": 2,
                    "evidence": [
                        {
                            "image_id": "img_001",
                            "receipt_id": 0,
                            "item": "COFFEE",
                            "amount": 5.99,
                        },
                        {
                            "image_id": "img_002",
                            "receipt_id": 0,
                            "item": "COLD BREW",
                            "amount": 10.00,
                        },
                    ],
                }
            ]
        }


# ==============================================================================
# Dataset Reference Schema (Ground Truth)
# ==============================================================================


class QARAGDatasetReference(BaseModel):
    """Reference/ground truth schema for QA RAG evaluation.

    This is the expected output for scoring.
    """

    # Expected answer components
    expected_answer: Optional[str] = Field(
        default=None,
        description="Expected answer text (for completeness evaluation)",
    )

    expected_amount: Optional[float] = Field(
        default=None,
        description="Expected total amount for accuracy evaluation",
    )

    expected_receipt_count: Optional[int] = Field(
        default=None,
        description="Expected number of receipts",
    )

    # Retrieval ground truth
    relevant_receipt_ids: list[ReceiptIdentifier] = Field(
        default_factory=list,
        description="List of relevant receipt IDs for retrieval evaluation",
    )

    # Tool expectations
    expected_tools: list[str] = Field(
        default_factory=list,
        description="Expected tools to be called",
    )

    expected_steps: Optional[int] = Field(
        default=None,
        description="Expected number of steps for trajectory evaluation",
    )

    # Metadata
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium",
        description="Question difficulty for stratified analysis",
    )

    tags: list[str] = Field(
        default_factory=list,
        description="Tags for filtering (e.g., 'amount', 'list', 'merchant')",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Human notes about this example",
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "expected_amount": 15.99,
                    "expected_receipt_count": 2,
                    "relevant_receipt_ids": [
                        {"image_id": "img_001", "receipt_id": 0},
                        {"image_id": "img_002", "receipt_id": 0},
                    ],
                    "expected_tools": [
                        "search_receipts",
                        "get_receipt",
                        "aggregate_amounts",
                    ],
                    "difficulty": "easy",
                    "tags": ["amount", "specific_item"],
                }
            ]
        }


# ==============================================================================
# Full Dataset Example
# ==============================================================================


class QARAGDatasetExample(BaseModel):
    """Complete dataset example combining input and reference.

    Use this for creating/loading full dataset examples.
    """

    # Unique identifier
    example_id: Optional[str] = Field(
        default=None,
        description="Unique example identifier",
    )

    # Input
    inputs: QARAGDatasetInput = Field(
        description="Agent inputs (question + metadata)",
    )

    # Reference outputs (ground truth)
    reference_outputs: QARAGDatasetReference = Field(
        description="Expected outputs for evaluation",
    )

    # Source tracking
    source: Literal["manual", "auto", "from_run"] = Field(
        default="manual",
        description="How this example was created",
    )

    source_run_id: Optional[str] = Field(
        default=None,
        description="LangSmith run ID if created from a run",
    )

    created_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp when created",
    )

    def to_langsmith_format(self) -> dict:
        """Convert to LangSmith dataset format.

        Returns:
            Dict with 'inputs' and 'outputs' keys
        """
        return {
            "inputs": self.inputs.model_dump(),
            "outputs": self.reference_outputs.model_dump(),
            "metadata": {
                "example_id": self.example_id,
                "source": self.source,
                "source_run_id": self.source_run_id,
                "created_at": self.created_at,
            },
        }

    @classmethod
    def from_agent_result(
        cls,
        question: str,
        question_type: str,
        result: dict,
        *,
        example_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> "QARAGDatasetExample":
        """Create from agent result (for auto-annotation).

        Args:
            question: The question asked
            question_type: Question classification
            result: Agent result dict
            example_id: Optional example ID
            run_id: Optional LangSmith run ID

        Returns:
            QARAGDatasetExample with auto-filled reference
        """
        # Extract evidence to get relevant receipts
        relevant_ids = []
        for e in result.get("evidence", []):
            if e.get("image_id") and e.get("receipt_id") is not None:
                relevant_ids.append(ReceiptIdentifier(
                    image_id=e["image_id"],
                    receipt_id=e["receipt_id"],
                ))

        # Deduplicate
        seen = set()
        unique_ids = []
        for rid in relevant_ids:
            key = (rid.image_id, rid.receipt_id)
            if key not in seen:
                seen.add(key)
                unique_ids.append(rid)

        return cls(
            example_id=example_id,
            inputs=QARAGDatasetInput(
                question=question,
                question_type=question_type,  # type: ignore
            ),
            reference_outputs=QARAGDatasetReference(
                expected_answer=result.get("answer"),
                expected_amount=result.get("total_amount"),
                expected_receipt_count=result.get("receipt_count"),
                relevant_receipt_ids=unique_ids,
                expected_tools=[],  # Can be filled in manually
            ),
            source="from_run" if run_id else "auto",
            source_run_id=run_id,
        )


# ==============================================================================
# Conversion Utilities
# ==============================================================================


def convert_to_langsmith_dataset(
    examples: list[QARAGDatasetExample],
) -> list[dict]:
    """Convert list of examples to LangSmith dataset format.

    Args:
        examples: List of QARAGDatasetExample objects

    Returns:
        List of dicts ready for LangSmith dataset upload
    """
    return [ex.to_langsmith_format() for ex in examples]


def parse_from_langsmith_dataset(
    dataset_examples: list[dict],
) -> list[QARAGDatasetExample]:
    """Parse LangSmith dataset format to example objects.

    Args:
        dataset_examples: List of dicts from LangSmith

    Returns:
        List of QARAGDatasetExample objects
    """
    results = []
    for raw in dataset_examples:
        inputs_data = raw.get("inputs", {})
        outputs_data = raw.get("outputs", raw.get("reference_outputs", {}))
        metadata = raw.get("metadata", {})

        # Parse relevant_receipt_ids
        relevant_ids = []
        for rid in outputs_data.get("relevant_receipt_ids", []):
            if isinstance(rid, dict):
                relevant_ids.append(ReceiptIdentifier(**rid))
            elif isinstance(rid, ReceiptIdentifier):
                relevant_ids.append(rid)

        example = QARAGDatasetExample(
            example_id=metadata.get("example_id"),
            inputs=QARAGDatasetInput(**inputs_data),
            reference_outputs=QARAGDatasetReference(
                expected_answer=outputs_data.get("expected_answer"),
                expected_amount=outputs_data.get("expected_amount"),
                expected_receipt_count=outputs_data.get("expected_receipt_count"),
                relevant_receipt_ids=relevant_ids,
                expected_tools=outputs_data.get("expected_tools", []),
                difficulty=outputs_data.get("difficulty", "medium"),
                tags=outputs_data.get("tags", []),
                notes=outputs_data.get("notes"),
            ),
            source=metadata.get("source", "manual"),
            source_run_id=metadata.get("source_run_id"),
            created_at=metadata.get("created_at"),
        )
        results.append(example)

    return results
