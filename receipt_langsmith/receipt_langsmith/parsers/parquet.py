"""Typed Parquet reader for LangSmith exports.

This module provides a typed reader for LangSmith bulk export Parquet files,
converting raw rows into LangSmithRun objects with parsed JSON fields.
"""

from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterator, Optional, Union

import boto3
import pyarrow.parquet as pq

from receipt_langsmith.entities.base import LangSmithRun
from receipt_langsmith.entities.parquet_schema import LangSmithRunRaw
from receipt_langsmith.parsers.json_fields import (
    parse_extra,
    parse_inputs,
    parse_outputs,
    parse_tags,
)

logger = logging.getLogger(__name__)


class ParquetReader:
    """Typed Parquet reader for LangSmith exports.

    This reader can read from:
    - S3 bucket with prefix
    - Local file paths

    Args:
        bucket: S3 bucket name (for S3 reads).
        prefix: S3 prefix for Parquet files (default: "traces/").
        local_path: Local directory or file path (for local reads).

    Example:
        ```python
        # Read from S3
        reader = ParquetReader(bucket="my-export-bucket", prefix="traces/")
        for run in reader.read_parsed_traces():
            print(run.name, run.duration_ms)

        # Read from local file
        reader = ParquetReader(local_path="/path/to/export.parquet")
        traces = list(reader.read_parsed_traces())
        ```
    """

    def __init__(
        self,
        bucket: Optional[str] = None,
        prefix: str = "traces/",
        local_path: Optional[Union[str, Path]] = None,
    ):
        self.bucket = bucket
        self.prefix = prefix
        self.local_path = Path(local_path) if local_path else None

        if not bucket and not local_path:
            raise ValueError("Either bucket or local_path must be provided")

        self._s3 = boto3.client("s3") if bucket else None

    def list_parquet_files(self) -> list[str]:
        """List all Parquet files in the source.

        Returns:
            List of file paths (S3 keys or local paths).
        """
        if self.local_path:
            return self._list_local_files()
        return self._list_s3_files()

    def _list_local_files(self) -> list[str]:
        """List Parquet files in local path."""
        if not self.local_path:
            return []

        if self.local_path.is_file():
            return [str(self.local_path)]

        files = []
        for path in self.local_path.rglob("*.parquet"):
            files.append(str(path))
        return sorted(files)

    def _list_s3_files(self) -> list[str]:
        """List Parquet files in S3 bucket."""
        if not self._s3 or not self.bucket:
            return []

        keys = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".parquet"):
                    keys.append(key)
        return sorted(keys)

    def read_raw_traces(
        self,
        key: Optional[str] = None,
    ) -> Iterator[LangSmithRunRaw]:
        """Read raw traces from Parquet file(s).

        Args:
            key: Specific file to read (S3 key or local path).
                If None, reads all files.

        Yields:
            LangSmithRunRaw objects (unparsed JSON fields).
        """
        keys = [key] if key else self.list_parquet_files()

        for k in keys:
            try:
                table = self._read_parquet_table(k)
                for row in table.to_pylist():
                    try:
                        yield LangSmithRunRaw(**row)
                    except Exception as e:
                        logger.debug("Failed to parse row: %s", e)
                        continue
            except Exception:
                logger.exception("Error reading %s", k)
                continue

    def read_parsed_traces(
        self,
        key: Optional[str] = None,
    ) -> Iterator[LangSmithRun]:
        """Read and parse traces with typed fields.

        Args:
            key: Specific file to read (S3 key or local path).
                If None, reads all files.

        Yields:
            LangSmithRun objects with parsed JSON fields.
        """
        for raw in self.read_raw_traces(key):
            try:
                yield self._parse_raw_trace(raw)
            except Exception as e:
                logger.debug("Failed to parse trace %s: %s", raw.id, e)
                continue

    def read_all_traces(self, key: Optional[str] = None) -> list[LangSmithRun]:
        """Read all traces into a list.

        Args:
            key: Specific file to read.

        Returns:
            List of all parsed traces.
        """
        return list(self.read_parsed_traces(key))

    def _read_parquet_table(self, key: str) -> "pq.ParquetFile":
        """Read a Parquet table from S3 or local path."""
        if self.local_path or not self._s3:
            # Local file
            logger.debug("Reading local Parquet file: %s", key)
            return pq.read_table(key)

        # S3 file
        logger.debug("Reading S3 Parquet file: s3://%s/%s", self.bucket, key)
        response = self._s3.get_object(Bucket=self.bucket, Key=key)
        return pq.read_table(BytesIO(response["Body"].read()))

    def _parse_raw_trace(self, raw: LangSmithRunRaw) -> LangSmithRun:
        """Parse a raw trace into a typed LangSmithRun.

        Args:
            raw: Raw trace from Parquet.

        Returns:
            Parsed LangSmithRun with typed fields.
        """
        # Parse JSON string fields
        inputs = parse_inputs(raw.inputs)
        outputs = parse_outputs(raw.outputs)
        metadata, runtime = parse_extra(raw.extra)
        tags = parse_tags(raw.tags)

        # Calculate duration
        duration_ms = 0
        if raw.start_time and raw.end_time:
            duration_ms = int(
                (raw.end_time - raw.start_time).total_seconds() * 1000
            )

        # Handle missing timestamps
        start_time = raw.start_time or datetime.min
        end_time = raw.end_time or datetime.min

        return LangSmithRun(
            id=raw.id,
            trace_id=raw.trace_id,
            parent_run_id=raw.parent_run_id,
            dotted_order=raw.dotted_order,
            is_root=raw.is_root or (raw.parent_run_id is None),
            name=raw.name,
            run_type=raw.run_type,
            status=raw.status,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            error=raw.error,
            total_tokens=raw.total_tokens or 0,
            prompt_tokens=raw.prompt_tokens or 0,
            completion_tokens=raw.completion_tokens or 0,
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
            runtime=runtime,
            tags=tags,
        )


# Convenience functions for backward compatibility


def read_traces_from_parquet(
    bucket: str,
    prefix: str = "traces/",
) -> list[dict]:
    """Read all traces from S3 Parquet export.

    This is a backward-compatible wrapper around ParquetReader.

    Args:
        bucket: S3 bucket name.
        prefix: S3 prefix for Parquet files.

    Returns:
        List of trace dictionaries.
    """
    reader = ParquetReader(bucket=bucket, prefix=prefix)
    traces = []

    for key in reader.list_parquet_files():
        try:
            table = reader._read_parquet_table(key)
            traces.extend(table.to_pylist())
        except Exception:
            logger.exception("Error reading %s", key)
            continue

    logger.info("Read %d traces from %s/%s", len(traces), bucket, prefix)
    return traces
