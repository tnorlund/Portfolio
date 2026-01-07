"""Entity schemas for LangSmith trace data.

This module provides typed Pydantic models for:
- Raw Parquet export schema (29 columns)
- Parsed trace data with typed fields
- Agent-specific input/output schemas
"""

from receipt_langsmith.entities.agentic import (
    AgenticValidationInputs,
    AgenticValidationOutputs,
)
from receipt_langsmith.entities.base import (
    LangSmithRun,
    RuntimeInfo,
    TraceMetadata,
)
from receipt_langsmith.entities.grouping import (
    GroupingInputs,
    GroupingOutputs,
    GroupingProposal,
    ReceiptBoundary,
)
from receipt_langsmith.entities.harmonizer import (
    HarmonizedField,
    HarmonizerInputs,
    HarmonizerOutputs,
    ReceiptMetadataSummary,
)
from receipt_langsmith.entities.label_evaluator import (
    CurrencyEvaluatorInputs,
    CurrencyEvaluatorOutputs,
    DrillDownWordTrace,
    EvaluateLabelsInputs,
    EvaluateLabelsOutputs,
    EvaluationIssueTrace,
    FinancialEvaluatorInputs,
    FinancialEvaluatorOutputs,
    FlaggedWordTrace,
    LabelEvaluatorInputs,
    LabelEvaluatorOutputs,
    LLMReviewOutputs,
    LLMReviewTrace,
    MetadataEvaluatorInputs,
    MetadataEvaluatorOutputs,
    ReviewDecisionTrace,
)
from receipt_langsmith.entities.parquet_schema import LangSmithRunRaw
from receipt_langsmith.entities.place_id_finder import (
    PlaceIdFinderInputs,
    PlaceIdFinderOutputs,
    ToolCallTrace,
)
from receipt_langsmith.entities.validation import (
    EvidenceType,
    MerchantCandidateTrace,
    ValidationAgentInputs,
    ValidationAgentOutputs,
    ValidationResultTrace,
    ValidationStatus,
    VerificationEvidenceTrace,
    VerificationStepTrace,
)

__all__ = [
    # Base schemas
    "LangSmithRun",
    "LangSmithRunRaw",
    "TraceMetadata",
    "RuntimeInfo",
    # Validation agent
    "ValidationStatus",
    "EvidenceType",
    "MerchantCandidateTrace",
    "VerificationEvidenceTrace",
    "VerificationStepTrace",
    "ValidationResultTrace",
    "ValidationAgentInputs",
    "ValidationAgentOutputs",
    # Label evaluator
    "DrillDownWordTrace",
    "EvaluationIssueTrace",
    "LLMReviewTrace",
    "ReviewDecisionTrace",
    "FlaggedWordTrace",
    "LabelEvaluatorInputs",
    "LabelEvaluatorOutputs",
    "EvaluateLabelsInputs",
    "EvaluateLabelsOutputs",
    "CurrencyEvaluatorInputs",
    "CurrencyEvaluatorOutputs",
    "MetadataEvaluatorInputs",
    "MetadataEvaluatorOutputs",
    "FinancialEvaluatorInputs",
    "FinancialEvaluatorOutputs",
    "LLMReviewOutputs",
    # Place ID finder
    "ToolCallTrace",
    "PlaceIdFinderInputs",
    "PlaceIdFinderOutputs",
    # Agentic validation
    "AgenticValidationInputs",
    "AgenticValidationOutputs",
    # Harmonizer
    "ReceiptMetadataSummary",
    "HarmonizedField",
    "HarmonizerInputs",
    "HarmonizerOutputs",
    # Grouping
    "ReceiptBoundary",
    "GroupingProposal",
    "GroupingInputs",
    "GroupingOutputs",
]
