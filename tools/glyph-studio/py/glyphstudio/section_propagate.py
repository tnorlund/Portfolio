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

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-8)


@dataclass
class Propagation:
    """Per-query propagation result."""

    labels: np.ndarray       # (n_query,) predicted section per query word
    confidence: np.ndarray   # (n_query,) winning-vote share in [0, 1]


def propagate_knn(
    train_emb: np.ndarray,
    train_labels: Sequence[str],
    query_emb: np.ndarray,
    k: int = 15,
    normalize: bool = True,
) -> Propagation:
    """Propagate a section to each query word by cosine-weighted KNN vote.

    Parameters
    ----------
    train_emb : (n_train, d) seed word embeddings (labeled).
    train_labels : (n_train,) each seed word's section.
    query_emb : (n_query, d) words to label.
    k : neighbors per query.
    normalize : L2-normalize rows so inner product == cosine similarity.

    Returns a :class:`Propagation` with the winning section and its vote share
    (a usable confidence — high share means the neighborhood agrees).
    """
    train_emb = np.asarray(train_emb, dtype=np.float32)
    query_emb = np.asarray(query_emb, dtype=np.float32)
    train_labels = np.asarray(train_labels, dtype=object)
    if train_emb.shape[0] == 0:
        raise ValueError("no seed (train) embeddings")
    k = min(k, train_emb.shape[0])
    if normalize:
        train_emb = _l2_normalize(train_emb)
        query_emb = _l2_normalize(query_emb)

    # cosine similarity == inner product on normalized rows
    sims = query_emb @ train_emb.T  # (n_query, n_train)
    # top-k neighbors per query
    nbr = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]

    labels = np.empty(query_emb.shape[0], dtype=object)
    confidence = np.empty(query_emb.shape[0], dtype=np.float32)
    for i in range(query_emb.shape[0]):
        votes: dict[str, float] = defaultdict(float)
        total = 0.0
        for j in nbr[i]:
            w = max(float(sims[i, j]), 0.0)
            votes[train_labels[j]] += w
            total += w
        best = max(votes.items(), key=lambda kv: kv[1])
        labels[i] = best[0]
        confidence[i] = best[1] / (total + 1e-8)
    return Propagation(labels=labels, confidence=confidence)


@dataclass
class PropagationEval:
    accuracy: float
    n_test: int
    per_section_recall: dict[str, tuple[int, float]]  # section -> (support, recall)
    coverage_at: dict[float, tuple[float, float]]      # thresh -> (frac, acc)


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
