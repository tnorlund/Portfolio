"""Unit tests for the SMART re-OCR strategy ladder and harvest
aggregation (receipt_upload.line_items.reocr_strategy)."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from receipt_upload.line_items.reocr_strategy import (
    LEDGER_ASSET,
    MIN_LEDGER_ATTEMPTS,
    STRATEGIES,
    build_ledger,
    choose_strategy,
    default_ladder,
    ladder,
    load_ledger,
    mechanism_from_dossier,
    mechanism_key,
)

# ---------------------------------------------------------------------------
# Mechanism normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mechanism", "expected"),
    [
        ("reverse-video-total", "reverse-video"),
        ("Reverse_Video_Total", "reverse-video"),
        ("tilted-0deg-quads", "tilted"),
        ("small-print", "small-print"),
        ("pen-stroke", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("something-else", "unknown"),
    ],
)
def test_mechanism_key(mechanism, expected):
    assert mechanism_key(mechanism) == expected


# ---------------------------------------------------------------------------
# Default ladders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mechanism", "head"),
    [
        ("reverse-video-total", ["invert", "plain"]),
        ("tilted-0deg-quads", ["deskew", "plain"]),
        ("small-print", ["upscale2x", "plain"]),
        ("pen-stroke", ["plain", "upscale2x"]),
        (None, ["plain", "upscale2x"]),
    ],
)
def test_default_ladder_heads(mechanism, head):
    order = default_ladder(mechanism)
    assert order[: len(head)] == head
    # Every strategy appears exactly once so retries never repeat
    # before all four are exhausted.
    assert sorted(order) == sorted(STRATEGIES)


def test_choose_strategy_attempt_two_differs():
    for mechanism in (
        "reverse-video-total",
        "tilted-0deg-quads",
        "small-print",
        "pen-stroke",
        None,
    ):
        first = choose_strategy(mechanism, 1, ledger={})
        second = choose_strategy(mechanism, 2, ledger={})
        assert first != second


def test_choose_strategy_first_four_attempts_all_distinct():
    picks = [
        choose_strategy("reverse-video-total", n, ledger={})
        for n in range(1, 5)
    ]
    assert sorted(picks) == sorted(STRATEGIES)


def test_choose_strategy_defaults():
    assert choose_strategy("reverse-video-total", 1, ledger={}) == "invert"
    assert choose_strategy("reverse-video-total", 2, ledger={}) == "plain"
    assert choose_strategy("tilted-0deg-quads", 1, ledger={}) == "deskew"
    assert choose_strategy("small-print", 1, ledger={}) == "upscale2x"
    assert choose_strategy(None, 1, ledger={}) == "plain"
    assert choose_strategy(None, 2, ledger={}) == "upscale2x"
    # attempt_number below 1 clamps to attempt 1
    assert choose_strategy(None, 0, ledger={}) == "plain"


# ---------------------------------------------------------------------------
# Ledger override
# ---------------------------------------------------------------------------


def _ledger_entry(attempts, rate, improvement=0.0):
    return {
        "attempts": attempts,
        "acceptance_rate": rate,
        "mean_delta_improvement": improvement,
    }


def test_ledger_overrides_default_order():
    # Measured: plain beats invert for reverse-video (hypothetically).
    ledger = {
        "reverse-video": {
            "invert": _ledger_entry(5, 0.2),
            "plain": _ledger_entry(5, 0.9),
        }
    }
    assert ladder("reverse-video-total", ledger)[:2] == ["plain", "invert"]
    assert choose_strategy("reverse-video-total", 1, ledger) == "plain"
    assert choose_strategy("reverse-video-total", 2, ledger) == "invert"


def test_ledger_below_min_attempts_is_ignored():
    ledger = {
        "reverse-video": {
            "invert": _ledger_entry(MIN_LEDGER_ATTEMPTS - 1, 0.0),
            "plain": _ledger_entry(MIN_LEDGER_ATTEMPTS - 1, 1.0),
        }
    }
    # Not enough evidence: hand-written default order stands.
    assert ladder("reverse-video-total", ledger)[:2] == ["invert", "plain"]


def test_ledger_tie_breaks_on_delta_improvement():
    ledger = {
        "unknown": {
            "plain": _ledger_entry(4, 0.5, improvement=0.1),
            "upscale2x": _ledger_entry(4, 0.5, improvement=2.5),
        }
    }
    assert ladder("pen-stroke", ledger)[:2] == ["upscale2x", "plain"]


def test_ledger_for_other_mechanism_does_not_leak():
    ledger = {"tilted": {"plain": _ledger_entry(9, 1.0)}}
    assert ladder("reverse-video-total", ledger)[:2] == ["invert", "plain"]


def test_load_ledger_missing_file_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == {}


def test_load_ledger_invalid_json_returns_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_ledger(bad) == {}


def test_committed_asset_is_valid():
    data = json.loads(Path(LEDGER_ASSET).read_text(encoding="utf-8"))
    assert data["schema"] == "reocr-ladder-v1"
    assert isinstance(data["mechanisms"], dict)
    # And the loader accepts it.
    assert isinstance(load_ledger(LEDGER_ASSET), dict)


# ---------------------------------------------------------------------------
# Dossier -> mechanism
# ---------------------------------------------------------------------------


def test_mechanism_from_dossier_reverse_video():
    dossier = {
        "mode": "E-image-suspect",
        "visual_evidence": [
            "Transcribed rows: BURGER 9.99, FRIES 3.49",
            "Printed TOTAL is reverse-video (white on black); OCR "
            "dropped it",
        ],
    }
    assert mechanism_from_dossier(dossier) == "reverse-video-total"


def test_mechanism_from_dossier_tilted():
    dossier = {
        "mode": "J-unknown",
        "visual_evidence": ["Receipt photographed tilted; rows skewed"],
    }
    assert mechanism_from_dossier(dossier) == "tilted"


def test_mechanism_from_dossier_small_print():
    dossier = {"visual_evidence": ["fine print at the bottom unreadable"]}
    assert mechanism_from_dossier(dossier) == "small-print"


def test_mechanism_from_dossier_pen_stroke():
    dossier = {"visual_evidence": ["a pen stroke crosses the total line"]}
    assert mechanism_from_dossier(dossier) == "pen-stroke"


def test_mechanism_from_dossier_no_false_pen_match():
    # "open" must not substring-match the pen hint.
    dossier = {"visual_evidence": ["store was open late; totals match"]}
    assert mechanism_from_dossier(dossier) is None


@pytest.mark.parametrize("dossier", [None, [], "text", {}, {"mode": ""}])
def test_mechanism_from_dossier_degenerate_inputs(dossier):
    assert mechanism_from_dossier(dossier) is None


# ---------------------------------------------------------------------------
# Harvest aggregation (build_ledger)
# ---------------------------------------------------------------------------


def _job(**overrides):
    values = {
        "status": "COMPLETED",
        "job_type": "REGIONAL_REOCR",
        "reocr_strategy": "invert",
        "reocr_mechanism": "reverse-video-total",
        "reocr_words_accepted": 8,
        "reocr_words_rejected": 2,
        "reocr_delta_before": -4.0,
        "reocr_delta_after": 0.0,
        "created_at": datetime(2026, 8, 1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_ledger_aggregates_fixture_jobs():
    jobs = [
        _job(),
        _job(
            reocr_words_accepted=2,
            reocr_words_rejected=8,
            reocr_delta_before=-4.0,
            reocr_delta_after=-3.0,
        ),
        _job(
            reocr_strategy="plain",
            reocr_words_accepted=0,
            reocr_words_rejected=5,
            reocr_delta_before=None,
            reocr_delta_after=None,
        ),
        _job(reocr_mechanism="tilted-0deg-quads", reocr_strategy="deskew"),
    ]
    ledger = build_ledger(jobs)

    rv_invert = ledger["reverse-video"]["invert"]
    assert rv_invert["attempts"] == 2
    assert rv_invert["words_accepted"] == 10
    assert rv_invert["words_rejected"] == 10
    assert rv_invert["acceptance_rate"] == 0.5
    # improvements: (4.0 - 0.0) and (4.0 - 3.0) -> mean 2.5
    assert rv_invert["mean_delta_improvement"] == 2.5

    rv_plain = ledger["reverse-video"]["plain"]
    assert rv_plain["attempts"] == 1
    assert rv_plain["acceptance_rate"] == 0.0
    assert rv_plain["mean_delta_improvement"] is None

    assert ledger["tilted"]["deskew"]["attempts"] == 1


def test_build_ledger_filters_non_contributing_jobs():
    jobs = [
        _job(status="PENDING"),
        _job(job_type="FIRST_PASS"),
        _job(reocr_strategy=None),
        _job(reocr_strategy="sharpen"),
        _job(reocr_words_accepted=None, reocr_words_rejected=None),
    ]
    assert build_ledger(jobs) == {}


def test_build_ledger_unknown_mechanism_bucket():
    ledger = build_ledger([_job(reocr_mechanism="pen-stroke")])
    assert list(ledger) == ["unknown"]


def test_build_ledger_feeds_choose_strategy():
    # Harvested outcomes flip the reverse-video ordering.
    jobs = [
        _job(
            reocr_strategy="invert",
            reocr_words_accepted=0,
            reocr_words_rejected=10,
        )
        for _ in range(MIN_LEDGER_ATTEMPTS)
    ] + [
        _job(
            reocr_strategy="plain",
            reocr_words_accepted=10,
            reocr_words_rejected=0,
        )
        for _ in range(MIN_LEDGER_ATTEMPTS)
    ]
    ledger = build_ledger(jobs)
    assert choose_strategy("reverse-video-total", 1, ledger) == "plain"
    assert choose_strategy("reverse-video-total", 2, ledger) == "invert"
