#!/usr/bin/env python3
"""Capture dev Lambda cProfile and import-time artifacts from CloudWatch.

The target functions must have the request-gated profiler enabled by
``RouteLambdaDefinition.enable_dev_profiling``. This script invokes each route
with ``__profile=1``, reconstructs the compressed pstats chunks, and extracts
the import-time trace from the same Lambda log stream.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import math
import re
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import boto3

PROFILE_MARKER = "LAMBDA_PROFILE_CHUNK "
REPORT_PATTERN = re.compile(
    r"REPORT RequestId: (?P<request_id>\S+).*?"
    r"Duration: (?P<duration>[\d.]+) ms.*?"
    r"Memory Size: (?P<memory_size>\d+) MB.*?"
    r"Max Memory Used: (?P<max_memory>\d+) MB"
)
INIT_DURATION_PATTERN = re.compile(r"Init Duration: (?P<value>[\d.]+) ms")


@dataclass(frozen=True)
class RouteSpec:
    logical_name: str
    path: str


@dataclass(frozen=True)
class InvocationTiming:
    status: int
    ttfb_ms: float
    total_ms: float
    response_bytes: int


ROUTES = {
    "images": RouteSpec(
        logical_name="api_images_GET_lambda",
        path="/images?limit=1",
    ),
    "receipts": RouteSpec(
        logical_name="api_receipts_GET_lambda",
        path="/receipts?limit=1",
    ),
}


def discover_function(lambda_client, logical_name: str) -> str:
    """Resolve a Pulumi auto-named function from its stable logical prefix."""
    matches = []
    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for function in page.get("Functions", []):
            name = function["FunctionName"]
            if not (
                name == logical_name or name.startswith(f"{logical_name}-")
            ):
                continue
            tags = lambda_client.list_tags(
                Resource=function["FunctionArn"]
            ).get("Tags", {})
            if tags.get("environment") == "dev":
                matches.append(name)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one function for {logical_name!r}, found {matches}"
        )
    return matches[0]


def invoke_profile(base_url: str, path: str) -> InvocationTiming:
    """Invoke a route and measure response-header TTFB and total time."""
    separator = "&" if "?" in path else "?"
    url = f"{base_url.rstrip('/')}{path}{separator}__profile=1"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        headers_received = time.perf_counter()
        payload = response.read()
        completed = time.perf_counter()
        status = response.status
    return InvocationTiming(
        status=status,
        ttfb_ms=(headers_received - started) * 1000,
        total_ms=(completed - started) * 1000,
        response_bytes=len(payload),
    )


def _parse_chunk(message: str) -> dict[str, Any] | None:
    marker_index = message.find(PROFILE_MARKER)
    if marker_index < 0:
        return None
    payload = message[marker_index + len(PROFILE_MARKER) :].strip()
    return json.loads(payload)


def _complete_profiles(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        chunk = _parse_chunk(event["message"])
        if chunk is None:
            continue
        profile = grouped.setdefault(
            chunk["profile_id"],
            {
                "chunks": {},
                "total": chunk["total"],
                "timestamp": event["timestamp"],
                "log_stream": event["logStreamName"],
            },
        )
        profile["chunks"][chunk["sequence"]] = chunk["data"]
        profile["timestamp"] = max(profile["timestamp"], event["timestamp"])

    complete = []
    for profile_id, profile in grouped.items():
        if len(profile["chunks"]) != profile["total"]:
            continue
        encoded = "".join(
            profile["chunks"][index] for index in range(profile["total"])
        )
        complete.append(
            {
                "profile_id": profile_id,
                "encoded": encoded,
                "timestamp": profile["timestamp"],
                "log_stream": profile["log_stream"],
            }
        )
    return complete


def wait_for_profile(
    logs_client,
    log_group: str,
    started_ms: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Poll CloudWatch until a complete chunked profile is visible."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        events = []
        kwargs = {
            "logGroupName": log_group,
            "startTime": started_ms,
            "filterPattern": f'"{PROFILE_MARKER.strip()}"',
        }
        while True:
            response = logs_client.filter_log_events(**kwargs)
            events.extend(response.get("events", []))
            token = response.get("nextToken")
            if not token:
                break
            kwargs["nextToken"] = token
        complete = _complete_profiles(events)
        if complete:
            return max(complete, key=lambda profile: profile["timestamp"])
        time.sleep(2)
    raise TimeoutError(f"profile not found in {log_group}")


def read_log_stream(logs_client, log_group: str, log_stream: str) -> list[str]:
    """Read one Lambda log stream from the beginning."""
    messages = []
    token = None
    while True:
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": log_stream,
            "startFromHead": True,
        }
        if token is not None:
            kwargs["nextToken"] = token
        response = logs_client.get_log_events(**kwargs)
        messages.extend(
            event["message"] for event in response.get("events", [])
        )
        next_token = response.get("nextForwardToken")
        if next_token == token:
            break
        token = next_token
    return messages


def extract_import_trace(messages: list[str]) -> str:
    lines = []
    for message in messages:
        for line in message.splitlines():
            index = line.find("import time:")
            if index >= 0:
                lines.append(line[index:])
    return "\n".join(lines) + ("\n" if lines else "")


def extract_report(messages: list[str], request_id: str) -> dict[str, Any]:
    for message in reversed(messages):
        match = REPORT_PATTERN.search(message)
        if match and match.group("request_id") == request_id:
            values = match.groupdict()
            init_match = INIT_DURATION_PATTERN.search(message)
            return {
                "duration_ms": float(values["duration"]),
                "memory_size_mb": int(values["memory_size"]),
                "max_memory_mb": int(values["max_memory"]),
                "init_duration_ms": (
                    float(init_match.group("value"))
                    if init_match is not None
                    else None
                ),
            }
    return {}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def collect_baseline(
    logs_client,
    log_group: str,
    days: int,
) -> dict[str, Any]:
    """Summarize recent Lambda REPORT lines for one function."""
    started_ms = int((time.time() - days * 86_400) * 1000)
    events = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": started_ms,
        "filterPattern": '"REPORT RequestId"',
    }
    while True:
        response = logs_client.filter_log_events(**kwargs)
        events.extend(response.get("events", []))
        token = response.get("nextToken")
        if not token:
            break
        kwargs["nextToken"] = token

    reports = []
    for event in events:
        match = REPORT_PATTERN.search(event["message"])
        if match is None:
            continue
        values = match.groupdict()
        init_match = INIT_DURATION_PATTERN.search(event["message"])
        reports.append(
            {
                "duration_ms": float(values["duration"]),
                "memory_size_mb": int(values["memory_size"]),
                "max_memory_mb": int(values["max_memory"]),
                "init_duration_ms": (
                    float(init_match.group("value"))
                    if init_match is not None
                    else None
                ),
            }
        )
    durations = [report["duration_ms"] for report in reports]
    init_durations = [
        report["init_duration_ms"]
        for report in reports
        if report["init_duration_ms"] is not None
    ]
    max_memory = [report["max_memory_mb"] for report in reports]
    return {
        "days": days,
        "invocations": len(reports),
        "cold_starts": len(init_durations),
        "cold_start_rate": (
            len(init_durations) / len(reports) if reports else None
        ),
        "duration_p50_ms": _percentile(durations, 0.50),
        "duration_p99_ms": _percentile(durations, 0.99),
        "init_p50_ms": _percentile(init_durations, 0.50),
        "init_p99_ms": _percentile(init_durations, 0.99),
        "max_memory_p99_mb": _percentile(max_memory, 0.99),
        "memory_size_mb": (reports[-1]["memory_size_mb"] if reports else None),
    }


def capture_route(
    route: str,
    spec: RouteSpec,
    base_url: str,
    output_dir: Path,
    lambda_client,
    logs_client,
    timeout_seconds: int,
    baseline_days: int,
) -> dict[str, Any]:
    function_name = discover_function(lambda_client, spec.logical_name)
    log_group = f"/aws/lambda/{function_name}"
    baseline = collect_baseline(logs_client, log_group, baseline_days)
    started_ms = int(time.time() * 1000) - 1000
    timing = invoke_profile(base_url, spec.path)
    profile = wait_for_profile(
        logs_client,
        log_group,
        started_ms,
        timeout_seconds,
    )

    raw_profile = gzip.decompress(base64.b64decode(profile["encoded"]))
    profile_path = output_dir / f"{route}.prof"
    profile_path.write_bytes(raw_profile)

    # The REPORT line can arrive just after the profile chunks. Briefly poll
    # the one stream so metadata normally contains the same invocation report.
    messages = []
    for _ in range(10):
        messages = read_log_stream(
            logs_client, log_group, profile["log_stream"]
        )
        if extract_report(messages, profile["profile_id"]):
            break
        time.sleep(1)

    import_trace = extract_import_trace(messages)
    import_path = output_dir / f"{route}.importtime"
    import_path.write_text(import_trace, encoding="utf-8")

    metadata = {
        "route": route,
        "function_name": function_name,
        "log_group": log_group,
        "log_stream": profile["log_stream"],
        "profile_id": profile["profile_id"],
        "baseline": baseline,
        "invocation": asdict(timing),
        "report": extract_report(messages, profile["profile_id"]),
        "artifacts": {
            "pstats": profile_path.name,
            "importtime": import_path.name,
        },
    }
    (output_dir / f"{route}.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--route",
        action="append",
        choices=sorted(ROUTES),
        required=True,
    )
    parser.add_argument(
        "--base-url",
        default="https://dev-api.tylernorlund.com",
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--baseline-days", type=int, default=7)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lambda_client = boto3.client("lambda", region_name=args.region)
    logs_client = boto3.client("logs", region_name=args.region)
    results = []
    for route in args.route:
        results.append(
            capture_route(
                route,
                ROUTES[route],
                args.base_url,
                args.output_dir,
                lambda_client,
                logs_client,
                args.timeout_seconds,
                args.baseline_days,
            )
        )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
