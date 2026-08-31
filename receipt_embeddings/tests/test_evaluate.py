"""evaluate.py is pure given fixtures; chroma replay self-parity is 1.0."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scripts.similarity_harness.backends import DynamoVectorClient
from scripts.similarity_harness.capture_golden import capture_synthetic
from scripts.similarity_harness.evaluate import (
    build_backend,
    evaluate,
    main,
)


def _write_fixtures(tmp_path: Path, limit: int = 6) -> Path:
    golden, corpus = capture_synthetic(seed=0, limit=limit)
    (tmp_path / "golden.json").write_text(json.dumps(golden))
    (tmp_path / "corpus.json").write_text(json.dumps(corpus))
    return tmp_path


def _parity_ok(scorecard: dict) -> None:
    metrics = scorecard["metrics"]
    assert metrics["neighbor_recall_at_k"]["macro"] == pytest.approx(1.0)
    assert metrics["neighbor_recall_at_k"]["recall@10"] == pytest.approx(1.0)
    assert metrics["merchant_agreement_pct"] == pytest.approx(100.0)
    assert metrics["tier_agreement_pct"] == pytest.approx(100.0)
    assert metrics["section_vote_agreement_pct"] == pytest.approx(100.0)
    assert metrics["tier_distribution_pp_gap"] == pytest.approx(0.0)
    assert metrics["est_usd_per_query"] == pytest.approx(0.0)
    assert scorecard["all_gates_pass"] is True


def test_fake_backend_self_parity(tmp_path: Path) -> None:
    fixtures = _write_fixtures(tmp_path)
    golden = json.loads((fixtures / "golden.json").read_text())
    client, name = build_backend("fake", fixture_dir=fixtures, golden=golden)
    assert name == "fake"
    scorecard = evaluate(client, golden, backend=name)
    _parity_ok(scorecard)
    required = {
        "neighbor_recall_at_k",
        "merchant_agreement_pct",
        "tier_distribution",
        "latency_ms",
        "est_usd_per_query",
    }
    assert required <= set(scorecard["metrics"])
    assert "p50" in scorecard["metrics"]["latency_ms"]
    assert "p95" in scorecard["metrics"]["latency_ms"]


def test_chroma_replay_self_parity(tmp_path: Path) -> None:
    fixtures = _write_fixtures(tmp_path)
    golden = json.loads((fixtures / "golden.json").read_text())
    client, name = build_backend("chroma", fixture_dir=fixtures, golden=golden)
    assert name == "chroma_replay"
    scorecard = evaluate(client, golden, backend=name)
    _parity_ok(scorecard)


def test_evaluate_is_pure_given_fixtures(tmp_path: Path) -> None:
    fixtures = _write_fixtures(tmp_path, limit=4)
    golden = json.loads((fixtures / "golden.json").read_text())
    client, name = build_backend("fake", fixture_dir=fixtures, golden=golden)
    first = evaluate(client, golden, backend=name)
    second = evaluate(client, golden, backend=name)
    skip = {"latency_ms"}
    left = {k: v for k, v in first["metrics"].items() if k not in skip}
    right = {k: v for k, v in second["metrics"].items() if k not in skip}
    assert left == right


def test_cli_chroma_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    fixtures = _write_fixtures(tmp_path, limit=3)
    out = tmp_path / "scorecard.json"
    code = main(
        [
            "--backend",
            "chroma",
            "--fixtures",
            str(fixtures),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    scorecard = json.loads(out.read_text())
    assert scorecard["backend"] == "chroma_replay"
    _parity_ok(scorecard)
    printed = json.loads(capsys.readouterr().out)
    assert printed["n_receipts"] == 3


def test_dynamo_client_never_creates_indexes() -> None:
    source = inspect.getsource(DynamoVectorClient)
    for banned in (
        "update_table(",
        "create_table(",
        ".update_table",
        ".create_table",
        "VectorIndexes",
        "VectorIndexUpdates",
    ):
        assert banned not in source


def test_dynamo_search_is_read_only() -> None:
    boto = MagicMock()
    boto.search_vectors.return_value = {
        "Items": [
            {
                "key": "IMAGE#x#RECEIPT#00001#LINE#00001",
                "Distance": 0.1,
                "Metadata": {"merchant_name": "A"},
            }
        ],
        "ConsumedCapacity": {"ReadRequestUnits": 2.0},
    }
    client = DynamoVectorClient(
        table_name="ReceiptsTable-dc5be22", client=boto
    )
    hits = client.search([1.0, 0.0], "lines", top_k=5)
    assert hits[0].key.endswith("LINE#00001")
    assert client.last_request_units == pytest.approx(2.0)
    boto.search_vectors.assert_called_once()
    boto.update_table.assert_not_called()
    boto.create_table.assert_not_called()
    kwargs = boto.search_vectors.call_args.kwargs
    assert kwargs["TableName"] == "ReceiptsTable-dc5be22"
    assert "VectorIndexes" not in kwargs
