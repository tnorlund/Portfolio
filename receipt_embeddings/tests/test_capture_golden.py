"""Offline capture_golden.py: synthetic fixtures, no live Chroma/AWS."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.similarity_harness.capture_golden import (
    capture_synthetic,
    main,
)
from scripts.similarity_harness.common import golden_receipt_set


def _strip_captured_at(payload: dict) -> dict:
    clone = json.loads(json.dumps(payload))
    clone.get("meta", {}).pop("captured_at", None)
    return clone


def test_golden_receipt_set_covers_forty_plus() -> None:
    receipts = golden_receipt_set()
    assert len(receipts) >= 40
    sources = {row["source_set"] for row in receipts}
    assert "line_items_golden" in sources
    assert "may26" in sources


def test_synthetic_capture_has_three_query_families() -> None:
    golden, corpus = capture_synthetic(limit=5)
    assert golden["meta"]["n_receipts"] == 5
    assert corpus["n_items"] > 0
    for receipt in golden["receipts"]:
        merchant = receipt["merchant_resolution"]
        assert merchant["neighbors"]
        assert merchant["top_k"] == 20
        assert "tier" in merchant
        assert "decision" in merchant
        assert len(receipt["word_queries"]) == 2
        for word in receipt["word_queries"]:
            assert word["top_k"] == 30
            assert word["neighbors"]
            assert "distance" in word["neighbors"][0]
            assert "key" in word["neighbors"][0]
        section = receipt["section_verifier"]
        assert set(section["votes"]) == {"AGREED", "DISAGREED", "ABSTAINED"}
        assert len(section["row_queries"]) == 3
        for row in section["row_queries"]:
            assert row["vote"] in {"AGREED", "DISAGREED", "ABSTAINED"}
            assert row["top_k"] == 15


def test_two_synthetic_captures_are_identical_modulo_timestamp() -> None:
    first, corpus_a = capture_synthetic(seed=0, limit=8)
    second, corpus_b = capture_synthetic(seed=0, limit=8)
    assert _strip_captured_at(first) == _strip_captured_at(second)
    assert corpus_a == corpus_b


def test_cli_refuses_live_capture_without_chroma_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "CHROMA_CLOUD_API_KEY",
        "CHROMA_CLOUD_TENANT",
        "CHROMA_CLOUD_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_cli_synthetic_writes_fixtures(tmp_path: Path) -> None:
    code = main(
        ["--synthetic", "--out", str(tmp_path), "--limit", "4", "--seed", "0"]
    )
    assert code == 0
    golden = json.loads((tmp_path / "golden.json").read_text())
    corpus = json.loads((tmp_path / "corpus.json").read_text())
    assert golden["meta"]["n_receipts"] == 4
    assert golden["meta"]["source"] == "synthetic_offline"
    assert corpus["items"]
