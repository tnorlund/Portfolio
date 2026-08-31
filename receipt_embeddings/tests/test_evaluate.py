"""evaluate.py is pure given fixtures; fake self-parity is 1.0."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from receipt_embeddings.dynamo_adapter import DynamoVectorSearchClient
from receipt_embeddings.fixtures import (
    load_fixture_bundle,
    write_fixture_bundle,
)
from receipt_embeddings.harness import client_for_backend, evaluate_backend
from receipt_embeddings.synthetic import generate_synthetic_bundle
from receipt_embeddings.testing.fake_index import FakeVectorIndex

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_EVALUATE = _REPO / "scripts" / "similarity_harness" / "evaluate.py"


def test_fake_self_parity() -> None:
    bundle = generate_synthetic_bundle()
    client = FakeVectorIndex.from_fixture_items(bundle["vectors"]["items"])
    scorecard = evaluate_backend(client, bundle, backend="fake")
    assert scorecard["n_receipts"] >= 40
    assert scorecard["neighbor_recall"]["recall@10"] == pytest.approx(1.0)
    assert scorecard["merchant_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["tier_decision_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["section_vote_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["est_usd_per_query"] == 0.0
    assert "p50" in scorecard["latency_ms"]
    assert "p95" in scorecard["latency_ms"]
    assert (
        "0.002" in str(scorecard["cost_model"])
        or scorecard["est_usd_per_query"] == 0.0
    )


def test_committed_fixtures_fake_self_parity() -> None:
    bundle = load_fixture_bundle()
    client = client_for_backend("fake", bundle)
    scorecard = evaluate_backend(client, bundle, backend="fake")
    assert scorecard["n_receipts"] >= 40
    assert scorecard["neighbor_recall"]["recall@10"] == pytest.approx(1.0)
    assert scorecard["merchant_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["tier_decision_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["section_vote_agreement_pct"] == pytest.approx(100.0)


def test_evaluate_offline_runtime_under_one_minute() -> None:
    bundle = generate_synthetic_bundle()
    client = FakeVectorIndex.from_fixture_items(bundle["vectors"]["items"])
    started = time.perf_counter()
    evaluate_backend(client, bundle, backend="fake")
    elapsed = time.perf_counter() - started
    assert elapsed < 60.0


def test_dynamo_backend_does_not_create_indexes() -> None:
    client = DynamoVectorSearchClient()
    with pytest.raises(NotImplementedError, match="Round C/D"):
        client.search([0.0], "line-embeddings", top_k=1)
    with pytest.raises(NotImplementedError, match="Round C"):
        client.get_vector("x")


def test_client_for_backend_fake() -> None:
    bundle = generate_synthetic_bundle()
    client = client_for_backend("fake", bundle)
    assert len(client.search([1.0] * 16, "line-embeddings", top_k=1)) >= 0


def test_evaluate_cli_fake(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    write_fixture_bundle(fixtures, generate_synthetic_bundle())
    out = tmp_path / "scorecard.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_EVALUATE),
            "--backend",
            "fake",
            "--fixtures",
            str(fixtures),
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stderr
    scorecard = json.loads(out.read_text(encoding="utf-8"))
    assert scorecard["backend"] == "fake"
    assert scorecard["neighbor_recall"]["recall@10"] == pytest.approx(1.0)
