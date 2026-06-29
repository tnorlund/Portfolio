#!/usr/bin/env python3
"""reocr_score.py — score a re-OCR'd training example by LABEL-PROPAGATION success, NOT text cleanliness.

The clean-data verifier (verify_candidates) penalizes OCR noise (e.g. 'asterar' as an invalid
PAYMENT_METHOD) — but for re-OCR'd data that noise is the FEATURE. So this scorer ignores text content
and asks only: did each ground-truth labeled region get the RIGHT entity carried onto the OCR tokens?

Metrics (all 0..1), comparing GT (synthesized) vs the re-OCR'd output, both in the 0..1000 y-high-top frame:
  - type_recall          : GT entity types that survived into the output / all GT entity types
  - region_label_recall  : GT labeled tokens whose box is covered by an OCR token with the SAME entity
  - region_mislabel_rate : GT labeled tokens covered by an OCR token with a DIFFERENT (non-O) entity
  - propagation_score    : region_label_recall * (1 - region_mislabel_rate)  (the headline)

Usage (standalone): reocr_score.py <gt_bundle.json> <reocr_example.json> [candidate_index]
"""
from __future__ import annotations
import json, sys


def _ent(tag):
    return tag[2:] if tag and tag[:2] in ("B-", "I-") else (None if tag in (None, "O", "") else tag)


def _inter_over_a(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    aa = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / aa


def score_propagation(gt_bboxes, gt_tags, re_bboxes, re_tags, cover=0.20):
    """bboxes are [x0,y0,x1,y1] in the same frame. Returns the metric dict."""
    gt = [(b, _ent(t)) for b, t in zip(gt_bboxes, gt_tags) if _ent(t)]
    re = [(b, _ent(t)) for b, t in zip(re_bboxes, re_tags)]
    gt_types = {e for _, e in gt}
    re_types = {e for _, e in re if e}
    covered = mislabeled = 0
    for gb, ge in gt:
        overlappers = [re_e for rb, re_e in re if _inter_over_a(_norm(gb), _norm(rb)) >= cover or
                       _inter_over_a(_norm(rb), _norm(gb)) >= cover]
        if any(e == ge for e in overlappers):
            covered += 1
        elif any(e is not None for e in overlappers):
            mislabeled += 1
    n = max(1, len(gt))
    rlr = covered / n
    mis = mislabeled / n
    return {
        "type_recall": round(len(gt_types & re_types) / max(1, len(gt_types)), 3),
        "region_label_recall": round(rlr, 3),
        "region_mislabel_rate": round(mis, 3),
        "propagation_score": round(rlr * (1 - mis), 3),
        "gt_types": sorted(gt_types),
        "missing_types": sorted(gt_types - re_types),
    }


def _norm(b):
    return [min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])]


def main():
    gt_ex = json.load(open(sys.argv[1]))["synthetic_training_examples"][int(sys.argv[3]) if len(sys.argv) > 3 else 0]
    re_ex = json.load(open(sys.argv[2]))
    m = score_propagation(gt_ex["bboxes"], gt_ex["ner_tags"], re_ex["bboxes"], re_ex["ner_tags"])
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
