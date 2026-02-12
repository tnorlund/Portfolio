"""Tests for label_validation_viz_cache_helpers behavior."""

from __future__ import annotations

from typing import Any


def test_build_viz_receipts_scans_until_max_buildable(
    monkeypatch,
) -> None:
    """Do not stop at first N roots; stop after N buildable receipts."""
    import receipt_langsmith.spark.label_validation_viz_cache_helpers as helpers

    root_traces = [
        {"trace_id": "trace-1"},
        {"trace_id": "trace-2"},
        {"trace_id": "trace-3"},
    ]
    called_trace_ids: list[str] = []

    monkeypatch.setattr(
        helpers,
        "extract_receipt_traces",
        lambda _df: root_traces,
    )
    monkeypatch.setattr(
        helpers,
        "extract_validation_traces",
        lambda _df, _trace_ids: {},
    )

    def fake_build_viz_receipt(
        root_trace: dict[str, Any],
        validation_traces: list[dict[str, Any]],
        receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    ) -> dict[str, Any] | None:
        del validation_traces, receipt_lookup
        called_trace_ids.append(str(root_trace["trace_id"]))
        if root_trace["trace_id"] == "trace-3":
            return {"trace_id": "trace-3"}
        return None

    monkeypatch.setattr(helpers, "build_viz_receipt", fake_build_viz_receipt)

    receipts = helpers._build_viz_receipts(
        df=object(),
        receipt_lookup={},
        max_receipts=1,
    )

    assert receipts == [{"trace_id": "trace-3"}]
    assert called_trace_ids == ["trace-1", "trace-2", "trace-3"]
