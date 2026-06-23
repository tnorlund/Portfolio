"""Audit synthetic receipt augmentation against Dynamo training metrics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any, Iterable

from receipt_agent.agents.label_evaluator.pattern_discovery import (
    normalize_confusion_label,
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConfusionPairAudit:
    """Before/after count for one targeted confusion pair."""

    actual_label: str
    predicted_label: str
    before_count: int
    after_count: int
    delta: int
    relative_reduction: float
    improved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "actual_label": self.actual_label,
            "predicted_label": self.predicted_label,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "delta": self.delta,
            "relative_reduction": self.relative_reduction,
            "improved": self.improved,
        }


@dataclass(frozen=True)
class SyntheticAugmentationAudit:
    """Decision record for a synthetic-augmentation training run."""

    baseline_job_id: str
    augmented_job_id: str
    recommendation: str
    synthetic_train_examples: int | None
    synthetic_candidates_seen: int | None
    synthetic_candidates_accepted: int | None
    synthetic_candidates_rejected: int | None
    synthetic_acceptance_rate: float | None
    synthetic_rejection_reasons: dict[str, int]
    synthetic_accepted_candidate_quality: dict[str, Any]
    synthetic_accepted_candidate_quality_components: dict[str, Any]
    synthetic_accepted_mix_balance: dict[str, Any]
    synthetic_accepted_real_baseline_comparison: dict[str, Any]
    synthetic_mix_balance_signal: bool
    synthetic_real_baseline_signal: bool
    synthetic_quality_signal: bool
    target_pair_count: int
    improved_pair_count: int
    total_before_count: int
    total_after_count: int
    total_relative_reduction: float
    val_f1_before: float | None
    val_f1_after: float | None
    val_f1_delta: float | None
    train_loss_delta: float | None
    eval_loss_delta: float | None
    overtraining_signal: bool
    reasons: list[str]
    pair_audits: list[ConfusionPairAudit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_job_id": self.baseline_job_id,
            "augmented_job_id": self.augmented_job_id,
            "recommendation": self.recommendation,
            "synthetic_train_examples": self.synthetic_train_examples,
            "synthetic_candidates_seen": self.synthetic_candidates_seen,
            "synthetic_candidates_accepted": self.synthetic_candidates_accepted,
            "synthetic_candidates_rejected": self.synthetic_candidates_rejected,
            "synthetic_acceptance_rate": self.synthetic_acceptance_rate,
            "synthetic_rejection_reasons": self.synthetic_rejection_reasons,
            "synthetic_accepted_candidate_quality": (
                self.synthetic_accepted_candidate_quality
            ),
            "synthetic_accepted_candidate_quality_components": (
                self.synthetic_accepted_candidate_quality_components
            ),
            "synthetic_accepted_mix_balance": self.synthetic_accepted_mix_balance,
            "synthetic_accepted_real_baseline_comparison": (
                self.synthetic_accepted_real_baseline_comparison
            ),
            "synthetic_mix_balance_signal": self.synthetic_mix_balance_signal,
            "synthetic_real_baseline_signal": self.synthetic_real_baseline_signal,
            "synthetic_quality_signal": self.synthetic_quality_signal,
            "target_pair_count": self.target_pair_count,
            "improved_pair_count": self.improved_pair_count,
            "total_before_count": self.total_before_count,
            "total_after_count": self.total_after_count,
            "total_relative_reduction": self.total_relative_reduction,
            "val_f1_before": self.val_f1_before,
            "val_f1_after": self.val_f1_after,
            "val_f1_delta": self.val_f1_delta,
            "train_loss_delta": self.train_loss_delta,
            "eval_loss_delta": self.eval_loss_delta,
            "overtraining_signal": self.overtraining_signal,
            "reasons": self.reasons,
            "pair_audits": [audit.to_dict() for audit in self.pair_audits],
        }


def _metric_name(metric: Any) -> str | None:
    if isinstance(metric, dict):
        return metric.get("metric_name")
    return getattr(metric, "metric_name", None)


def _metric_value(metric: Any) -> Any:
    if isinstance(metric, dict):
        return metric.get("value")
    return getattr(metric, "value", None)


def _metric_epoch(metric: Any) -> int | None:
    if isinstance(metric, dict):
        return metric.get("epoch")
    return getattr(metric, "epoch", None)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _latest_numeric(metrics: list[Any]) -> float | None:
    numeric_metrics = [
        metric
        for metric in metrics
        if _numeric(_metric_value(metric)) is not None
    ]
    if not numeric_metrics:
        return None
    selected = max(
        numeric_metrics,
        key=lambda metric: (
            _metric_epoch(metric) is not None,
            _metric_epoch(metric) or -1,
        ),
    )
    return _numeric(_metric_value(selected))


def _latest_mapping(metrics: list[Any]) -> dict[str, Any]:
    candidates = [
        metric for metric in metrics if isinstance(_metric_value(metric), dict)
    ]
    if not candidates:
        return {}
    selected = max(
        candidates,
        key=lambda metric: (
            _metric_epoch(metric) is not None,
            _metric_epoch(metric) or -1,
        ),
    )
    return dict(_metric_value(selected))


def _latest_int(metrics: list[Any]) -> int | None:
    value = _latest_numeric(metrics)
    return int(value) if value is not None else None


def _compact_reason_counts(value: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason, count in value.items():
        numeric_count = _numeric(count)
        if numeric_count is None or numeric_count <= 0:
            continue
        counts[str(reason)] = int(numeric_count)
    return dict(sorted(counts.items()))


def _compact_score_summary(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    count = _numeric(value.get("count"))
    if count is not None:
        result["count"] = int(count)
    for key in ("avg", "min", "max"):
        score = _numeric(value.get(key))
        if score is not None:
            result[key] = score
    return result


def _compact_component_score_summary(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, summary in value.items():
        if not isinstance(summary, dict):
            continue
        compact = _compact_score_summary(summary)
        if compact:
            result[str(name)] = compact
    return result


def _compact_mix_balance(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "accepted_count": _safe_int(value.get("accepted_count")),
        "merchant_count": _safe_int(value.get("merchant_count")),
        "operation_count": _safe_int(value.get("operation_count")),
        "top_merchant": value.get("top_merchant"),
        "top_merchant_count": _safe_int(value.get("top_merchant_count")),
        "top_merchant_share": _numeric(value.get("top_merchant_share")),
        "top_operation": value.get("top_operation"),
        "top_operation_count": _safe_int(value.get("top_operation_count")),
        "top_operation_share": _numeric(value.get("top_operation_share")),
        "merchant_entropy": _numeric(value.get("merchant_entropy")),
        "operation_entropy": _numeric(value.get("operation_entropy")),
        "risk_level": value.get("risk_level"),
        "risk_reasons": [
            str(reason)
            for reason in (value.get("risk_reasons") or [])[:8]
            if reason
        ],
    }
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _compact_real_baseline_comparison_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {
        "count": _safe_int(value.get("count")),
        "within_real_score_range_count": _safe_int(
            value.get("within_real_score_range_count")
        ),
        "below_real_score_range_count": _safe_int(
            value.get("below_real_score_range_count")
        ),
        "within_real_score_range_share": _numeric(
            value.get("within_real_score_range_share")
        ),
    }
    for key in (
        "candidate_score",
        "baseline_avg",
        "baseline_min",
        "baseline_pair_count",
        "delta_from_avg",
        "delta_from_min",
    ):
        raw_summary = value.get(key)
        compact = (
            _compact_score_summary(raw_summary)
            if isinstance(raw_summary, dict)
            else {}
        )
        if compact:
            result[key] = compact
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _best_f1_metric(metrics: list[Any]) -> Any | None:
    candidates = [
        metric
        for metric in metrics
        if _numeric(_metric_value(metric)) is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda metric: (
            _numeric(_metric_value(metric)) or float("-inf"),
            _metric_epoch(metric) or -1,
        ),
    )


def _confusion_matrix_for_epoch(
    metrics: list[Any], epoch: int | None
) -> dict[str, Any] | None:
    candidates = [
        metric
        for metric in metrics
        if isinstance(_metric_value(metric), dict)
        and (
            epoch is None
            or _metric_epoch(metric) == epoch
            or _metric_epoch(metric) is None
        )
    ]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda metric: (
            _metric_epoch(metric) is not None,
            _metric_epoch(metric) or -1,
        ),
    )
    return dict(_metric_value(selected))


def metrics_summary_from_job_metrics(
    job_metrics: Iterable[Any],
) -> dict[str, Any]:
    """Summarize Dynamo ``JobMetric`` rows for augmentation auditing.

    ``val_f1`` and the confusion matrix are selected from the best validation
    epoch. Losses are selected from the latest logged epoch so overtraining
    checks can catch final train/eval divergence.
    """
    by_name: dict[str, list[Any]] = defaultdict(list)
    for metric in job_metrics:
        name = _metric_name(metric)
        if name:
            by_name[name].append(metric)

    best_f1_metric = _best_f1_metric(by_name.get("val_f1", []))
    best_epoch = _metric_epoch(best_f1_metric) if best_f1_metric else None
    synthetic_examples = _latest_int(
        by_name.get("synthetic_train_examples", [])
    )

    return {
        "best_epoch": best_epoch,
        "val_f1": (
            _numeric(_metric_value(best_f1_metric)) if best_f1_metric else None
        ),
        "train_loss": _latest_numeric(by_name.get("train_loss", [])),
        "eval_loss": _latest_numeric(by_name.get("eval_loss", [])),
        "confusion_matrix": _confusion_matrix_for_epoch(
            by_name.get("confusion_matrix", []), best_epoch
        ),
        "synthetic_train_examples": synthetic_examples,
        "synthetic_candidates_seen": _latest_int(
            by_name.get("synthetic_candidates_seen", [])
        ),
        "synthetic_candidates_accepted": _latest_int(
            by_name.get("synthetic_candidates_accepted", [])
        ),
        "synthetic_candidates_rejected": _latest_int(
            by_name.get("synthetic_candidates_rejected", [])
        ),
        "synthetic_rejection_reasons": _compact_reason_counts(
            _latest_mapping(by_name.get("synthetic_rejection_reasons", []))
        ),
        "synthetic_accepted_operation_counts": _compact_reason_counts(
            _latest_mapping(
                by_name.get("synthetic_accepted_operation_counts", [])
            )
        ),
        "synthetic_accepted_category_counts": _compact_reason_counts(
            _latest_mapping(
                by_name.get("synthetic_accepted_category_counts", [])
            )
        ),
        "synthetic_accepted_field_replacement_counts": _compact_reason_counts(
            _latest_mapping(
                by_name.get(
                    "synthetic_accepted_field_replacement_counts",
                    [],
                )
            )
        ),
        "synthetic_accepted_structure_similarity": _compact_score_summary(
            _latest_mapping(
                by_name.get("synthetic_accepted_structure_similarity", [])
            )
        ),
        "synthetic_accepted_structure_components": _compact_component_score_summary(
            _latest_mapping(
                by_name.get("synthetic_accepted_structure_components", [])
            )
        ),
        "synthetic_accepted_candidate_quality": _compact_score_summary(
            _latest_mapping(
                by_name.get("synthetic_accepted_candidate_quality", [])
            )
        ),
        "synthetic_accepted_candidate_quality_components": (
            _compact_component_score_summary(
                _latest_mapping(
                    by_name.get(
                        "synthetic_accepted_candidate_quality_components",
                        [],
                    )
                )
            )
        ),
        "synthetic_accepted_mix_balance": _compact_mix_balance(
            _latest_mapping(by_name.get("synthetic_accepted_mix_balance", []))
        ),
        "synthetic_accepted_real_baseline_comparison": (
            _compact_real_baseline_comparison_summary(
                _latest_mapping(
                    by_name.get(
                        "synthetic_accepted_real_baseline_comparison",
                        [],
                    )
                )
            )
        ),
        "synthetic_accepted_grounded_count": _latest_int(
            by_name.get("synthetic_accepted_grounded_count", [])
        ),
        "synthetic_accepted_arithmetic_count": _latest_int(
            by_name.get("synthetic_accepted_arithmetic_count", [])
        ),
    }


def _target_pair(target: Any) -> tuple[str, str] | None:
    if isinstance(target, dict):
        actual = target.get("actual_label")
        predicted = target.get("predicted_label")
    elif isinstance(target, (tuple, list)) and len(target) >= 2:
        actual, predicted = target[0], target[1]
    else:
        actual = getattr(target, "actual_label", None)
        predicted = getattr(target, "predicted_label", None)
    actual_label = normalize_confusion_label(actual)
    predicted_label = normalize_confusion_label(predicted)
    if (
        not actual_label
        or not predicted_label
        or actual_label == predicted_label
    ):
        return None
    return actual_label, predicted_label


def target_pairs_from_plan(
    plan: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """Extract targeted label pairs from a synthetic receipt plan artifact."""
    pairs: list[tuple[str, str]] = []
    for recipe in (plan or {}).get("recipes", []):
        pair = _target_pair(recipe)
        if pair and pair not in pairs:
            pairs.append(pair)
    return pairs


def target_pairs_from_pattern_artifact(
    artifact: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """Extract target pairs from a pattern-builder artifact.

    The pattern builder writes the synthetic receipt plan under
    ``synthetic_receipt_plan``. Tests and ad-hoc audits sometimes pass the
    plan itself, so this accepts both shapes.
    """
    if not artifact:
        return []
    if "recipes" in artifact:
        return target_pairs_from_plan(artifact)
    plan = artifact.get("synthetic_receipt_plan")
    if isinstance(plan, dict):
        return target_pairs_from_plan(plan)
    line_item_patterns = artifact.get("line_item_patterns")
    if isinstance(line_item_patterns, dict):
        return target_pairs_from_pattern_artifact(line_item_patterns)
    return []


def confusion_pair_count(
    confusion_matrix: dict[str, Any] | None,
    actual_label: str,
    predicted_label: str,
) -> int:
    """Count one confusion pair, collapsing BIO labels when present."""
    if not confusion_matrix:
        return 0
    labels = confusion_matrix.get("labels") or []
    matrix = confusion_matrix.get("matrix") or []
    actual = normalize_confusion_label(actual_label)
    predicted = normalize_confusion_label(predicted_label)
    total = 0
    for row_idx, row_label in enumerate(labels):
        if normalize_confusion_label(row_label) != actual:
            continue
        row = matrix[row_idx] if row_idx < len(matrix) else []
        for col_idx, col_label in enumerate(labels):
            if normalize_confusion_label(col_label) != predicted:
                continue
            if col_idx < len(row):
                total += int(row[col_idx] or 0)
    return total


def _job_id(job: Any) -> str | None:
    if isinstance(job, dict):
        return job.get("job_id")
    return getattr(job, "job_id", None)


def _job_name(job: Any) -> str | None:
    if isinstance(job, dict):
        return job.get("name")
    return getattr(job, "name", None)


def resolve_job_id_from_ref(dynamo_client: Any, job_ref: str) -> str:
    """Resolve a Dynamo LayoutLM job id from a job id or job name.

    SageMaker start responses expose the training job name, while the trainer
    stores metrics under an internal Dynamo UUID. This helper bridges those two
    identifiers so audits can be triggered from either side of the workflow.
    """
    if not job_ref or not isinstance(job_ref, str):
        raise ValueError("job_ref must be a non-empty string")

    if UUID_RE.match(job_ref):
        return job_ref

    if hasattr(dynamo_client, "get_job_by_name"):
        jobs, _ = dynamo_client.get_job_by_name(job_ref, limit=1)
        if jobs:
            job_id = _job_id(jobs[0])
            if job_id:
                return job_id

    if hasattr(dynamo_client, "list_jobs"):
        jobs, _ = dynamo_client.list_jobs(limit=500)
        matches = [job for job in jobs if _job_name(job) == job_ref]
        if matches:
            matches.sort(
                key=lambda job: (
                    (
                        job.get("created_at")
                        if isinstance(job, dict)
                        else getattr(job, "created_at", "")
                    )
                    or ""
                ),
                reverse=True,
            )
            job_id = _job_id(matches[0])
            if job_id:
                return job_id

    raise ValueError(f"Could not resolve LayoutLM job reference: {job_ref}")


def _list_all_job_metrics(dynamo_client: Any, job_id: str) -> list[Any]:
    metrics: list[Any] = []
    last_key = None
    while True:
        page, last_key = dynamo_client.list_job_metrics(
            job_id,
            last_evaluated_key=last_key,
        )
        metrics.extend(page)
        if not last_key:
            return metrics


def metrics_summary_from_dynamo_job(
    dynamo_client: Any,
    job_ref: str,
) -> tuple[str, dict[str, Any]]:
    """Resolve a job ref and summarize its Dynamo ``JobMetric`` rows."""
    job_id = resolve_job_id_from_ref(dynamo_client, job_ref)
    return job_id, metrics_summary_from_job_metrics(
        _list_all_job_metrics(dynamo_client, job_id)
    )


def _metric_delta(
    before: dict[str, Any],
    after: dict[str, Any],
    metric_name: str,
) -> float | None:
    before_value = _numeric(before.get(metric_name))
    after_value = _numeric(after.get(metric_name))
    if before_value is None or after_value is None:
        return None
    return after_value - before_value


def _safe_int(value: Any) -> int | None:
    numeric_value = _numeric(value)
    return int(numeric_value) if numeric_value is not None else None


def _synthetic_acceptance_rate(
    *,
    seen: int | None,
    accepted: int | None,
) -> float | None:
    if seen is None or seen <= 0:
        return None
    return round((accepted or 0) / seen, 4)


def _candidate_quality_below_threshold(
    summary: dict[str, Any],
    *,
    threshold: float,
) -> bool:
    if not summary:
        return False
    count = _safe_int(summary.get("count"))
    if count is not None and count <= 0:
        return False
    minimum = _numeric(summary.get("min"))
    average = _numeric(summary.get("avg"))
    return bool(
        (minimum is not None and minimum < threshold)
        or (average is not None and average < threshold)
    )


def _mix_balance_risk_signal(balance: dict[str, Any]) -> bool:
    risk_level = str(balance.get("risk_level") or "").strip().lower()
    if risk_level in {"medium", "high"}:
        return True
    accepted_count = _safe_int(balance.get("accepted_count")) or 0
    if accepted_count < 3:
        return False
    top_merchant_share = _numeric(balance.get("top_merchant_share"))
    top_operation_share = _numeric(balance.get("top_operation_share"))
    merchant_count = _safe_int(balance.get("merchant_count")) or 0
    operation_count = _safe_int(balance.get("operation_count")) or 0
    return bool(
        merchant_count <= 1
        or operation_count <= 1
        or (top_merchant_share is not None and top_merchant_share >= 0.67)
        or (top_operation_share is not None and top_operation_share >= 0.80)
    )


def _real_baseline_risk_signal(summary: dict[str, Any]) -> bool:
    count = _safe_int(summary.get("count")) or 0
    if count <= 0:
        return False
    below_count = _safe_int(summary.get("below_real_score_range_count"))
    if below_count is not None:
        return below_count > 0
    within_share = _numeric(summary.get("within_real_score_range_share"))
    return bool(within_share is not None and within_share < 1.0)


def build_synthetic_augmentation_audit(
    *,
    baseline_job_id: str,
    augmented_job_id: str,
    baseline_metrics: dict[str, Any],
    augmented_metrics: dict[str, Any],
    target_pairs: Iterable[Any],
    min_confusion_reduction: float = 0.05,
    max_val_f1_drop: float = 0.005,
    min_synthetic_acceptance_rate: float = 0.5,
    min_accepted_candidate_quality: float = 0.75,
) -> SyntheticAugmentationAudit:
    """Compare a synthetic-augmented run against its baseline metrics."""
    normalized_pairs = [
        pair for target in target_pairs if (pair := _target_pair(target))
    ]

    pair_audits: list[ConfusionPairAudit] = []
    for actual_label, predicted_label in normalized_pairs:
        before_count = confusion_pair_count(
            baseline_metrics.get("confusion_matrix"),
            actual_label,
            predicted_label,
        )
        after_count = confusion_pair_count(
            augmented_metrics.get("confusion_matrix"),
            actual_label,
            predicted_label,
        )
        delta = after_count - before_count
        relative_reduction = (
            (before_count - after_count) / before_count
            if before_count > 0
            else 0.0
        )
        pair_audits.append(
            ConfusionPairAudit(
                actual_label=actual_label,
                predicted_label=predicted_label,
                before_count=before_count,
                after_count=after_count,
                delta=delta,
                relative_reduction=relative_reduction,
                improved=relative_reduction >= min_confusion_reduction,
            )
        )

    total_before = sum(audit.before_count for audit in pair_audits)
    total_after = sum(audit.after_count for audit in pair_audits)
    total_relative_reduction = (
        (total_before - total_after) / total_before
        if total_before > 0
        else 0.0
    )
    improved_pair_count = sum(1 for audit in pair_audits if audit.improved)

    val_f1_delta = _metric_delta(baseline_metrics, augmented_metrics, "val_f1")
    train_loss_delta = _metric_delta(
        baseline_metrics, augmented_metrics, "train_loss"
    )
    eval_loss_delta = _metric_delta(
        baseline_metrics, augmented_metrics, "eval_loss"
    )

    val_f1_drop = val_f1_delta is not None and val_f1_delta < -max_val_f1_drop
    train_eval_divergence = (
        train_loss_delta is not None
        and eval_loss_delta is not None
        and train_loss_delta < 0
        and eval_loss_delta > 0
    )
    overtraining_signal = bool(val_f1_drop or train_eval_divergence)
    synthetic_candidates_seen = _safe_int(
        augmented_metrics.get("synthetic_candidates_seen")
    )
    synthetic_candidates_accepted = _safe_int(
        augmented_metrics.get("synthetic_candidates_accepted")
    )
    synthetic_candidates_rejected = _safe_int(
        augmented_metrics.get("synthetic_candidates_rejected")
    )
    synthetic_acceptance_rate = _synthetic_acceptance_rate(
        seen=synthetic_candidates_seen,
        accepted=synthetic_candidates_accepted,
    )
    raw_rejection_reasons = augmented_metrics.get(
        "synthetic_rejection_reasons"
    )
    synthetic_rejection_reasons = (
        _compact_reason_counts(raw_rejection_reasons)
        if isinstance(raw_rejection_reasons, dict)
        else {}
    )
    raw_candidate_quality = augmented_metrics.get(
        "synthetic_accepted_candidate_quality"
    )
    synthetic_accepted_candidate_quality = (
        _compact_score_summary(raw_candidate_quality)
        if isinstance(raw_candidate_quality, dict)
        else {}
    )
    raw_candidate_quality_components = augmented_metrics.get(
        "synthetic_accepted_candidate_quality_components"
    )
    synthetic_accepted_candidate_quality_components = (
        _compact_component_score_summary(raw_candidate_quality_components)
        if isinstance(raw_candidate_quality_components, dict)
        else {}
    )
    raw_mix_balance = augmented_metrics.get("synthetic_accepted_mix_balance")
    synthetic_accepted_mix_balance = (
        _compact_mix_balance(raw_mix_balance)
        if isinstance(raw_mix_balance, dict)
        else {}
    )
    raw_real_baseline = augmented_metrics.get(
        "synthetic_accepted_real_baseline_comparison"
    ) or augmented_metrics.get("accepted_real_baseline_comparison")
    synthetic_accepted_real_baseline_comparison = (
        _compact_real_baseline_comparison_summary(raw_real_baseline)
        if isinstance(raw_real_baseline, dict)
        else {}
    )
    acceptance_quality_signal = (
        synthetic_acceptance_rate is not None
        and synthetic_acceptance_rate < min_synthetic_acceptance_rate
    )
    accepted_candidate_quality_signal = _candidate_quality_below_threshold(
        synthetic_accepted_candidate_quality,
        threshold=min_accepted_candidate_quality,
    )
    synthetic_mix_balance_signal = _mix_balance_risk_signal(
        synthetic_accepted_mix_balance
    )
    synthetic_real_baseline_signal = _real_baseline_risk_signal(
        synthetic_accepted_real_baseline_comparison
    )
    synthetic_quality_signal = (
        acceptance_quality_signal
        or accepted_candidate_quality_signal
        or synthetic_mix_balance_signal
        or synthetic_real_baseline_signal
    )

    reasons: list[str] = []
    target_improved = total_relative_reduction >= min_confusion_reduction
    if target_improved:
        reasons.append(
            "Targeted confusion pairs decreased by "
            f"{total_relative_reduction:.1%}."
        )
    else:
        reasons.append("Targeted confusion pairs did not decrease enough.")
    if val_f1_delta is not None:
        reasons.append(f"Validation F1 changed by {val_f1_delta:+.4f}.")
    if train_eval_divergence:
        reasons.append(
            "Train loss fell while eval loss rose, suggesting overtraining."
        )
    if val_f1_drop:
        reasons.append("Validation F1 dropped beyond the allowed threshold.")
    if synthetic_acceptance_rate is not None:
        reasons.append(
            "Synthetic quality gate accepted "
            f"{synthetic_candidates_accepted or 0}/{synthetic_candidates_seen} "
            f"candidates ({synthetic_acceptance_rate:.1%})."
        )
    if acceptance_quality_signal:
        top_reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(
                synthetic_rejection_reasons.items(),
                key=lambda item: (-item[1], item[0]),
            )[:3]
        )
        suffix = (
            f" Top rejection reasons: {top_reasons}." if top_reasons else ""
        )
        reasons.append(
            "Synthetic quality gate rejected too many candidates." + suffix
        )
    if accepted_candidate_quality_signal:
        minimum = _numeric(synthetic_accepted_candidate_quality.get("min"))
        average = _numeric(synthetic_accepted_candidate_quality.get("avg"))
        details = []
        if minimum is not None:
            details.append(f"min={minimum:.3f}")
        if average is not None:
            details.append(f"avg={average:.3f}")
        suffix = f" ({', '.join(details)})." if details else "."
        reasons.append(
            "Accepted synthetic candidate quality fell below "
            f"{min_accepted_candidate_quality:.2f}{suffix}"
        )
    if synthetic_mix_balance_signal:
        risk_level = str(
            synthetic_accepted_mix_balance.get("risk_level") or "unknown"
        )
        risk_reasons = ", ".join(
            str(reason)
            for reason in synthetic_accepted_mix_balance.get("risk_reasons")
            or []
        )
        top_merchant = synthetic_accepted_mix_balance.get("top_merchant")
        top_operation = synthetic_accepted_mix_balance.get("top_operation")
        top_merchant_share = _numeric(
            synthetic_accepted_mix_balance.get("top_merchant_share")
        )
        top_operation_share = _numeric(
            synthetic_accepted_mix_balance.get("top_operation_share")
        )
        details = []
        if top_merchant and top_merchant_share is not None:
            details.append(
                f"top merchant {top_merchant}={top_merchant_share:.1%}"
            )
        if top_operation and top_operation_share is not None:
            details.append(
                f"top operation {top_operation}={top_operation_share:.1%}"
            )
        if risk_reasons:
            details.append(f"reasons: {risk_reasons}")
        suffix = f" ({'; '.join(details)})." if details else "."
        reasons.append(
            "Accepted synthetic mix balance has "
            f"{risk_level} concentration risk{suffix}"
        )
    if synthetic_real_baseline_signal:
        count = _safe_int(
            synthetic_accepted_real_baseline_comparison.get("count")
        )
        below_count = _safe_int(
            synthetic_accepted_real_baseline_comparison.get(
                "below_real_score_range_count"
            )
        )
        within_share = _numeric(
            synthetic_accepted_real_baseline_comparison.get(
                "within_real_score_range_share"
            )
        )
        details = []
        if below_count is not None and count is not None:
            details.append(f"{below_count}/{count} below range")
        if within_share is not None:
            details.append(f"in-range share {within_share:.1%}")
        suffix = f" ({', '.join(details)})." if details else "."
        reasons.append(
            "Accepted synthetic examples fell outside the real receipt "
            f"structure baseline{suffix}"
        )

    if (
        target_improved
        and not overtraining_signal
        and not synthetic_quality_signal
    ):
        recommendation = "promote"
    elif target_improved and (overtraining_signal or synthetic_quality_signal):
        recommendation = "hold"
    else:
        recommendation = (
            "reject"
            if overtraining_signal or synthetic_quality_signal
            else "hold"
        )

    return SyntheticAugmentationAudit(
        baseline_job_id=baseline_job_id,
        augmented_job_id=augmented_job_id,
        recommendation=recommendation,
        synthetic_train_examples=augmented_metrics.get(
            "synthetic_train_examples"
        ),
        synthetic_candidates_seen=synthetic_candidates_seen,
        synthetic_candidates_accepted=synthetic_candidates_accepted,
        synthetic_candidates_rejected=synthetic_candidates_rejected,
        synthetic_acceptance_rate=synthetic_acceptance_rate,
        synthetic_rejection_reasons=synthetic_rejection_reasons,
        synthetic_accepted_candidate_quality=synthetic_accepted_candidate_quality,
        synthetic_accepted_candidate_quality_components=(
            synthetic_accepted_candidate_quality_components
        ),
        synthetic_accepted_mix_balance=synthetic_accepted_mix_balance,
        synthetic_accepted_real_baseline_comparison=(
            synthetic_accepted_real_baseline_comparison
        ),
        synthetic_mix_balance_signal=synthetic_mix_balance_signal,
        synthetic_real_baseline_signal=synthetic_real_baseline_signal,
        synthetic_quality_signal=synthetic_quality_signal,
        target_pair_count=len(pair_audits),
        improved_pair_count=improved_pair_count,
        total_before_count=total_before,
        total_after_count=total_after,
        total_relative_reduction=total_relative_reduction,
        val_f1_before=_numeric(baseline_metrics.get("val_f1")),
        val_f1_after=_numeric(augmented_metrics.get("val_f1")),
        val_f1_delta=val_f1_delta,
        train_loss_delta=train_loss_delta,
        eval_loss_delta=eval_loss_delta,
        overtraining_signal=overtraining_signal,
        reasons=reasons,
        pair_audits=pair_audits,
    )


def build_synthetic_augmentation_audit_from_dynamo(
    *,
    dynamo_client: Any,
    baseline_job_ref: str,
    augmented_job_ref: str,
    pattern_artifact: dict[str, Any] | None = None,
    synthetic_plan: dict[str, Any] | None = None,
    target_pairs: Iterable[Any] | None = None,
    min_confusion_reduction: float = 0.05,
    max_val_f1_drop: float = 0.005,
    min_synthetic_acceptance_rate: float = 0.5,
    min_accepted_candidate_quality: float = 0.75,
) -> SyntheticAugmentationAudit:
    """Build an augmentation audit directly from Dynamo metrics.

    ``pattern_artifact`` should be the pattern-builder JSON saved to S3, or
    ``synthetic_plan`` can be passed directly. The audit uses the plan's target
    pairs to decide whether the augmented run improved the exact confusions the
    synthetic receipts were created for.
    """
    selected_target_pairs = list(target_pairs or [])
    if not selected_target_pairs and synthetic_plan:
        selected_target_pairs = target_pairs_from_plan(synthetic_plan)
    if not selected_target_pairs and pattern_artifact:
        selected_target_pairs = target_pairs_from_pattern_artifact(
            pattern_artifact
        )
    if not selected_target_pairs:
        raise ValueError(
            "target_pairs, synthetic_plan, or a pattern artifact with "
            "synthetic_receipt_plan is required"
        )

    baseline_job_id, baseline_metrics = metrics_summary_from_dynamo_job(
        dynamo_client,
        baseline_job_ref,
    )
    augmented_job_id, augmented_metrics = metrics_summary_from_dynamo_job(
        dynamo_client,
        augmented_job_ref,
    )

    return build_synthetic_augmentation_audit(
        baseline_job_id=baseline_job_id,
        augmented_job_id=augmented_job_id,
        baseline_metrics=baseline_metrics,
        augmented_metrics=augmented_metrics,
        target_pairs=selected_target_pairs,
        min_confusion_reduction=min_confusion_reduction,
        max_val_f1_drop=max_val_f1_drop,
        min_synthetic_acceptance_rate=min_synthetic_acceptance_rate,
        min_accepted_candidate_quality=min_accepted_candidate_quality,
    )
