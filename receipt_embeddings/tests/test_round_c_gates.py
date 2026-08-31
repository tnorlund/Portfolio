"""Round C offline gates against the committed bootstrap fixture."""

from __future__ import annotations

from pathlib import Path

from scripts.similarity_harness.common import corpus_items, load_fixture
from scripts.similarity_harness.evaluate import evaluate_fixture

from receipt_embeddings.testing import FakeVectorIndex

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "similarity"
    / "golden.json"
)


def test_fake_backend_meets_offline_parity_gates() -> None:
    fixture = load_fixture(GOLDEN)
    scorecard = evaluate_fixture(
        fixture,
        FakeVectorIndex(corpus_items(fixture)),
        backend_name="fake",
    )
    metrics = scorecard["metrics"]
    assert metrics["neighbor_recall_at_k"]["overall"] >= 0.85
    assert metrics["merchant_agreement_percent"] >= 98.0
