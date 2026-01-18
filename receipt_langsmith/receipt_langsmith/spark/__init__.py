"""PySpark processing for LangSmith analytics.

This module provides PySpark-based analytics for large-scale
LangSmith trace processing on EMR Serverless.

Note: This module requires the [pyspark] optional dependency.
Install with: pip install receipt-langsmith[pyspark]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from receipt_langsmith.spark.processor import LangSmithSparkProcessor
    from receipt_langsmith.spark.schemas import LANGSMITH_PARQUET_SCHEMA


def __getattr__(name: str):
    """Lazy import for PySpark dependencies.

    PySpark is an optional dependency. Imports are deferred to runtime so that
    the package can be imported without PySpark installed. This allows Lambda
    functions to use receipt_langsmith without installing PySpark.
    """
    # pylint: disable=import-outside-toplevel
    # Lazy imports are intentional - PySpark is optional and should only be
    # loaded when explicitly accessed, not when the package is imported.
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
    # pylint: enable=import-outside-toplevel

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LangSmithSparkProcessor",
    "LANGSMITH_PARQUET_SCHEMA",
]
