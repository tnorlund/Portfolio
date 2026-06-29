#!/usr/bin/env python3
"""reocr_score.py — score a re-OCR'd example by LABEL-PROPAGATION quality (review action #3, hardened).

The clean-data verifier penalizes OCR text noise (the feature). This scorer ignores text CONTENT but is now
strict about label QUALITY, after the codex/opus review showed the v1 region-membership scorer let garbage
pass ('='->TAX, 'B1'->TIME, GRAND_TOTAL on a value-corrupted token all counted as "correct"). Metrics:
  - region_label_recall : GT labeled tokens covered by a NON-DEGENERATE same-type OCR token
  - label_precision     : labeled OCR tokens that land in a same-type GT region / all labeled OCR tokens
  - false_positive_rate : labeled OCR tokens landing in a GT-'O' region (no GT label there)
  - mislabel_rate       : labeled OCR tokens landing in a DIFFERENT-type GT region
  - degenerate_labels   : labeled OCR tokens that are punctuation/symbol-only (e.g. '='->TAX) -- not credited
  - value_cer           : mean char-error-rate of OCR vs GT text on covered VALUE fields (how corrupted values are)
  - propagation_f1      : harmonic mean of recall & precision -- the headline (replaces the lenient score)

Usage: reocr_score.py <gt_bundle.json> <reocr_example.json> [candidate_index]
"""
from __future__ import annotations
import json, sys

VALUE_ENTS = {"LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"}


def _ent(t):
    return t[2:] if t and t[:2] in ("B-", "I-") else (None if t in (None, "O", "") else t)


def _norm(b):
    return [min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])]


def _over(a, b):  # intersection / area(b)
    a, b = _norm(a), _norm(b)
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return inter / max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))


def _degenerate(tok):
    return not any(c.isalnum() for c in (tok or ""))


def _cer(a, b):
    a, b = a or "", b or ""
    if not a and not b:
        return 0.0
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[m] / max(1, len(a))


def score_propagation(gt_tokens, gt_bboxes, gt_tags, re_tokens, re_bboxes, re_tags, cover=0.20):
    gt = [(tok, _norm(b), _ent(t)) for tok, b, t in zip(gt_tokens, gt_bboxes, gt_tags)]
    gt_lab = [(tok, b, e) for tok, b, e in gt if e]
    re_lab = [(tok, _norm(b), _ent(t)) for tok, b, t in zip(re_tokens, re_bboxes, re_tags) if _ent(t)]

    # ---- recall: each GT labeled token covered by a NON-DEGENERATE same-type OCR token ----
    covered_types = set()
    covered = 0
    value_cers = []
    for gtok, gb, ge in gt_lab:
        hit = None
        for rtok, rb, re_ in re_lab:
            if re_ == ge and not _degenerate(rtok) and (_over(rb, gb) >= cover or _over(gb, rb) >= cover):
                hit = rtok
                break
        if hit is not None:
            covered += 1
            covered_types.add(ge)
            if ge in VALUE_ENTS:
                value_cers.append(_cer(gtok, hit))
        elif ge in VALUE_ENTS:
            value_cers.append(1.0)  # uncovered value token = max error (no selection bias)
    n_gt = max(1, len(gt_lab))
    recall = covered / n_gt

    # ---- precision / FP / mislabel / degenerate over labeled OCR tokens ----
    tp = fp = mis = degen = 0
    for rtok, rb, re_ in re_lab:
        if _degenerate(rtok):
            # A supervised label on a punctuation/symbol-only token is a FALSE label that poisons
            # training (e.g. '='->TAX). Count it as a failure, not a free skip.
            degen += 1
            fp += 1
            continue
        same = any(ge == re_ and _over(rb, gb) >= cover for _, gb, ge in gt_lab)
        diff = any(ge != re_ and _over(rb, gb) >= cover for _, gb, ge in gt_lab)
        if same:
            tp += 1
        elif diff:
            mis += 1
        else:
            fp += 1
    n_re = max(1, tp + fp + mis)
    precision = tp / n_re
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    gt_types = {e for _, _, e in gt_lab}
    return {
        "region_label_recall": round(recall, 3),
        "label_precision": round(precision, 3),
        "false_positive_rate": round(fp / n_re, 3),
        "mislabel_rate": round(mis / n_re, 3),
        "degenerate_labels": degen,
        "value_cer": round(sum(value_cers) / len(value_cers), 3) if value_cers else None,
        "propagation_f1": round(f1, 3),
        "type_recall": round(len(covered_types) / max(1, len(gt_types)), 3),
        "missing_types": sorted(gt_types - covered_types),
    }


def main():
    gt = json.load(open(sys.argv[1]))["synthetic_training_examples"][int(sys.argv[3]) if len(sys.argv) > 3 else 0]
    re = json.load(open(sys.argv[2]))
    print(json.dumps(score_propagation(gt["tokens"], gt["bboxes"], gt["ner_tags"],
                                       re["tokens"], re["bboxes"], re["ner_tags"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
