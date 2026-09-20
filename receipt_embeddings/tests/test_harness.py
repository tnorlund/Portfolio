"""Offline tests for fixture capture, replay, and scorecard semantics."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripts.similarity_harness.common import (
    MERCHANT_FAMILY,
    MIN_RECEIPTS,
    QUERY_FAMILIES,
    SECTION_FAMILY,
    WORD_FAMILY,
    FixtureError,
    corpus_items,
    derive_section_vote,
    load_fixture,
    validate_fixture,
    write_fixture,
)
from scripts.similarity_harness.evaluate import (
    CapturedGoldenReplay,
    evaluate_fixture,
    failed_gates,
)
from scripts.similarity_harness.evaluate import main as evaluate_main

from receipt_embeddings import ScoredItem
from receipt_embeddings.testing import FakeVectorIndex

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "similarity" / "golden.json"
)
EVALUATE_SCRIPT = (
    REPOSITORY_ROOT / "scripts" / "similarity_harness" / "evaluate.py"
)


@pytest.mark.unit
def test_committed_fixture_covers_every_family_for_40_plus_receipts() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)

    assert len(fixture["receipts"]) >= MIN_RECEIPTS
    by_receipt: dict[str, set[str]] = {}
    for query in fixture["queries"]:
        by_receipt.setdefault(query["receipt_key"], set()).add(query["family"])
    assert all(
        families == set(QUERY_FAMILIES) for families in by_receipt.values()
    )
    assert all(
        query["top_k"] == 30 and len(query["expected"]["neighbors"]) == 30
        for query in fixture["queries"]
        if query["family"] == WORD_FAMILY
    )
    assert all(
        "merchant" in query["expected"]
        for query in fixture["queries"]
        if query["family"] == MERCHANT_FAMILY
    )
    assert all(
        "section" in query["expected"]
        for query in fixture["queries"]
        if query["family"] == SECTION_FAMILY
    )


@pytest.mark.unit
def test_dynamo_evaluation_rejects_prod_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")

    with pytest.raises(SystemExit, match="refusing to query DynamoDB"):
        evaluate_main(
            [
                "--backend",
                "dynamo",
                "--fixture",
                str(GOLDEN_FIXTURE),
                "--out",
                str(tmp_path / "unused.json"),
            ]
        )


@pytest.mark.unit
def test_golden_self_parity_is_one() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)
    scorecard = evaluate_fixture(
        fixture,
        CapturedGoldenReplay(fixture),
        backend_name="golden",
    )
    metrics = scorecard["metrics"]

    assert metrics["neighbor_recall_at_k"]["overall"] == 1.0
    assert metrics["merchant_agreement_percent"] == 100.0
    assert metrics["merchant_tier_decision_agreement_percent"] == 100.0
    assert metrics["section_vote_agreement_percent"] == 100.0
    assert metrics["estimated_usd_per_query"] == 0.0


@pytest.mark.unit
def test_merchant_truth_agreement_scores_known_truth_receipts() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)
    truth_count = sum(
        1 for receipt in fixture["receipts"] if receipt.get("merchant_truth")
    )
    assert truth_count > 0

    scorecard = evaluate_fixture(
        fixture,
        CapturedGoldenReplay(fixture),
        backend_name="golden",
    )
    metrics = scorecard["metrics"]

    assert metrics["merchant_truth_sample_count"] == truth_count
    agreement = metrics["merchant_truth_agreement_percent"]
    assert isinstance(agreement, float)
    assert 0.0 <= agreement <= 100.0

    stripped = copy.deepcopy(fixture)
    for receipt in stripped["receipts"]:
        receipt.pop("merchant_truth", None)
    scorecard = evaluate_fixture(
        stripped,
        CapturedGoldenReplay(stripped),
        backend_name="golden",
    )
    assert scorecard["metrics"]["merchant_truth_agreement_percent"] is None
    assert scorecard["metrics"]["merchant_truth_sample_count"] == 0


@pytest.mark.unit
def test_section_vote_matches_cosine_weighted_runtime_semantics() -> None:
    neighbors = [
        ScoredItem(
            "weak-a-1",
            0.9,
            {"image_id": "a", "receipt_id": 1, "section_type": "A"},
        ),
        ScoredItem(
            "weak-a-2",
            0.9,
            {"image_id": "b", "receipt_id": 1, "section_type": "A"},
        ),
        ScoredItem(
            "strong-b",
            0.1,
            {"image_id": "c", "receipt_id": 1, "section_type": "B"},
        ),
    ]

    vote = derive_section_vote(
        neighbors,
        image_id="query",
        receipt_id=1,
        proposed_section_type="B",
    )

    assert vote == {
        "predicted_section_type": "B",
        "proposed_section_type": "B",
        "vote": "agree",
    }


@pytest.mark.unit
def test_fake_scorecard_is_pure_given_fixture() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)
    first = evaluate_fixture(
        fixture,
        FakeVectorIndex(corpus_items(fixture)),
        backend_name="fake",
    )
    second = evaluate_fixture(
        fixture,
        FakeVectorIndex(corpus_items(fixture)),
        backend_name="fake",
    )

    assert first == second
    assert first["metrics"]["latency_ms"]["p50"] == 0.0
    assert first["metrics"]["estimated_usd_per_query"] == 0.0


@pytest.mark.unit
def test_fixture_validation_rejects_missing_family_and_short_word_results() -> (
    None
):
    fixture = load_fixture(GOLDEN_FIXTURE)
    missing = copy.deepcopy(fixture)
    missing["queries"] = [
        query
        for query in missing["queries"]
        if not (
            query["receipt_key"] == missing["receipts"][0]["key"]
            and query["family"] == SECTION_FAMILY
        )
    ]
    with pytest.raises(FixtureError, match="lacks query families"):
        validate_fixture(missing, minimum_receipts=MIN_RECEIPTS)

    short = copy.deepcopy(fixture)
    word_query = next(
        query for query in short["queries"] if query["family"] == WORD_FAMILY
    )
    word_query["expected"]["neighbors"].pop()
    with pytest.raises(FixtureError, match="exactly 30"):
        validate_fixture(short, minimum_receipts=MIN_RECEIPTS)


@pytest.mark.unit
def test_offline_evaluate_self_gate_under_60_seconds() -> None:
    """In-process runtime self-gate, from the cursor Round A entrant.

    The CLI test below times the subprocess path; this pins the library
    path itself so a slow evaluate cannot hide behind interpreter
    startup accounting.
    """

    fixture = load_fixture(GOLDEN_FIXTURE)
    started = time.perf_counter()
    evaluate_fixture(
        fixture,
        FakeVectorIndex(corpus_items(fixture)),
        backend_name="fake",
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 60.0


@pytest.mark.unit
def test_cli_evaluate_stays_well_below_runtime_limits(
    tmp_path: Path,
) -> None:
    scorecard_path = tmp_path / "scorecard.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "receipt_embeddings")
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            str(EVALUATE_SCRIPT),
            "--backend",
            "golden",
            "--fixture",
            str(GOLDEN_FIXTURE),
            "--out",
            str(scorecard_path),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    evaluate_seconds = time.perf_counter() - started

    assert evaluate_seconds < 60
    assert scorecard_path.exists()


def test_failed_gates_reports_only_false_gates() -> None:
    scorecard = {
        "gates": {
            "latency_p95_under_100ms": None,
            "merchant_agreement_at_least_98_percent": True,
            "neighbor_recall_at_least_0_85": False,
            "tier_distribution_within_5_percentage_points": False,
        }
    }
    assert failed_gates(scorecard) == [
        "neighbor_recall_at_least_0_85",
        "tier_distribution_within_5_percentage_points",
    ]
    assert failed_gates({"gates": {}}) == []


def test_fail_on_gate_exits_nonzero_only_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.similarity_harness.evaluate as evaluate_module

    def fake_evaluate_fixture(*args: object, **kwargs: object) -> dict:
        return {
            "backend": "golden",
            "gates": {"neighbor_recall_at_least_0_85": False},
            "metrics": {},
        }

    monkeypatch.setattr(
        evaluate_module, "evaluate_fixture", fake_evaluate_fixture
    )
    common = [
        "--backend",
        "golden",
        "--fixture",
        str(GOLDEN_FIXTURE),
        "--out",
        str(tmp_path / "scorecard.json"),
    ]
    assert evaluate_main(common) == 0
    assert (
        "FAILED GATES: neighbor_recall_at_least_0_85"
        in capsys.readouterr().err
    )
    assert evaluate_main([*common, "--fail-on-gate"]) == 1


def test_fail_on_gate_passes_on_golden_self_parity(tmp_path: Path) -> None:
    assert (
        evaluate_main(
            [
                "--backend",
                "golden",
                "--fixture",
                str(GOLDEN_FIXTURE),
                "--out",
                str(tmp_path / "scorecard.json"),
                "--fail-on-gate",
            ]
        )
        == 0
    )


def test_require_canonical_rejects_the_offline_bootstrap(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match="not canonical"):
        evaluate_main(
            [
                "--backend",
                "golden",
                "--fixture",
                str(GOLDEN_FIXTURE),
                "--out",
                str(tmp_path / "scorecard.json"),
                "--require-canonical",
            ]
        )
