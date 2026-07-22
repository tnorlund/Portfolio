"""MERCHANT_TRUTH_GATE record: queryable eval/gate history.

Per the MerchantTruth v3.1 contract (docs/architecture/MERCHANT_TRUTH_DYNAMO.md
section 7.6), eval/gate history moves from files-only into a queryable record
class under the merchant partition. A gate record is per-run evidence about a
bundle -- what gated it, when, at which eval code, with which evidence -- and
is deliberately NOT an 8th truth component (section 7.1): it is not part of the
bundle's immutable closure and must never perturb ``bundle_hash`` or the
exact-set gates.

Own ``TYPE`` ``MERCHANT_TRUTH_GATE`` (one-TYPE-per-record-class, section 3): a
fleet-wide gate-history enumeration is one GSITYPE query that never drags other
record classes. The SK ``GATE#{run_at_iso}#v{n:010d}`` sorts before
``PROPOSED#`` and ``TRUTH#``, so the existing ``begins_with(SK, "TRUTH#...")``
bundle queries are untouched; the version segment reuses the same
``v{n:010d}`` zero-padded encoding as the key grammar (section 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_map,
    to_dynamodb_value,
)
from receipt_dynamo.entities.merchant_truth import (
    _validate_hash,
    merchant_truth_pk,
    version_prefix,
)

# The three overall verdicts the eval emits (checks["overall"]).
GATE_OVERALL_VERDICTS = frozenset({"PASS", "PASS_WITH_GAPS", "FAIL"})


def gate_version_segment(version: int) -> str:
    """Return the ``v{n:010d}`` SK segment, reusing the key-grammar padding.

    Delegates range/type validation to ``version_prefix`` (section 2) so the
    gate SK and the truth SK can never disagree on the version encoding.
    """
    return version_prefix(version).split("#", 1)[1]


@dataclass(eq=True)
class MerchantTruthGateRecord(DynamoDBEntity):
    """One append-only gate/eval run against a merchant-truth bundle."""

    slug: str
    run_at: str
    version: int
    bundle_hash: str
    eval_git_sha: str
    overall: str
    per_metric: list[dict[str, Any]]
    gaps: list[dict[str, Any]] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    receipt_tested: Any = None

    def __post_init__(self) -> None:
        # Slug + version validity is enforced by the shared key builders.
        merchant_truth_pk(self.slug)
        gate_version_segment(self.version)
        if not isinstance(self.run_at, str) or not self.run_at:
            raise ValueError("gate record run_at must be a non-empty string")
        _validate_hash(self.bundle_hash, "bundle_hash")
        if not isinstance(self.eval_git_sha, str) or not self.eval_git_sha:
            raise ValueError(
                "gate record eval_git_sha must be a non-empty string"
            )
        if self.overall not in GATE_OVERALL_VERDICTS:
            raise ValueError(f"invalid gate overall verdict: {self.overall!r}")
        # per_metric matches the eval->seal bridge shape (contract section
        # 7.5): a list of {metric, verdict}, the FULL picture, so a seal's
        # manifest gate_results and this record carry byte-identical lists.
        if not isinstance(self.per_metric, list) or not self.per_metric:
            raise ValueError("per_metric must be a non-empty verdict list")
        for entry in self.per_metric:
            if not isinstance(entry, dict):
                raise ValueError("each per_metric entry must be a map")
            metric = entry.get("metric")
            verdict = entry.get("verdict")
            if not isinstance(metric, str) or not metric:
                raise ValueError(
                    "per_metric.metric must be a non-empty string"
                )
            if not isinstance(verdict, str) or not verdict:
                raise ValueError(
                    "per_metric.verdict must be a non-empty string"
                )
        if not isinstance(self.gaps, list):
            raise ValueError("gaps must be a list")
        for gap in self.gaps:
            if not isinstance(gap, dict):
                raise ValueError("each gap must be a map")
            metric = gap.get("metric")
            verdict = gap.get("verdict")
            if not isinstance(metric, str) or not metric:
                raise ValueError("gap.metric must be a non-empty string")
            if not isinstance(verdict, str) or not verdict:
                raise ValueError("gap.verdict must be a non-empty string")
            if "detail" not in gap:
                raise ValueError("gap must carry a detail field")
            # Gaps are the NON-PASS subset (section 7.6): a PASS verdict can
            # never appear in the gap list.
            if verdict == "PASS":
                raise ValueError("a PASS verdict may not appear in gaps")
        # ``coverage`` records the eval's sub-metric coverage_gaps paths
        # (e.g. "columns:top:price") SEPARATELY: they are not per-metric
        # verdicts, so section 7.6 keeps them out of ``gaps`` (which stays
        # exactly the non-PASS per-metric subset the bridge derives). The
        # contract permits additional record fields.
        if not isinstance(self.coverage, list) or any(
            not isinstance(path, str) or not path for path in self.coverage
        ):
            raise ValueError("coverage entries must be non-empty strings")
        if not isinstance(self.evidence_refs, list) or any(
            not isinstance(ref, str) or not ref for ref in self.evidence_refs
        ):
            raise ValueError("evidence_refs must be non-empty strings")
        if not self.receipt_tested:
            raise ValueError("receipt_tested must identify the receipt tested")
        # The section 7.5/7.6 consistency invariant: overall == PASS_WITH_GAPS
        # iff the gap list is non-empty. PASS may never carry gaps; a
        # PASS_WITH_GAPS with an empty gap list is a bridge error. (FAIL is
        # unconstrained here -- its gaps are the work list for closing it.)
        if self.overall == "PASS" and self.gaps:
            raise ValueError(
                "overall PASS may not carry gaps (a non-empty gap list is "
                "never plain PASS)"
            )
        if self.overall == "PASS_WITH_GAPS" and not self.gaps:
            raise ValueError(
                "overall PASS_WITH_GAPS requires a non-empty gaps list"
            )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {
                "S": f"GATE#{self.run_at}#{gate_version_segment(self.version)}"
            },
        }

    def to_item(self) -> dict[str, Any]:
        return {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_GATE"},
            "slug": {"S": self.slug},
            "run_at": {"S": self.run_at},
            "version": {"N": str(self.version)},
            "bundle": to_dynamodb_value(
                {"version": self.version, "hash": self.bundle_hash}
            ),
            "eval_git_sha": {"S": self.eval_git_sha},
            "overall": {"S": self.overall},
            "per_metric": to_dynamodb_value(self.per_metric),
            "gaps": to_dynamodb_value(self.gaps),
            "coverage": to_dynamodb_value(self.coverage),
            "evidence_refs": to_dynamodb_value(self.evidence_refs),
            "receipt_tested": to_dynamodb_value(self.receipt_tested),
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantTruthGateRecord":
        data = parse_dynamodb_map(item)
        bundle = data.get("bundle") or {}
        return cls(
            slug=data["slug"],
            run_at=data["run_at"],
            version=int(bundle.get("version", data.get("version"))),
            bundle_hash=bundle.get("hash", data.get("bundle_hash")),
            eval_git_sha=data["eval_git_sha"],
            overall=data["overall"],
            per_metric=_normalize_per_metric(data.get("per_metric", [])),
            gaps=data.get("gaps", []),
            coverage=data.get("coverage", []),
            evidence_refs=data.get("evidence_refs", []),
            receipt_tested=data.get("receipt_tested"),
        )


def _normalize_per_metric(value: Any) -> Any:
    """Read append-only history across the per_metric shape change.

    The write path only ever emits the eval->seal bridge shape
    (``list[{metric, verdict}]``). A record written before that bridge was
    adopted stored ``per_metric`` as a ``{metric: verdict}`` map; normalize it
    to the canonical sorted list on read so a single pre-bridge record can
    never break ``list_gate_records`` for the whole merchant. Any other type
    is returned untouched for the constructor to validate.
    """
    if isinstance(value, dict):
        return [
            {"metric": name, "verdict": value[name]} for name in sorted(value)
        ]
    return value
