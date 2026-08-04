"""SMART re-OCR strategy ladder (pure, bundleable).

Maps a diagnosed OCR-failure mechanism (free string carried on triage
dossiers and OCRJob.reocr_mechanism, e.g. "reverse-video-total",
"tilted-0deg-quads", "small-print", "pen-stroke") to an ordered ladder
of capture strategies the Swift worker executes: ``plain`` crop,
``invert`` (reverse-video), ``deskew``, or ``upscale2x``.

``choose_strategy()`` picks the strategy for a given attempt number so
attempt 2 tries something DIFFERENT from attempt 1 instead of repeating
a strategy that already failed. A measured-outcome ledger -- built by
``scripts/harvest_reocr_outcomes.py`` from completed OCRJobs and
committed at ``assets/reocr_ladder.json`` (same pattern as the
block-role priors) -- overrides the hand-written default ordering once
a mechanism x strategy pair has enough recorded attempts.

Dependency-free (stdlib only) so the module bundles into the line-item
updater Lambda alongside blocks.py/reocr.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# Contract shared with receipt_dynamo.entities.ocr_job and the Swift
# worker -- keep the values in sync.
STRATEGIES = ("plain", "invert", "deskew", "upscale2x")

# Mechanism-key -> default strategy ordering. Free-string mechanisms
# are normalised onto these keys by prefix (mechanism_key below).
DEFAULT_LADDERS: dict[str, list[str]] = {
    "reverse-video": ["invert", "plain"],
    "tilted": ["deskew", "plain"],
    "small-print": ["upscale2x", "plain"],
}
UNKNOWN_LADDER = ["plain", "upscale2x"]

# A mechanism x strategy pair needs this many harvested attempts before
# its measured acceptance rate outranks the hand-written default order.
MIN_LEDGER_ATTEMPTS = 3

LEDGER_ASSET = Path(__file__).parent / "assets" / "reocr_ladder.json"

_LEDGER_CACHE: dict[str, dict] | None = None

# Dossier-text hints -> canonical mechanism string. Order matters: the
# first matching mechanism wins. Patterns are regexes over the
# lower-cased mode + visual_evidence text.
_MECHANISM_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "reverse-video-total",
        (
            r"reverse[- ]video",
            r"\binverted\b",
            r"white[- ]on[- ]black",
            r"\bknockout\b",
        ),
    ),
    (
        "tilted",
        (r"\btilt", r"skew", r"rotat", r"\bslant"),
    ),
    (
        "small-print",
        (
            r"small[- ]print",
            r"\btiny\b",
            r"fine[- ]print",
            r"microprint",
        ),
    ),
    (
        "pen-stroke",
        (r"\bpen\b", r"handwrit", r"\bink\b", r"scribble"),
    ),
)


def mechanism_key(mechanism: str | None) -> str:
    """Normalise a free-string mechanism onto a ladder key.

    "reverse-video-total" -> "reverse-video"; "tilted-0deg-quads" ->
    "tilted"; anything unrecognised (including "pen-stroke", which has
    no strategy that helps) -> "unknown".
    """
    if not mechanism:
        return "unknown"
    normalised = str(mechanism).strip().lower().replace("_", "-")
    for key in DEFAULT_LADDERS:
        if normalised.startswith(key):
            return key
    return "unknown"


def default_ladder(mechanism: str | None) -> list[str]:
    """Full default strategy order for a mechanism.

    The mechanism's ladder comes first; the remaining strategies are
    appended so attempts past the ladder still try something new
    before any repeat.
    """
    base = DEFAULT_LADDERS.get(mechanism_key(mechanism), UNKNOWN_LADDER)
    return list(base) + [s for s in STRATEGIES if s not in base]


def load_ledger(path: Path | None = None) -> dict[str, dict]:
    """Load the committed outcome ledger; {} when absent or invalid."""
    global _LEDGER_CACHE  # pylint: disable=global-statement
    if path is None and _LEDGER_CACHE is not None:
        return _LEDGER_CACHE
    ledger_path = path or LEDGER_ASSET
    try:
        with open(ledger_path, encoding="utf-8") as handle:
            data = json.load(handle)
        mechanisms = data.get("mechanisms")
        ledger = mechanisms if isinstance(mechanisms, dict) else {}
    except (OSError, ValueError):
        ledger = {}
    if path is None:
        _LEDGER_CACHE = ledger
    return ledger


def _measured_score(
    stats: dict[str, Any], strategy: str
) -> tuple[float, float] | None:
    """(acceptance_rate, mean_delta_improvement) when measured enough."""
    entry = stats.get(strategy)
    if not isinstance(entry, dict):
        return None
    try:
        attempts = int(entry.get("attempts", 0))
    except (TypeError, ValueError):
        return None
    if attempts < MIN_LEDGER_ATTEMPTS:
        return None
    rate = entry.get("acceptance_rate")
    improvement = entry.get("mean_delta_improvement")
    try:
        return (
            float(rate) if rate is not None else 0.0,
            float(improvement) if improvement is not None else 0.0,
        )
    except (TypeError, ValueError):
        return None


def ladder(
    mechanism: str | None, ledger: dict[str, dict] | None = None
) -> list[str]:
    """Strategy order for a mechanism, ledger-adjusted.

    Strategies with enough harvested attempts are promoted ahead of the
    unmeasured ones and ordered by (acceptance_rate,
    mean_delta_improvement) descending; unmeasured strategies keep the
    default relative order after them.
    """
    order = default_ladder(mechanism)
    if ledger is None:
        ledger = load_ledger()
    stats = ledger.get(mechanism_key(mechanism))
    if not isinstance(stats, dict) or not stats:
        return order
    measured = [s for s in order if _measured_score(stats, s) is not None]
    unmeasured = [s for s in order if _measured_score(stats, s) is None]
    measured.sort(key=lambda s: _measured_score(stats, s), reverse=True)
    return measured + unmeasured


def choose_strategy(
    mechanism: str | None,
    attempt_number: int,
    ledger: dict[str, dict] | None = None,
) -> str:
    """Strategy for the given 1-based attempt number.

    Attempt 1 gets the ladder head; attempt 2 the next rung -- a
    DIFFERENT strategy, never a repeat until every strategy has been
    tried once (the full order covers all four).
    """
    if attempt_number < 1:
        attempt_number = 1
    order = ladder(mechanism, ledger)
    return order[(attempt_number - 1) % len(order)]


def mechanism_from_dossier(dossier: Any) -> str | None:
    """Best-effort mechanism from a triage dossier (schema v2).

    Scans ``mode`` plus the ``visual_evidence`` transcript for the
    failure-mechanism vocabulary the triage agents use. Returns a
    canonical mechanism string, or None when nothing matches (callers
    then fall back to the unknown ladder).
    """
    if not isinstance(dossier, dict):
        return None
    texts = [str(dossier.get("mode") or "")]
    evidence = dossier.get("visual_evidence")
    if isinstance(evidence, list):
        texts.extend(str(entry) for entry in evidence)
    blob = " ".join(texts).lower()
    if not blob.strip():
        return None
    for mechanism, patterns in _MECHANISM_HINTS:
        if any(re.search(pattern, blob) for pattern in patterns):
            return mechanism
    return None


def build_ledger(jobs: Iterable[Any]) -> dict[str, dict]:
    """Aggregate completed REGIONAL_REOCR jobs into ledger stats.

    ``jobs`` is any iterable of OCRJob-like objects (attribute access).
    Only COMPLETED jobs with a known strategy and at least one recorded
    word count contribute. Per mechanism-key x strategy: attempts,
    word-level acceptance rate, and mean |delta| improvement over the
    jobs where both deltas were recorded.
    """
    agg: dict[str, dict[str, dict[str, Any]]] = {}
    for job in jobs:
        if str(getattr(job, "status", "")) != "COMPLETED":
            continue
        if str(getattr(job, "job_type", "")) != "REGIONAL_REOCR":
            continue
        strategy = getattr(job, "reocr_strategy", None)
        if strategy not in STRATEGIES:
            continue
        accepted = getattr(job, "reocr_words_accepted", None)
        rejected = getattr(job, "reocr_words_rejected", None)
        if accepted is None and rejected is None:
            continue
        key = mechanism_key(getattr(job, "reocr_mechanism", None))
        entry = agg.setdefault(key, {}).setdefault(
            strategy,
            {
                "attempts": 0,
                "words_accepted": 0,
                "words_rejected": 0,
                "_improvements": [],
            },
        )
        entry["attempts"] += 1
        entry["words_accepted"] += int(accepted or 0)
        entry["words_rejected"] += int(rejected or 0)
        before = getattr(job, "reocr_delta_before", None)
        after = getattr(job, "reocr_delta_after", None)
        if before is not None and after is not None:
            entry["_improvements"].append(abs(before) - abs(after))

    ledger: dict[str, dict] = {}
    for key in sorted(agg):
        ledger[key] = {}
        for strategy in sorted(agg[key]):
            entry = agg[key][strategy]
            total = entry["words_accepted"] + entry["words_rejected"]
            improvements = entry["_improvements"]
            ledger[key][strategy] = {
                "attempts": entry["attempts"],
                "words_accepted": entry["words_accepted"],
                "words_rejected": entry["words_rejected"],
                "acceptance_rate": (
                    round(entry["words_accepted"] / total, 4) if total else 0.0
                ),
                "mean_delta_improvement": (
                    round(sum(improvements) / len(improvements), 4)
                    if improvements
                    else None
                ),
            }
    return ledger
