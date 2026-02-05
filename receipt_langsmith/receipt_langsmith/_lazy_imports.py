"""Shared lazy import helpers for optional dependencies."""

# Lazy imports are intentional to avoid importing optional dependencies at
# module import time.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

from typing import Any

PYSPARK_SCHEMA_NAMES = (
    "LANGSMITH_PARQUET_SCHEMA",
    "LABEL_VALIDATION_RECEIPT_SCHEMA",
    "LABEL_VALIDATION_STEP_TIMING_SCHEMA",
    "LABEL_VALIDATION_DECISION_SCHEMA",
    "MERCHANT_RESOLUTION_SCHEMA",
)


def resolve_parquet_reader(name: str) -> Any | None:
    """Resolve optional Parquet reader exports."""
    if name != "read_traces_from_parquet":
        return None
    try:
        from receipt_langsmith.parsers.parquet import read_traces_from_parquet

        return read_traces_from_parquet
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "PyArrow not available. Install with: "
            "pip install receipt-langsmith[pyspark]"
        ) from exc


def resolve_pyspark_attr(name: str) -> Any | None:
    """Resolve optional PySpark exports."""
    if name == "LangSmithSparkProcessor":
        try:
            from receipt_langsmith.spark.processor import (
                LangSmithSparkProcessor,
            )

            return LangSmithSparkProcessor
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "PySpark not available. Install with: "
                "pip install receipt-langsmith[pyspark]"
            ) from exc

    if name == "LabelValidationSparkProcessor":
        try:
            from receipt_langsmith.spark.label_validation_processor import (
                LabelValidationSparkProcessor,
            )

            return LabelValidationSparkProcessor
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "PySpark not available. Install with: "
                "pip install receipt-langsmith[pyspark]"
            ) from exc

    if name in PYSPARK_SCHEMA_NAMES:
        try:
            from receipt_langsmith.spark import schemas

            return getattr(schemas, name)
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "PySpark not available. Install with: "
                "pip install receipt-langsmith[pyspark]"
            ) from exc

    return None

# pylint: enable=import-outside-toplevel
