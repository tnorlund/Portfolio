"""PySpark processing for LangSmith analytics.

This module provides PySpark-based analytics for large-scale
LangSmith trace processing on EMR Serverless.

Includes processors for:
- LangSmithSparkProcessor: Analytics for label-evaluator-dev project
- LabelValidationSparkProcessor: Analytics for receipt-label-validation project

Note: This module requires the [pyspark] optional dependency.
Install with: pip install receipt-langsmith[pyspark]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from receipt_langsmith._lazy_imports import resolve_pyspark_attr

if TYPE_CHECKING:
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


def __getattr__(name: str):
    """Lazy import for PySpark dependencies.

    PySpark is an optional dependency. Imports are deferred to runtime so that
    the package can be imported without PySpark installed. This allows Lambda
    functions to use receipt_langsmith without installing PySpark.
    """
    resolved = resolve_pyspark_attr(name)
    if resolved is not None:
        return resolved

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Public re-export list is intentionally duplicated to keep a stable API.
# pylint: disable=duplicate-code
__all__ = [
    # Processors
    "LangSmithSparkProcessor",
    "LabelValidationSparkProcessor",
    # Schemas
    "LANGSMITH_PARQUET_SCHEMA",
    "LABEL_VALIDATION_RECEIPT_SCHEMA",
    "LABEL_VALIDATION_STEP_TIMING_SCHEMA",
    "LABEL_VALIDATION_DECISION_SCHEMA",
    "MERCHANT_RESOLUTION_SCHEMA",
]
# pylint: enable=duplicate-code
