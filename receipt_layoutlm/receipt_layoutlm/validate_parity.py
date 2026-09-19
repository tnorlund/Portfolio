"""Shared numerical parity helpers for Core ML / Core AI validators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from receipt_layoutlm.exceptions import NonFiniteLogitsError


@dataclass
class ValidationResult:
    """Results from comparing PyTorch vs an exported backend."""

    total_tokens: int
    matching_labels: int
    label_agreement_rate: float
    per_label_agreement: Dict[str, Tuple[int, int, float]]
    avg_confidence_diff: float
    max_confidence_diff: float
    avg_logit_rmse: float
    max_logit_rmse: float
    mismatches: List[Dict[str, Any]]
    backend_name: str = "backend"
    nan_inf_detected: bool = False
    nan_inf_count: int = 0

    def __str__(self) -> str:
        title = f"{self.backend_name} Validation Results"
        lines = [
            "=" * 60,
            title,
            "=" * 60,
            f"Total tokens evaluated: {self.total_tokens}",
            (
                f"Label agreement rate:   {self.label_agreement_rate:.4f} "
                f"({self.matching_labels}/{self.total_tokens})"
            ),
            "",
            "Confidence comparison:",
            f"  Average diff: {self.avg_confidence_diff:.6f}",
            f"  Max diff:     {self.max_confidence_diff:.6f}",
            "",
            "Logit RMSE (before softmax):",
            f"  Average: {self.avg_logit_rmse:.6f}",
            f"  Max:     {self.max_logit_rmse:.6f}",
            "",
            f"NaN/Inf detected: {self.nan_inf_detected} "
            f"(count={self.nan_inf_count})",
            "",
            "Per-label agreement:",
        ]
        for label, (matches, total, rate) in sorted(
            self.per_label_agreement.items()
        ):
            lines.append(f"  {label:20s}: {rate:.4f} ({matches}/{total})")

        if self.mismatches:
            lines.append("")
            lines.append(
                f"Sample mismatches (showing first 10 of "
                f"{len(self.mismatches)}):"
            )
            other_key = f"{self.backend_name.lower()}_label"
            other_conf = f"{self.backend_name.lower()}_conf"
            for m in self.mismatches[:10]:
                lines.append(
                    f"  '{m['token']}': PyTorch={m['pytorch_label']} "
                    f"({m['pytorch_conf']:.3f}) vs "
                    f"{self.backend_name}={m.get(other_key, m.get('backend_label'))} "
                    f"({m.get(other_conf, m.get('backend_conf', 0.0)):.3f})"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self, max_mismatches: int = 100) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "backend_name": self.backend_name,
            "total_tokens": self.total_tokens,
            "matching_labels": self.matching_labels,
            "label_agreement_rate": self.label_agreement_rate,
            "per_label_agreement": {
                k: {"matches": v[0], "total": v[1], "rate": v[2]}
                for k, v in self.per_label_agreement.items()
            },
            "avg_confidence_diff": self.avg_confidence_diff,
            "max_confidence_diff": self.max_confidence_diff,
            "avg_logit_rmse": self.avg_logit_rmse,
            "max_logit_rmse": self.max_logit_rmse,
            "nan_inf_detected": self.nan_inf_detected,
            "nan_inf_count": self.nan_inf_count,
            "num_mismatches": len(self.mismatches),
            "mismatches": self.mismatches[:max_mismatches],
        }


def softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last axis."""
    exp_x = np.exp(x - np.max(x))
    return exp_x / exp_x.sum()


def count_nonfinite(logits: np.ndarray) -> int:
    """Return the number of NaN/Inf values in ``logits``."""
    arr = np.asarray(logits)
    return int(np.isnan(arr).sum() + np.isinf(arr).sum())


def assert_finite_logits(logits: np.ndarray, backend: str) -> None:
    """Raise if any logit value is NaN or Inf."""
    bad = count_nonfinite(logits)
    if bad:
        raise NonFiniteLogitsError(backend, bad)


def aggregate_word_predictions(
    logits: np.ndarray,
    word_ids: Sequence[Optional[int]],
    num_words: int,
    id2label: Mapping[Any, str],
) -> Tuple[List[str], List[float], List[List[float]]]:
    """Mean-pool subtoken logits per word (matches validate_coreml)."""
    word_to_tokens: Dict[int, List[int]] = {}
    for i, wid in enumerate(word_ids):
        if wid is not None:
            word_to_tokens.setdefault(wid, []).append(i)

    labels: List[str] = []
    confs: List[float] = []
    word_logits: List[List[float]] = []
    num_classes = logits.shape[-1]

    for wid in range(num_words):
        token_idxs = word_to_tokens.get(wid, [])
        if not token_idxs:
            labels.append("O")
            confs.append(0.0)
            word_logits.append([0.0] * num_classes)
            continue

        avg_logits = np.mean([logits[i] for i in token_idxs], axis=0)
        probs = softmax(avg_logits)
        pred_id = int(np.argmax(probs))
        labels.append(id2label.get(pred_id, id2label.get(str(pred_id), "O")))
        confs.append(float(probs[pred_id]))
        word_logits.append(avg_logits.tolist())

    return labels, confs, word_logits


