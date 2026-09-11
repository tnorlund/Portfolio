"""Native receipt spans in S3, with optional hosted LangSmith debugging.

Each completed root writes its completed spans to one NDJSON object.
The context propagates through LangSmith's ContextThreadPoolExecutor in Lambda.
No API key or LangSmith service is needed for the native record.
"""

import functools
import inspect
import json
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, ParamSpec, TypeVar
from uuid import uuid4

import boto3
from langsmith import traceable as hosted_traceable

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class _Trace:
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    rows: list[dict[str, Any]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


_trace: ContextVar[_Trace | None] = ContextVar("receipt_trace", default=None)
_parent: ContextVar[str | None] = ContextVar("receipt_parent", default=None)


def hosted_enabled() -> bool:
    """A retained credential alone must never switch tracing on."""
    flag = os.environ.get(
        "LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2", "false")
    )
    return flag.lower() == "true" and bool(
        os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
    )


def capture_enabled() -> bool:
    """Return whether native or hosted capture is configured."""
    return bool(os.environ.get("RECEIPT_TRACE_BUCKET")) or hosted_enabled()


def _json(value: object) -> str:
    return json.dumps(
        value,
        default=lambda obj: (
            obj.model_dump() if hasattr(obj, "model_dump") else str(obj)
        ),
    )


def current_trace_context() -> dict[str, str] | None:
    """Small JSON-safe carrier for deferred work on the same receipt trace."""
    current = _trace.get()
    parent = _parent.get()
    if current is None or parent is None:
        return None
    return {"trace_id": current.trace_id, "parent_run_id": parent}


def _publish_trace(
    trace: _Trace,
    bucket: str,
    started: datetime,
    run_id: str,
    status: str,
) -> None:
    """Publish one complete bundle, isolated from other Lambda publishers."""
    body = "\n".join(
        _json({**span, "capture_status": status}) for span in trace.rows
    )
    key = (
        f"native-traces/date={started.date()}/"
        f"{trace.trace_id}-{run_id}.ndjson"
    )
    client: S3Client = boto3.client("s3")
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode(),
        ContentType="application/x-ndjson",
    )


def traceable(
    **options: Any,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Capture synchronous spans while preserving the wrapped signature."""

    native_parent = options.pop("native_parent", None)

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            call = hosted_traceable(**options)(fn) if hosted_enabled() else fn
            bucket = os.environ.get("RECEIPT_TRACE_BUCKET")
            if not bucket:
                return call(*args, **kwargs)
            current = _trace.get()
            is_root = current is None
            parent_id = _parent.get()
            if current is None and native_parent:
                current = _Trace(trace_id=native_parent["trace_id"])
                parent_id = native_parent["parent_run_id"]
            current = current or _Trace()
            run_id = str(uuid4())
            started = datetime.now(timezone.utc)
            inputs = {
                key: value
                for key, value in signature.bind(
                    *args, **kwargs
                ).arguments.items()
                if key != "self"
            }
            row = {
                "schema_version": 1,
                "id": run_id,
                "trace_id": current.trace_id,
                "parent_run_id": parent_id,
                "is_root": parent_id is None,
                "name": options.get("name", fn.__name__),
                "run_type": options.get("run_type", "chain"),
                "start_time": started.isoformat(),
                "inputs": _json(inputs),
                "extra": _json({"metadata": options.get("metadata", {})}),
                "project_name": options.get("project_name", ""),
                "status": "success",
                "outputs": None,
            }
            trace_token = _trace.set(current)
            parent_token = _parent.set(run_id)
            try:
                result = call(*args, **kwargs)
                row["outputs"] = _json(result)
                if isinstance(result, dict) and result.get("success") is False:
                    row["status"] = "error"
                    row["error"] = str(
                        result.get("error", "Unsuccessful result")
                    )
                return result
            except BaseException as error:
                row["status"] = "error"
                row["error"] = str(error)
                raise
            finally:
                row["end_time"] = datetime.now(timezone.utc).isoformat()
                with current.lock:
                    current.rows.append(row)
                _parent.reset(parent_token)
                _trace.reset(trace_token)
                if is_root:
                    _publish_trace(
                        current, bucket, started, run_id, row["status"]
                    )

        return wrapped

    return decorate
