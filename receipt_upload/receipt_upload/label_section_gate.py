"""Pure section-to-word-label compatibility gate.

The gate evaluates a proposed label against the canonical sections assigned
to its line. It is a dependency leaf and imports only the Python standard
library, allowing callers to load it without triggering the wider upload
package's numpy, Pillow, or embedding import chains.

Abstaining on an unsectioned line is a hard rule: DATE was approximately 70%
unsectioned and TIME approximately 72% unsectioned in the 2026-07-18 dev
snapshot because the semi-Markov decoder does not yet emit TRANSACTION_INFO.
Treating those lines as mismatches would therefore manufacture false flags.

This module is not wired into any write path. It only computes a verdict from
an immutable prior artifact and caller-provided section values.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Iterable, Optional

VERDICT_OK = "OK"
VERDICT_LOW_PRIOR = "LOW_PRIOR"
VERDICT_ABSTAIN = "ABSTAIN"

# Measured bad/good flag rates and enrichment by threshold on 2026-07-18:
# 0.01: 10.5%/1.6%/6.5x; 0.02: 18.0%/3.8%/4.8x;
# 0.05: 26.3%/6.3%/4.2x; 0.07: 28.0%/6.7%/4.2x;
# 0.10: 29.7%/7.5%/3.9x; 0.15: 33.0%/9.2%/3.6x;
# 0.30: 33.3%/11.2%/3.0x.
# False flags consume human review time, while misses are silent. A threshold
# of 0.05 sits left of the flat Youden-J plateau (0.200 at 0.05 versus the
# 0.238 maximum at 0.15), retains near-maximal enrichment, and lies on the
# robust 0.05-0.07 shelf rather than at a knife-edge.
DEFAULT_LOW_PRIOR_THRESHOLD = 0.05

# ceil(ln(0.05) / ln(1 - 0.05)) = 59. This is the smallest sample size for
# which a cell with a true rate equal to the threshold has less than a 5%
# probability of producing zero observations. Support is counted in DISTINCT
# SECTIONED LINES (``sectioned_lines``), not word rows: all words on a line
# share the same section assignment, so word rows are not independent trials
# and counting them would overstate support (pseudo-replication).
MIN_SECTIONED_SUPPORT = 59


@dataclass(frozen=True)
class GateResult:
    """Result of evaluating one label against its line's sections."""

    verdict: str
    prior: Optional[float]
    threshold: float
    reason: str


@lru_cache(maxsize=1)
def _default_priors_text() -> str:
    """Read and cache the packaged default prior artifact's text.

    Caching the text rather than the parsed object means every caller gets
    a fresh dict, so no caller can mutate another caller's priors.
    """
    path = files("receipt_upload").joinpath(
        "assets",
        "section_label_priors_v1.json",
    )
    return path.read_text(encoding="utf-8")


def load_priors(path: Optional[str] = None) -> dict[str, Any]:
    """Load priors, caching only reads of the packaged default artifact.

    A caller-supplied path is always read afresh. Objects implementing the
    standard path protocol, such as ``pathlib.Path``, are also accepted by
    Python's built-in ``open`` despite the intentionally narrow annotation.
    """
    if path is None:
        return json.loads(_default_priors_text())

    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def evaluate_label_section(
    label: str,
    section_types: Optional[Iterable[str]],
    priors: dict[str, Any],
    *,
    threshold: float = DEFAULT_LOW_PRIOR_THRESHOLD,
    min_support: int = MIN_SECTIONED_SUPPORT,
) -> GateResult:
    """Evaluate one label against the canonical sections assigned to a line."""
    # NaN compares False everywhere, so an invalid threshold would silently
    # pass every row as OK; fail loudly instead.
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError(
            f"threshold {threshold!r} must be a finite value in [0, 1]"
        )
    canonical_sections = set(priors.get("sections", []))
    line_sections = {
        section_type
        for section_type in section_types or ()
        if section_type in canonical_sections
    }
    if not line_sections:
        return GateResult(
            verdict=VERDICT_ABSTAIN,
            prior=None,
            threshold=threshold,
            reason="unsectioned-line",
        )

    labels = priors.get("labels", {})
    if label not in labels:
        return GateResult(
            verdict=VERDICT_ABSTAIN,
            prior=None,
            threshold=threshold,
            reason="unknown-label",
        )

    entry = labels[label]
    support = int(entry.get("sectioned_lines", entry.get("sectioned", 0)))
    if support < min_support:
        return GateResult(
            verdict=VERDICT_ABSTAIN,
            prior=None,
            threshold=threshold,
            reason="insufficient-support",
        )

    section_priors = entry.get("sections", {})
    cell_values = [
        float(section_priors.get(section_type, {}).get("p", 0.0))
        for section_type in line_sections
    ]
    # A malformed artifact (NaN/inf/out-of-range cells) must fail loudly:
    # NaN compares False against the threshold and would silently pass as OK.
    for value in cell_values:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"Malformed prior cell for label {label!r}: {value!r} is "
                "not a finite probability in [0, 1]"
            )
    prior = max(cell_values)
    if prior < threshold:
        return GateResult(
            verdict=VERDICT_LOW_PRIOR,
            prior=prior,
            threshold=threshold,
            reason="prior-below-threshold",
        )

    return GateResult(
        verdict=VERDICT_OK,
        prior=prior,
        threshold=threshold,
        reason="prior-at-or-above-threshold",
    )


__all__ = [
    "DEFAULT_LOW_PRIOR_THRESHOLD",
    "MIN_SECTIONED_SUPPORT",
    "VERDICT_ABSTAIN",
    "VERDICT_LOW_PRIOR",
    "VERDICT_OK",
    "GateResult",
    "evaluate_label_section",
    "load_priors",
]
