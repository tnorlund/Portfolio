"""Unit tests for dev API Lambda profile reconstruction."""

import base64
import gzip
import json

from scripts import capture_dev_api_profiles as capture


def _event(profile_id, sequence, total, data, timestamp=1):
    message = {
        "profile_id": profile_id,
        "sequence": sequence,
        "total": total,
        "data": data,
    }
    return {
        "message": capture.PROFILE_MARKER + json.dumps(message),
        "timestamp": timestamp,
        "logStreamName": "stream",
    }


def test_complete_profiles_reassembles_ordered_chunks() -> None:
    payload = base64.b64encode(gzip.compress(b"profile-data")).decode("ascii")
    midpoint = len(payload) // 2
    events = [
        _event("profile", 1, 2, payload[midpoint:], timestamp=2),
        _event("profile", 0, 2, payload[:midpoint]),
    ]

    profiles = capture._complete_profiles(events)

    assert len(profiles) == 1
    assert profiles[0]["encoded"] == payload
    assert profiles[0]["timestamp"] == 2


def test_complete_profiles_ignores_incomplete_payloads() -> None:
    assert capture._complete_profiles([_event("profile", 0, 2, "a")]) == []


def test_extract_import_trace_removes_lambda_prefix() -> None:
    messages = [
        "2026-01-01 import time: self [us] | cumulative | imported package",
        "import time:       100 |        100 | json",
        "START RequestId: request",
    ]

    assert capture.extract_import_trace(messages) == (
        "import time: self [us] | cumulative | imported package\n"
        "import time:       100 |        100 | json\n"
    )


def test_extract_report_parses_lambda_report() -> None:
    messages = [
        "REPORT RequestId: request-1\tDuration: 123.45 ms\t"
        "Billed Duration: 124 ms\tMemory Size: 1024 MB\t"
        "Max Memory Used: 99 MB\tInit Duration: 210.5 ms"
    ]

    assert capture.extract_report(messages, "request-1") == {
        "duration_ms": 123.45,
        "memory_size_mb": 1024,
        "max_memory_mb": 99,
        "init_duration_ms": 210.5,
    }


def test_percentile_uses_nearest_rank() -> None:
    assert capture._percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 2.0
    assert capture._percentile([4.0, 1.0, 3.0, 2.0], 0.99) == 4.0
    assert capture._percentile([], 0.99) is None
