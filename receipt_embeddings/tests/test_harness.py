"""Offline tests for fixture capture, replay, and scorecard semantics."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from receipt_embeddings import ScoredItem
from receipt_embeddings.testing import FakeVectorIndex
from scripts.similarity_harness.capture_golden import (
    _default_receipts,
    _require_live_environment,
    build_offline_bootstrap,
    compare_fixtures,
)
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
    CapturedChromaReplay,
    evaluate_fixture,
)
from scripts.similarity_harness.evaluate import main as evaluate_main

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "similarity" / "golden.json"
)
CAPTURE_SCRIPT = (
    REPOSITORY_ROOT / "scripts" / "similarity_harness" / "capture_golden.py"
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
def test_offline_bootstrap_is_byte_deterministic(tmp_path: Path) -> None:
    receipts = _default_receipts()
    first = build_offline_bootstrap(receipts)
    second = build_offline_bootstrap(receipts)
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    write_fixture(first_path, first)
    write_fixture(second_path, second)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert (
        compare_fixtures(
            first,
            second,
            distance_tolerance=1e-6,
            vector_tolerance=1e-7,
        )
        == []
    )


@pytest.mark.unit
def test_capture_comparison_tolerates_only_documented_float_drift() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)
    changed = copy.deepcopy(fixture)
    changed["queries"][0]["expected"]["neighbors"][0]["distance"] += 5e-7

    assert (
        compare_fixtures(
            fixture,
            changed,
            distance_tolerance=1e-6,
            vector_tolerance=1e-7,
        )
        == []
    )

    changed["queries"][0]["expected"]["neighbors"][0]["distance"] += 1e-4
    assert compare_fixtures(
        fixture,
        changed,
        distance_tolerance=1e-6,
        vector_tolerance=1e-7,
    )


@pytest.mark.unit
def test_live_capture_requires_existing_dev_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CHROMA_CLOUD_API_KEY",
        "CHROMA_CLOUD_TENANT",
        "CHROMA_CLOUD_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="live capture is disabled"):
        _require_live_environment("ReceiptsTable-dc5be22")


@pytest.mark.unit
def test_live_capture_rejects_prod_database_and_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHROMA_CLOUD_API_KEY", "present")
    monkeypatch.setenv("CHROMA_CLOUD_TENANT", "present")
    monkeypatch.setenv("CHROMA_CLOUD_DATABASE", "receipt_prod")
    with pytest.raises(ValueError, match="refusing to touch Chroma"):
        _require_live_environment("ReceiptsTable-dc5be22")

    monkeypatch.setenv("CHROMA_CLOUD_DATABASE", "receipt_dev")
    with pytest.raises(ValueError, match="refusing to touch DynamoDB"):
        _require_live_environment("ReceiptsTable-d7ff76a")


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
def test_capture_source_contains_no_table_write_operations() -> None:
    source = CAPTURE_SCRIPT.read_text(encoding="utf-8").lower()
    forbidden = (
        ".put_item(",
        ".update_item(",
        ".delete_item(",
        ".batch_write_item(",
        ".transact_write_items(",
        ".update_table(",
        ".create_table(",
        ".upsert(",
        ".create_collection(",
        ".delete_collection(",
    )

    assert not any(token in source for token in forbidden)


@pytest.mark.unit
def test_chroma_self_parity_is_one() -> None:
    fixture = load_fixture(GOLDEN_FIXTURE)
    scorecard = evaluate_fixture(
        fixture,
        CapturedChromaReplay(fixture),
        backend_name="chroma",
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
        CapturedChromaReplay(fixture),
        backend_name="chroma",
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
        CapturedChromaReplay(stripped),
        backend_name="chroma",
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
def test_cli_capture_and_evaluate_stay_well_below_runtime_limits(
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "golden.json"
    scorecard_path = tmp_path / "scorecard.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "receipt_embeddings")
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            str(CAPTURE_SCRIPT),
            "--offline-bootstrap",
            "--out",
            str(fixture_path),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    capture_seconds = time.perf_counter() - started
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            str(EVALUATE_SCRIPT),
            "--backend",
            "chroma",
            "--fixture",
            str(fixture_path),
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

    assert capture_seconds < 15 * 60
    assert evaluate_seconds < 60
    assert scorecard_path.exists()
