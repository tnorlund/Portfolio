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
    CurrencyEvaluatorInputs,
    CurrencyEvaluatorOutputs,
    DrillDownWordTrace,
    EvaluateLabelsInputs,
    EvaluateLabelsOutputs,
    EvaluationIssueTrace,
    EvidenceType,
    FinancialEvaluatorInputs,
    FinancialEvaluatorOutputs,
    FlaggedWordTrace,
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
    ReceiptMetadataSummary,
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
    TraceTreeBuilder,
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
    "find_receipts_with_decisions_from_parquet",
    "find_receipts_with_anomalies_from_parquet",
    "find_visualization_receipts_from_parquet",
]
