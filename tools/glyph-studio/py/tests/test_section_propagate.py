"""Unit tests for KNN section propagation (synthetic embeddings, no snapshot)."""

import numpy as np
import pytest

from glyphstudio.section_propagate import (
    evaluate_cross_receipt,
    propagate_knn,
)


def _clustered(sections, per=20, dim=8, spread=0.05, seed=0):
    """Make embeddings clustered by section: each section = a random center."""
    rng = np.random.RandomState(seed)
    centers = {s: rng.randn(dim) for s in set(sections)}
    emb, labels = [], []
    for s in sections:
        for _ in range(per):
            emb.append(centers[s] + spread * rng.randn(dim))
            labels.append(s)
    return np.asarray(emb, dtype=np.float32), labels


def test_propagate_recovers_clustered_labels():
    secs = ["items", "payment", "total_line"]
    train_emb, train_labels = _clustered(secs, per=30, seed=1)
    # queries = actual payment seed points, lightly perturbed (same cluster)
    tl = np.asarray(train_labels)
    pay = train_emb[tl == "payment"]
    rng = np.random.RandomState(2)
    q_emb = pay[:10] + 0.02 * rng.randn(10, pay.shape[1]).astype(np.float32)
    res = propagate_knn(train_emb, train_labels, q_emb, k=10)
    assert (res.labels == "payment").mean() >= 0.9
    assert res.confidence.min() >= 0.0 and res.confidence.max() <= 1.0


def test_propagate_confidence_reflects_agreement():
    # a query exactly at a center should get high confidence (unanimous nbrs)
    train_emb, train_labels = _clustered(["a", "b"], per=40, spread=0.01, seed=3)
    center_a = train_emb[np.array(train_labels) == "a"].mean(0, keepdims=True)
    res = propagate_knn(train_emb, train_labels, center_a, k=10)
    assert res.labels[0] == "a"
    assert res.confidence[0] > 0.9


def test_propagate_rejects_empty_train():
    with pytest.raises(ValueError):
        propagate_knn(np.zeros((0, 8)), [], np.zeros((2, 8)))


def test_propagate_k_capped_to_train_size():
    train_emb, train_labels = _clustered(["x"], per=3, seed=4)
    res = propagate_knn(train_emb, train_labels, train_emb[:1], k=15)
    assert res.labels[0] == "x"


def test_evaluate_cross_receipt_holdout():
    # 10 receipts, each with words from 2 sections; clusters are separable, so
    # cross-receipt propagation should be highly accurate.
    rng = np.random.RandomState(5)
    centers = {"items": rng.randn(8), "payment": rng.randn(8)}
    emb, labels, receipts = [], [], []
    for rcpt in range(10):
        for s in ("items", "payment"):
            for _ in range(15):
                emb.append(centers[s] + 0.05 * rng.randn(8))
                labels.append(s)
                receipts.append(f"r{rcpt}")
    emb = np.asarray(emb, dtype=np.float32)
    res = evaluate_cross_receipt(emb, labels, receipts, k=10, holdout_every=5)
    assert res.n_test > 0
    assert res.accuracy >= 0.9
    assert set(res.per_section_recall) == {"items", "payment"}
    assert res.coverage_at[0.6][0] > 0  # some words above conf 0.6


def test_evaluate_ignores_unlabeled_words():
    rng = np.random.RandomState(6)
    c = rng.randn(8)
    emb = np.vstack([c + 0.05 * rng.randn(8) for _ in range(30)]).astype(np.float32)
    labels = ["items"] * 20 + [None] * 10   # 10 unlabeled -> excluded
    receipts = [f"r{i%5}" for i in range(30)]
    res = evaluate_cross_receipt(emb, labels, receipts, k=5)
    # only the 20 labeled words can be train/test
    assert res.n_test <= 20