def compare_predictions(
    *,
    tokens: Sequence[str],
    pytorch_labels: Sequence[str],
    backend_labels: Sequence[str],
    pytorch_confs: Sequence[float],
    backend_confs: Sequence[float],
    pytorch_logits: Optional[Sequence[Sequence[float]]],
    backend_logits: Optional[Sequence[Sequence[float]]],
    backend_name: str,
    raise_on_nonfinite: bool = True,
) -> Dict[str, Any]:
    """Compare one sample's PyTorch vs backend predictions.

    Returns a dict with per-token lists plus logit RMSEs and NaN counts.
    """
    mismatches: List[Dict[str, Any]] = []
    label_key = f"{backend_name.lower()}_label"
    conf_key = f"{backend_name.lower()}_conf"

    for tok, pt_lbl, be_lbl, pt_conf, be_conf in zip(
        tokens,
        pytorch_labels,
        backend_labels,
        pytorch_confs,
        backend_confs,
    ):
        if pt_lbl != be_lbl:
            mismatches.append(
                {
                    "token": tok,
                    "pytorch_label": pt_lbl,
                    label_key: be_lbl,
                    "backend_label": be_lbl,
                    "pytorch_conf": pt_conf,
                    conf_key: be_conf,
                    "backend_conf": be_conf,
                }
            )

    logit_rmses: List[float] = []
    nan_inf_count = 0
    if pytorch_logits is not None and backend_logits is not None:
        pt_arr = np.asarray(pytorch_logits, dtype=np.float64)
        be_arr = np.asarray(backend_logits, dtype=np.float64)
        nan_inf_count = count_nonfinite(pt_arr) + count_nonfinite(be_arr)
        if raise_on_nonfinite:
            assert_finite_logits(pt_arr, "PyTorch")
            assert_finite_logits(be_arr, backend_name)
        min_len = min(len(pt_arr), len(be_arr))
        for pt_log, be_log in zip(pt_arr[:min_len], be_arr[:min_len]):
            rmse = float(np.sqrt(np.mean((pt_log - be_log) ** 2)))
            logit_rmses.append(rmse)

    return {
        "mismatches": mismatches,
        "logit_rmses": logit_rmses,
        "nan_inf_count": nan_inf_count,
        "pytorch_labels": list(pytorch_labels),
        "backend_labels": list(backend_labels),
        "pytorch_confs": list(pytorch_confs),
        "backend_confs": list(backend_confs),
    }


def summarize_comparisons(
    *,
    all_pytorch_labels: Sequence[str],
    all_backend_labels: Sequence[str],
    all_pytorch_confs: Sequence[float],
    all_backend_confs: Sequence[float],
    all_logit_rmses: Sequence[float],
    mismatches: List[Dict[str, Any]],
    backend_name: str,
    nan_inf_count: int = 0,
) -> ValidationResult:
    """Aggregate per-sample comparison results into a ValidationResult."""
    total = len(all_pytorch_labels)
    matches = sum(
        1 for p, c in zip(all_pytorch_labels, all_backend_labels) if p == c
    )

    per_label: Dict[str, Tuple[int, int, float]] = {}
    for label in set(all_pytorch_labels):
        label_matches = sum(
            1
            for p, c in zip(all_pytorch_labels, all_backend_labels)
            if p == label and p == c
        )
        label_total = sum(1 for p in all_pytorch_labels if p == label)
        rate = label_matches / label_total if label_total > 0 else 0.0
        per_label[label] = (label_matches, label_total, rate)

    conf_diffs = [
        abs(p - c) for p, c in zip(all_pytorch_confs, all_backend_confs)
    ]

    return ValidationResult(
        total_tokens=total,
        matching_labels=matches,
        label_agreement_rate=matches / total if total > 0 else 0.0,
        per_label_agreement=per_label,
        avg_confidence_diff=float(np.mean(conf_diffs)) if conf_diffs else 0.0,
        max_confidence_diff=max(conf_diffs) if conf_diffs else 0.0,
        avg_logit_rmse=(
            float(np.mean(all_logit_rmses)) if all_logit_rmses else 0.0
        ),
        max_logit_rmse=max(all_logit_rmses) if all_logit_rmses else 0.0,
        mismatches=mismatches,
        backend_name=backend_name,
        nan_inf_detected=nan_inf_count > 0,
        nan_inf_count=nan_inf_count,
    )
