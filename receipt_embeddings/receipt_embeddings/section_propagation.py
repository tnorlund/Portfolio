"""Pure NumPy section propagation shared by research and runtime callers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def _l2_normalize(values: np.ndarray) -> np.ndarray:
    return values / (np.linalg.norm(values, axis=1, keepdims=True) + 1e-8)


@dataclass(frozen=True)
class Propagation:
    """Per-query nearest-neighbor result."""

    labels: np.ndarray
    confidence: np.ndarray


def propagate_knn(
    train_embeddings: np.ndarray,
    train_labels: Sequence[str],
    query_embeddings: np.ndarray,
    k: int = 15,
    normalize: bool = True,
) -> Propagation:
    """Assign sections with non-negative cosine-weighted KNN voting."""

    train = np.asarray(train_embeddings, dtype=np.float32)
    query = np.asarray(query_embeddings, dtype=np.float32)
    labels = np.asarray(train_labels, dtype=object)
    if train.ndim != 2 or query.ndim != 2:
        raise ValueError("train and query embeddings must be two-dimensional")
    if train.shape[0] == 0:
        raise ValueError("no seed (train) embeddings")
    if train.shape[0] != labels.shape[0]:
        raise ValueError("train embeddings and labels must have equal length")
    if train.shape[1] != query.shape[1]:
        raise ValueError("train and query embedding dimensions must match")
    neighbors = min(k, train.shape[0])
    if neighbors <= 0:
        raise ValueError("k must be positive")
    if normalize:
        train = _l2_normalize(train)
        query = _l2_normalize(query)

    similarities = query @ train.T
    indices = np.argpartition(-similarities, kth=neighbors - 1, axis=1)[
        :, :neighbors
    ]
    predicted = np.empty(query.shape[0], dtype=object)
    confidence = np.empty(query.shape[0], dtype=np.float32)
    for row_index, row_neighbors in enumerate(indices):
        votes: dict[str, float] = defaultdict(float)
        total = 0.0
        for neighbor in row_neighbors:
            weight = max(float(similarities[row_index, neighbor]), 0.0)
            votes[str(labels[neighbor])] += weight
            total += weight
        winner, winning_weight = max(
            votes.items(), key=lambda item: (item[1], item[0])
        )
        predicted[row_index] = winner
        confidence[row_index] = winning_weight / (total + 1e-8)
    return Propagation(predicted, confidence)


__all__ = ["Propagation", "propagate_knn"]
