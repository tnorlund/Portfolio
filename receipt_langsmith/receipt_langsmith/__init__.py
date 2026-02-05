"""LangSmith trace schemas, API client, and analytics for receipt evaluation.

This package provides:

1. **Entity Schemas** (`entities/`):
   - Typed Pydantic models for all receipt agent traces
   - Raw Parquet schema (29 columns)
   - Agent-specific input/output schemas

2. **API Client** (`client/`):
   - Async/sync LangSmith API client with retry logic
   - Bulk export manager for triggering and monitoring exports

3. **Parsers** (`parsers/`):
   - Trace tree builder for parent-child hierarchy reconstruction
   - JSON field parsing utilities

4. **PySpark Analytics** (`spark/`):
   - EMR Serverless processor for large-scale analytics
   - Receipt metrics, step timing, decision analysis

Installation:
    pip install receipt-langsmith              # Base package
    pip install receipt-langsmith[pyspark]     # With PySpark
    pip install receipt-langsmith[full]        # Everything

Example:
    ```python
    from receipt_langsmith import (
        LangSmithClient,
        TraceTreeBuilder,
    )

    # API usage
    client = LangSmithClient()
    projects = client.list_projects()

    # Build trace trees from API traces
    traces = client.list_runs(project_name="my-project")
    builder = TraceTreeBuilder(traces)
    roots = builder.get_root_runs(name_filter="ReceiptEvaluation")
    ```
"""

from typing import TYPE_CHECKING

from receipt_langsmith._lazy_imports import (
    resolve_parquet_reader,
    resolve_pyspark_attr,
)

__version__ = "0.3.0"  # Remove pyarrow dependency, cleanup dead code

# Client (always available)
from receipt_langsmith.client import (
    BulkExportDestination,
    BulkExportManager,
    BulkExportRequest,
    BulkExportResponse,
    ExportJob,
    ExportStatus,
    LangSmithClient,
    Project,
)

# Core entities (always available)
# Base; Validation agent; Label evaluator; Other agents; Label validation
# entities (receipt-label-validation project)
from receipt_langsmith.entities import (
    AgenticValidationInputs,
    AgenticValidationOutputs,
    BoundingBox,
    ChromaDBUpsertOutputs,
    CurrencyEvaluatorInputs,
    CurrencyEvaluatorOutputs,
    DecisionCounts,
    DrillDownWordTrace,
    EvaluateLabelsInputs,
    EvaluateLabelsOutputs,
    EvaluationIssueTrace,
    EvaluatorResult,
    EvaluatorTiming,
    EvidenceType,
    FinancialEvaluatorInputs,
    FinancialEvaluatorOutputs,
    FlaggedWordTrace,
    GeometricResult,
    GroupingInputs,
    GroupingOutputs,
    GroupingProposal,
    HarmonizedField,
    HarmonizerInputs,
    HarmonizerOutputs,
    LabelEvaluatorInputs,
    LabelEvaluatorOutputs,
    LabelValidationInputs,
    LabelValidationOutputs,
    LabelValidationSummary,
    LangSmithRun,
    LangSmithRunRaw,
    LLMBatchValidationInputs,
    LLMBatchValidationOutputs,
    LLMReviewOutputs,
    LLMReviewTrace,
    MerchantCandidateTrace,
    MerchantResolutionInputs,
    MerchantResolutionOutputs,
    MerchantResolutionSummary,
    MetadataEvaluatorInputs,
    MetadataEvaluatorOutputs,
    OpenAIEmbedInputs,
    OpenAIEmbedOutputs,
    PlaceIdFinderInputs,
    PlaceIdFinderOutputs,
    ReceiptBoundary,
    ReceiptIdentifier,
    ReceiptMetadataSummary,
    ReceiptProcessingInputs,
    ReceiptProcessingOutputs,
    ReceiptWithAnomalies,
    ReceiptWithDecisions,
    ReviewDecisionTrace,
    RuntimeInfo,
    S3DownloadSnapshotInputs,
    S3DownloadSnapshotOutputs,
    SimilarityMatchTrace,
    SimilarWordTrace,
    StepTimingSummary,
    ToolCallTrace,
    TraceMetadata,
    ValidationAgentInputs,
    ValidationAgentOutputs,
    ValidationResultTrace,
    ValidationStatus,
    VerificationEvidenceTrace,
    VerificationStepTrace,
    VisualizationReceipt,
    VizCacheReceipt,
    WordWithLabel,
)

