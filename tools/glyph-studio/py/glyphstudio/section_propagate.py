"""M1b: word-context section propagation via nearest-neighbor voting.

Sections are seeded sparsely (M0): only words whose line carries a
``ReceiptSection`` get a section. This propagates a section to *every* word by
nearest-neighbor voting in word-embedding space (the receipt_chroma ``words``
collection), turning the sparse seed into dense per-word coverage — within and
across receipts.

The core (:func:`propagate_knn`) is a pure array function so it is unit-testable
and callable per-receipt at runtime (the propagator must not be batch-only —
epic amendment #1067). The Chroma/DynamoDB loading lives in the CLI adapter
(``section_propagate_eval.py``).

Measured on the dev words snapshot (97,658 words, real OpenAI embeddings), with
whole-receipt hold-out so there is no same-line leakage: **89% cross-receipt
accuracy; 96.7% on the 79% of words at confidence >= 0.8.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from receipt_chroma import Propagation, propagate_knn


@dataclass
class PropagationEval:
    accuracy: float
    n_test: int
    per_section_recall: dict[
        str, tuple[int, float]
    ]  # section -> (support, recall)
    coverage_at: dict[float, tuple[float, float]]  # thresh -> (frac, acc)


def evaluate_cross_receipt(
    emb: np.ndarray,
    labels: Sequence[Optional[str]],
    receipt_ids: Sequence[str],
    k: int = 15,
    holdout_every: int = 5,
) -> PropagationEval:
    """Honest cross-receipt eval: hold out whole receipts, predict their labeled
    words from words in the other receipts (whole-receipt holdout avoids
    same-line leakage). Only words with a seed label participate.
    """
    emb = np.asarray(emb, dtype=np.float32)
    labels = np.asarray(labels, dtype=object)
    receipt_ids = np.asarray(receipt_ids, dtype=object)
    labeled = np.array([s is not None for s in labels])

    uniq = sorted(set(receipt_ids[labeled]))
    test_receipts = set(uniq[::holdout_every])
    is_test = np.array([r in test_receipts for r in receipt_ids])
    train_mask = labeled & ~is_test
    test_mask = labeled & is_test

    res = propagate_knn(
        emb[train_mask], labels[train_mask].astype(str), emb[test_mask], k=k
    )
    yte = labels[test_mask].astype(str)
    pred, conf = res.labels, res.confidence
    acc = float((pred == yte).mean()) if len(yte) else 0.0

    per_section: dict[str, tuple[int, float]] = {}
    for s in sorted(set(yte)):
        m = yte == s
        per_section[s] = (int(m.sum()), float((pred[m] == s).mean()))

    cov: dict[float, tuple[float, float]] = {}
    for th in (0.6, 0.8):
        hm = conf >= th
        cov[th] = (
            float(hm.mean()),
            float((pred[hm] == yte[hm]).mean()) if hm.any() else 0.0,
        )
    return PropagationEval(acc, int(test_mask.sum()), per_section, cov)
