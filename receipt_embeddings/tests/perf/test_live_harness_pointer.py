"""Pin the live-latency harness instead of duplicating SearchVectors I/O."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.performance

_REPO = Path(__file__).resolve().parents[3]
_EVALUATE = _REPO / "scripts" / "similarity_harness" / "evaluate.py"


def test_live_wall_latency_lives_in_similarity_harness() -> None:
    source = _EVALUATE.read_text(encoding="utf-8")
    assert "--measure-wall-latency" in source
    assert '"dynamo"' in source or "'dynamo'" in source
    assert "measure_wall_latency" in source