# Parsers (always available)
# Label validation helpers (receipt-label-validation project)
from receipt_langsmith.parsers import (
    LabelValidationTraceIndex,
    TraceTreeBuilder,
    build_label_validation_summary,
    build_merchant_resolution_summary,
    count_label_validation_decisions,
    get_merchant_resolution_result,
    get_step_timings,
    parse_extra,
    parse_json,
)

# Legacy exports (backward compatibility with existing code)
from receipt_langsmith.queries import (
    find_receipts_with_anomalies,
    find_receipts_with_llm_decisions,
    get_child_traces,
    get_langsmith_client,
    query_recent_receipt_traces,
)

if TYPE_CHECKING:
    from receipt_langsmith.parsers.parquet import read_traces_from_parquet
    from receipt_langsmith.spark.label_validation_processor import (
        LabelValidationSparkProcessor,
    )
    from receipt_langsmith.spark.processor import LangSmithSparkProcessor
    from receipt_langsmith.spark.schemas import (
        LABEL_VALIDATION_DECISION_SCHEMA,
        LABEL_VALIDATION_RECEIPT_SCHEMA,
        LABEL_VALIDATION_STEP_TIMING_SCHEMA,
        LANGSMITH_PARQUET_SCHEMA,
        MERCHANT_RESOLUTION_SCHEMA,
    )


# Lazy imports for optional dependencies
def __getattr__(name: str):
    """Lazy import for optional dependencies (PySpark)."""
    resolved = resolve_parquet_reader(name)
    if resolved is not None:
        return resolved

    resolved = resolve_pyspark_attr(name)
    if resolved is not None:
        return resolved

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Public re-export list is intentionally duplicated to keep a stable API.
# pylint: disable=duplicate-code
__all__ = [
    "__version__",
    # Base entities
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
    # Other agents
    "ToolCallTrace",
    "PlaceIdFinderInputs",
    "PlaceIdFinderOutputs",
    "AgenticValidationInputs",
    "AgenticValidationOutputs",
    "ReceiptMetadataSummary",
    "HarmonizedField",
    "HarmonizerInputs",
    "HarmonizerOutputs",
    "ReceiptBoundary",
    "GroupingProposal",
    "GroupingInputs",
    "GroupingOutputs",
    # Visualization entities
    "DecisionCounts",
    "EvaluatorResult",
    "GeometricResult",
    "ReceiptIdentifier",
    "EvaluatorTiming",
    "ReceiptWithDecisions",
    "ReceiptWithAnomalies",
    "VisualizationReceipt",
    "BoundingBox",
    "WordWithLabel",
    "VizCacheReceipt",
    # Client
    "LangSmithClient",
    "BulkExportManager",
    "ExportJob",
    "ExportStatus",
    "BulkExportDestination",
    "BulkExportRequest",
    "BulkExportResponse",
    "Project",
    # Parsers
    "TraceTreeBuilder",
    "parse_json",
    "parse_extra",
    # Legacy API-based queries (backward compatibility)
    "get_langsmith_client",
    "query_recent_receipt_traces",
    "get_child_traces",
    "find_receipts_with_anomalies",
    "find_receipts_with_llm_decisions",
    # Legacy Parquet-based queries (backward compatibility)
    "read_traces_from_parquet",
    # Label validation entities (receipt-label-validation project)
    "ChromaDBUpsertOutputs",
    "LabelValidationInputs",
    "LabelValidationOutputs",
    "LabelValidationSummary",
    "LLMBatchValidationInputs",
    "LLMBatchValidationOutputs",
    "MerchantResolutionInputs",
    "MerchantResolutionOutputs",
    "MerchantResolutionSummary",
    "OpenAIEmbedInputs",
    "OpenAIEmbedOutputs",
    "ReceiptProcessingInputs",
    "ReceiptProcessingOutputs",
    "S3DownloadSnapshotInputs",
    "S3DownloadSnapshotOutputs",
    "SimilarityMatchTrace",
    "SimilarWordTrace",
    "StepTimingSummary",
    # Label validation trace helpers
    "LabelValidationTraceIndex",
    "build_label_validation_summary",
    "build_merchant_resolution_summary",
    "count_label_validation_decisions",
    "get_merchant_resolution_result",
    "get_step_timings",
    # PySpark processors (lazy imports)
    "LangSmithSparkProcessor",
    "LabelValidationSparkProcessor",
    # PySpark schemas (lazy imports)
    "LANGSMITH_PARQUET_SCHEMA",
    "LABEL_VALIDATION_RECEIPT_SCHEMA",
    "LABEL_VALIDATION_STEP_TIMING_SCHEMA",
    "LABEL_VALIDATION_DECISION_SCHEMA",
    "MERCHANT_RESOLUTION_SCHEMA",
]
# pylint: enable=duplicate-code
