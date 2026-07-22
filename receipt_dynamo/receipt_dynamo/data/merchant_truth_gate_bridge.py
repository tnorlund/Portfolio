"""Adapt ``full_fidelity_eval`` output onto the seal gate contract.

``full_fidelity_eval`` emits ``checks["overall"] ∈ {PASS, PASS_WITH_GAPS,
FAIL}`` with a per-metric verdict under each metric key, while
``_MerchantTruth.seal_version`` derives its gate from an explicit
``gate_results`` block (``_derive_gate_status``: explicit ``status``/``passed``,
fail-closed on ambiguity). This bridge maps one onto the other under the
PASS_WITH_GAPS policy of contract §7.5 / §7.6:

- ``PASS`` -> seals with a passing signal, no gaps.
- ``PASS_WITH_GAPS`` -> **seals** with a passing signal while recording the
  gap list verbatim, so a sealed-with-gaps version is still owner-gated at
  flip time and its gaps are never summarized away.
- ``FAIL`` -> **blocks the seal** (``GateBlockedError``); the version stays
  OPEN and the derived gaps ride the exception as the work list.

Consistency invariant (§7.5): ``overall == "PASS_WITH_GAPS"`` **iff** the gap
list is non-empty. A ``PASS_WITH_GAPS`` with an empty gap list, or a non-empty
gap list presented as a plain ``PASS``, is a bridge error and fails closed.
"""

from __future__ import annotations

from typing import Any, Iterator

from receipt_dynamo.data.shared_exceptions import (
    GateBlockedError,
    GateBridgeError,
)

__all__ = [
    "bridge_eval_to_gate_results",
    "GateBlockedError",
    "GateBridgeError",
]

VALID_OVERALL = frozenset({"PASS", "PASS_WITH_GAPS", "FAIL"})

# Keys in the eval ``checks`` dict that are aggregate signals, not metrics.
_NON_METRIC_KEYS = frozenset({"overall", "coverage_gaps"})


def _metric_entries(
    checks: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(name, entry)`` for each per-metric verdict block.

    A metric entry is any dict value carrying a ``"verdict"`` key; the
    aggregate ``overall`` (a string) and ``coverage_gaps`` (a list) never
    match. Sorted by name so the derived ``per_metric``/``gaps`` are
    deterministic. This shape covers both the seven-metric synthetic pair
    eval and the three-metric real-real eval without enumerating names.
    """
    for name in sorted(checks):
        if name in _NON_METRIC_KEYS:
            continue
        entry = checks[name]
        if isinstance(entry, dict) and "verdict" in entry:
            yield name, entry


def bridge_eval_to_gate_results(checks: dict[str, Any]) -> dict[str, Any]:
    """Convert eval ``checks`` into a ``seal_version`` gate_results block.

    Returns the ``gate_results`` dict that ``seal_version`` accepts for a
    ``PASS`` or ``PASS_WITH_GAPS`` overall. Raises :class:`GateBlockedError`
    on ``FAIL`` (the seal is blocked, version stays OPEN) and
    :class:`GateBridgeError` on structurally inconsistent input (unknown
    ``overall``, the empty-gaps invariant, or non-PASS metrics dressed as a
    plain ``PASS``).

    ``gaps`` is exactly the non-PASS subset of the per-metric verdicts,
    each ``{metric, verdict, detail}`` where ``detail`` is the metric entry's
    remaining fields (everything but ``verdict``), recorded verbatim.
    ``per_metric`` keeps the full picture (every metric's verdict).
    """
    if not isinstance(checks, dict):
        raise GateBridgeError("eval checks must be a mapping")

    overall = checks.get("overall")
    if overall not in VALID_OVERALL:
        raise GateBridgeError(f"unknown eval overall verdict: {overall!r}")

    per_metric: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for name, entry in _metric_entries(checks):
        verdict = entry.get("verdict")
        per_metric.append({"metric": name, "verdict": verdict})
        if verdict != "PASS":
            detail = {
                key: value for key, value in entry.items() if key != "verdict"
            }
            gaps.append({"metric": name, "verdict": verdict, "detail": detail})

    # Two-way consistency invariant (§7.5): PASS_WITH_GAPS iff gaps non-empty.
    if overall == "PASS_WITH_GAPS" and not gaps:
        raise GateBridgeError(
            "overall PASS_WITH_GAPS carries an empty gap list; the "
            "gaps-invariant requires a non-empty gap list"
        )
    if overall == "PASS" and gaps:
        raise GateBridgeError(
            "overall PASS presents non-PASS metrics as a plain pass: "
            f"{[gap['metric'] for gap in gaps]}"
        )

    if overall == "FAIL":
        raise GateBlockedError(
            "eval FAIL blocks the seal; version stays OPEN",
            gate_results={
                "status": "FAIL",
                "passed": False,
                "overall": "FAIL",
                "per_metric": per_metric,
                "gaps": gaps,
            },
        )

    return {
        "status": "PASS",
        "passed": True,
        "overall": overall,
        "per_metric": per_metric,
        "gaps": gaps,
    }
