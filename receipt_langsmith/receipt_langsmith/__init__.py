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
   - Typed Parquet reader for bulk exports
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
        ParquetReader,
        TraceTreeBuilder,
    )

    # API usage
    client = LangSmithClient()
    projects = client.list_projects()

    # Parquet parsing
    reader = ParquetReader(bucket="my-export-bucket")
    traces = reader.read_all_traces()

    # Build trace trees
    builder = TraceTreeBuilder(traces)
    roots = builder.get_root_runs(name_filter="ReceiptEvaluation")
    ```
"""

__version__ = "0.2.0"

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
from receipt_langsmith.entities import (  # Base; Validation agent; Label evaluator; Other agents
    AgenticValidationInputs,
    AgenticValidationOutputs,
    BoundingBox,
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
    LangSmithRun,
    LangSmithRunRaw,
    LLMReviewOutputs,
    LLMReviewTrace,
    MerchantCandidateTrace,
    MetadataEvaluatorInputs,
    MetadataEvaluatorOutputs,
    PlaceIdFinderInputs,
    PlaceIdFinderOutputs,
    ReceiptBoundary,
    ReceiptIdentifier,
    ReceiptMetadataSummary,
    ReceiptWithAnomalies,
    ReceiptWithDecisions,
    ReviewDecisionTrace,
    RuntimeInfo,
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
from receipt_langsmith.parquet_reader import (
    find_receipts_with_anomalies_from_parquet,
    find_receipts_with_decisions_from_parquet,
    find_visualization_receipts_from_parquet,
    read_traces_from_parquet,
)

# Parsers (always available)
from receipt_langsmith.parsers import (
    ParquetReader,
    TraceIndex,
    TraceTreeBuilder,
    build_evaluator_result,
    build_geometric_from_trace,
    build_geometric_result,
    build_receipt_identifier,
    count_decisions,
    extract_metadata,
    get_decisions_from_trace,
    get_duration_seconds,
    get_relative_timing,
    is_all_needs_review,
    load_s3_result,
    parse_datetime,
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


# Lazy imports for optional dependencies
def __getattr__(name: str):
    """Lazy import for optional dependencies (PySpark)."""
    # PySpark dependencies
    if name == "LangSmithSparkProcessor":
        try:
            from receipt_langsmith.spark.processor import (
                LangSmithSparkProcessor,
            )

            return LangSmithSparkProcessor
        except ImportError as e:
            raise ImportError(
                "PySpark not available. Install with: "
                "pip install receipt-langsmith[pyspark]"
            ) from e

    if name == "LANGSMITH_PARQUET_SCHEMA":
        try:
            from receipt_langsmith.spark.schemas import (
                LANGSMITH_PARQUET_SCHEMA,
            )

            return LANGSMITH_PARQUET_SCHEMA
        except ImportError as e:
            raise ImportError(
                "PySpark not available. Install with: "
                "pip install receipt-langsmith[pyspark]"
            ) from e

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "ParquetReader",
    "TraceTreeBuilder",
    "TraceIndex",
    "parse_json",
    "parse_extra",
    # Trace helpers
    "extract_metadata",
    "build_receipt_identifier",
    "count_decisions",
    "is_all_needs_review",
    "parse_datetime",
    "get_duration_seconds",
    "get_relative_timing",
    "load_s3_result",
    "build_evaluator_result",
    "build_geometric_result",
    "build_geometric_from_trace",
    "get_decisions_from_trace",
    # Legacy API-based queries (backward compatibility)
    "get_langsmith_client",
    "query_recent_receipt_traces",
    "get_child_traces",
    "find_receipts_with_anomalies",
    "find_receipts_with_llm_decisions",
    # Legacy Parquet-based queries (backward compatibility)
    "read_traces_from_parquet",
    "find_receipts_with_decisions_from_parquet",
    "find_receipts_with_anomalies_from_parquet",
    "find_visualization_receipts_from_parquet",
]
