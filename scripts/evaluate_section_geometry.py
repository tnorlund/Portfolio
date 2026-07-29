#!/usr/bin/env python3
"""Evaluate structure and Chroma projection evidence for receipt sections.

This is an offline, read-only experiment.  It consumes the repository's local
analytics cache, learns only from QA-VALID section rows, splits by receipt,
and compares the existing semi-Markov decoder with three supplemental signals:

* geometry + arithmetic: a regularized diagonal class model over row layout,
  price-column alignment, amount magnitude, and running-sum residuals;
* embedding projection: cosine projection onto section centroids; and
* embedding neighborhood: cosine-weighted KNN votes.

No DynamoDB or S3 writes are performed.  Chroma snapshots affected by the
metadata-only vector-checkpoint lag are repaired only in a separate local copy
and only after proving that every skipped log record is a vector-free UPDATE.
"""

# Monorepo sibling paths must be installed before runtime imports.
# pylint: disable=wrong-import-position,too-many-locals

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import fmean, median
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_chroma", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _package))

from receipt_chroma import ChromaClient  # noqa: E402
from receipt_dynamo import (  # noqa: E402
    item_to_receipt_line,
    item_to_receipt_row,
    item_to_receipt_section,
)
from receipt_dynamo.constants import ValidationStatus  # noqa: E402
from receipt_upload.section_assignment import (  # noqa: E402
    RowFeatures,
    assign_feature_sections,
    extract_row_features,
    learn_prior,
)

_AMOUNT_RE = re.compile(
    r"^\(?\s*([-+])?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([-])?\s*\)?$"
)
_EPSILON = 1e-8
_KNN_NEIGHBORS = 15
_WEIGHT_CHOICES = (0.0, 0.25, 0.5, 1.0)


@dataclass(frozen=True)
class ReceiptCase:
    """One receipt and its observable/truth evidence."""

    image_id: str
    receipt_id: int
    merchant: str
    features: tuple[RowFeatures, ...]
    truth: Mapping[int, str]
    structure: np.ndarray

    @property
    def key(self) -> tuple[str, int]:
        return self.image_id, self.receipt_id


@dataclass(frozen=True)
class EvidenceWeights:
    """Fusion weights selected on the validation receipt split."""

    geometry_math: float = 0.0
    embedding_projection: float = 0.0
    embedding_knn: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "geometry_math": self.geometry_math,
            "embedding_projection": self.embedding_projection,
            "embedding_knn": self.embedding_knn,
        }


@dataclass(frozen=True)
class CaseEvidence:
    """Section-aligned supplemental scores for one receipt."""

    geometry_math: np.ndarray
    embedding_projection: np.ndarray
    embedding_knn: np.ndarray
    embedding_available: np.ndarray


@dataclass(frozen=True)
class EvidenceModel:
    """Training-split geometry and embedding projection model."""

    sections: tuple[str, ...]
    structure_mean: np.ndarray
    structure_scale: np.ndarray
    class_structure_mean: np.ndarray
    class_structure_scale: np.ndarray
    embedding_centroids: np.ndarray | None
    train_embeddings: np.ndarray | None
    train_embedding_labels: np.ndarray | None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=_REPO_ROOT / ".cache" / "analytics" / "dev",
    )
    parser.add_argument(
        "--working-root",
        type=Path,
        default=_REPO_ROOT / ".cache" / "section-geometry",
        help="Ignored local directory for a guarded Chroma read copy",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--max-receipts",
        type=int,
        help="Deterministic receipt cap for quick smoke experiments",
    )
    parser.add_argument("--seed", default="section-geometry-v1")
    parser.add_argument("--knn-neighbors", type=int, default=_KNN_NEIGHBORS)
    return parser.parse_args()


def _wire_items(
    connection: sqlite3.Connection,
    entity_type: str,
    image_id: str | None = None,
    receipt_id: int | None = None,
) -> list[dict[str, Any]]:
    clauses = ["entity_type = ?"]
    parameters: list[Any] = [entity_type]
    if image_id is not None:
        clauses.append("image_id = ?")
        parameters.append(image_id)
    if receipt_id is not None:
        clauses.append("receipt_id = ?")
        parameters.append(receipt_id)
    rows = connection.execute(
        "SELECT dynamodb_json FROM dynamo_items WHERE " + " AND ".join(clauses),
        parameters,
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _row_label(row: Any, line_sections: Mapping[int, set[str]]) -> str | None:
    votes = Counter(
        section
        for line_id in row.line_ids
        if line_id in line_sections
        for section in line_sections[line_id]
    )
    if not votes:
        return None
    maximum = max(votes.values())
    leaders = sorted(label for label, count in votes.items() if count == maximum)
    return leaders[0] if len(leaders) == 1 else None


def _parse_amount(value: str | None) -> float | None:
    if not value:
        return None
    match = _AMOUNT_RE.fullmatch(value.strip())
    if not match:
        return None
    amount = float(match.group(2).replace(",", ""))
    if match.group(1) == "-" or match.group(3) == "-":
        amount *= -1
    return amount


def _structure_matrix(features: Sequence[RowFeatures]) -> np.ndarray:
    """Build scale-free geometry and arithmetic row features."""

    if not features:
        return np.empty((0, 16), dtype=np.float32)
    rows = [feature.row for feature in features]
    heights = np.asarray(
        [max(row.y_max - row.y_min, _EPSILON) for row in rows],
        dtype=np.float32,
    )
    typical_height = max(float(median(heights.tolist())), _EPSILON)
    amounts = [_parse_amount(row.amount_text) for row in rows]
    maximum_amount = max((abs(value) for value in amounts if value), default=1.0)
    amount_flags = np.asarray(
        [float(value is not None) for value in amounts], dtype=np.float32
    )
    matrix: list[list[float]] = []
    prior_amounts: list[float] = []
    for index, (feature, amount) in enumerate(zip(features, amounts, strict=True)):
        row = feature.row
        gap_above = 0.0
        if index:
            gap_above = max(0.0, rows[index - 1].y_min - row.y_max)
        gap_below = 0.0
        if index + 1 < len(rows):
            gap_below = max(0.0, row.y_min - rows[index + 1].y_max)
        arithmetic_fit = 1.0
        if amount is not None and abs(amount) > _EPSILON and prior_amounts:
            window_sums = [
                sum(prior_amounts[-width:])
                for width in range(1, min(len(prior_amounts), 12) + 1)
            ]
            arithmetic_fit = min(
                min(
                    abs(abs(amount) - candidate) / max(abs(amount), candidate, _EPSILON)
                    for candidate in window_sums
                ),
                1.0,
            )
        radius_start = max(0, index - 2)
        radius_end = min(len(rows), index + 3)
        local_amount_density = float(np.mean(amount_flags[radius_start:radius_end]))
        price_offset = 0.0
        if row.price_column_x is not None:
            price_offset = row.x_max - row.price_column_x
        matrix.append(
            [
                feature.position,
                (row.y_min + row.y_max) / 2,
                math.log1p((row.y_max - row.y_min) / typical_height),
                row.x_min,
                row.x_max,
                (row.x_min + row.x_max) / 2,
                row.x_max - row.x_min,
                math.log1p(gap_above / typical_height),
                math.log1p(gap_below / typical_height),
                float(amount is not None),
                float(row.amount_text is not None),
                price_offset,
                (
                    math.log1p(abs(amount)) / math.log1p(maximum_amount)
                    if amount is not None
                    else 0.0
                ),
                float(amount is not None and amount < 0),
                1.0 - arithmetic_fit if amount is not None else 0.0,
                local_amount_density,
            ]
        )
        if amount is not None:
            prior_amounts.append(abs(amount))
    return np.asarray(matrix, dtype=np.float32)


def load_cases(db_path: Path, max_receipts: int | None = None) -> list[ReceiptCase]:
    """Load the QA-VALID receipt corpus from the SQLite mirror."""

    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        valid_sections = [
            item_to_receipt_section(item)
            for item in _wire_items(connection, "RECEIPT_SECTION")
            if item.get("validation_status", {}).get("S")
            == ValidationStatus.VALID.value
        ]
        sections_by_receipt: dict[tuple[str, int], list[Any]] = defaultdict(list)
        for section in valid_sections:
            sections_by_receipt[(section.image_id, section.receipt_id)].append(section)
        keys = sorted(sections_by_receipt)
        if max_receipts is not None:
            keys = keys[:max_receipts]

        result: list[ReceiptCase] = []
        for image_id, receipt_id in keys:
            rows = sorted(
                (
                    item_to_receipt_row(item)
                    for item in _wire_items(
                        connection, "RECEIPT_ROW", image_id, receipt_id
                    )
                ),
                key=lambda row: (-(row.y_min + row.y_max), row.row_id),
            )
            lines = [
                item_to_receipt_line(item)
                for item in _wire_items(
                    connection, "RECEIPT_LINE", image_id, receipt_id
                )
            ]
            if not rows or not lines:
                continue
            line_sections: dict[int, set[str]] = defaultdict(set)
            for section in sections_by_receipt[(image_id, receipt_id)]:
                for line_id in section.line_ids:
                    line_sections[line_id].add(str(section.section_type))
            truth = {
                row.row_id: label
                for row in rows
                if (label := _row_label(row, line_sections)) is not None
            }
            if not truth:
                continue
            place_rows = connection.execute(
                """SELECT item_json FROM dynamo_items
                   WHERE entity_type = 'RECEIPT_PLACE'
                     AND image_id = ? AND receipt_id = ? LIMIT 1""",
                (image_id, receipt_id),
            ).fetchall()
            merchant = ""
            if place_rows:
                merchant = str(json.loads(place_rows[0][0]).get("merchant_name", ""))
            features = tuple(extract_row_features(rows, lines))
            result.append(
                ReceiptCase(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant=merchant,
                    features=features,
                    truth=truth,
                    structure=_structure_matrix(features),
                )
            )
    return result


def _chroma_count(path: Path) -> int:
    with ChromaClient(persist_directory=str(path), mode="read") as client:
        return int(client.get_collection("lines").count())


def repair_metadata_only_vector_lag(db_path: Path) -> dict[str, int]:
    """Advance a copied vector checkpoint over proven metadata-only updates.

    Chroma 1.3.6 can fail to open an atomically downloaded snapshot when its
    vector segment checkpoint trails metadata-only UPDATE log records whose
    vectors are NULL.  Those records cannot affect the HNSW vector segment.
    Every safety predicate is checked in one immediate transaction before the
    single checkpoint update is allowed.
    """

    with sqlite3.connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        segments = connection.execute("""SELECT s.id, s.scope, m.seq_id
               FROM segments s JOIN max_seq_id m ON m.segment_id = s.id
               WHERE s.scope IN ('VECTOR', 'METADATA')""").fetchall()
        by_scope = {
            scope: (segment_id, int(seq_id)) for segment_id, scope, seq_id in segments
        }
        if set(by_scope) != {"VECTOR", "METADATA"}:
            raise RuntimeError("Expected exactly one VECTOR and METADATA checkpoint")
        vector_id, vector_seq = by_scope["VECTOR"]
        _, metadata_seq = by_scope["METADATA"]
        if vector_seq >= metadata_seq:
            connection.rollback()
            return {"from_seq": vector_seq, "to_seq": vector_seq, "updates": 0}
        rows = connection.execute(
            """SELECT seq_id, operation, vector IS NULL
               FROM embeddings_queue
               WHERE seq_id > ? AND seq_id <= ? ORDER BY seq_id""",
            (vector_seq, metadata_seq),
        ).fetchall()
        expected = metadata_seq - vector_seq
        if len(rows) != expected:
            raise RuntimeError(
                "Unsafe Chroma repair: vector-lag log range is not contiguous"
            )
        if any(
            int(seq_id) != vector_seq + index
            or int(operation) != 1
            or int(vector_is_null) != 1
            for index, (seq_id, operation, vector_is_null) in enumerate(rows, start=1)
        ):
            raise RuntimeError(
                "Unsafe Chroma repair: lag contains a vector write, delete, "
                "or non-UPDATE operation"
            )
        cursor = connection.execute(
            "UPDATE max_seq_id SET seq_id = ? WHERE segment_id = ? AND seq_id = ?",
            (metadata_seq, vector_id, vector_seq),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Chroma vector checkpoint changed during repair")
        connection.commit()
        return {
            "from_seq": vector_seq,
            "to_seq": metadata_seq,
            "updates": len(rows),
        }


def prepare_chroma_lines(source: Path, working: Path) -> tuple[Path, dict[str, Any]]:
    """Return a readable line snapshot, repairing only a separate copy."""

    try:
        count = _chroma_count(source)
        return source, {"copied": False, "repair": None, "vector_count": count}
    except Exception as error:  # the exact Chroma exception changed across 1.x
        if (
            "backfill" not in str(error).casefold()
            or "log" not in str(error).casefold()
        ):
            raise
        if not working.exists():
            working.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, working)
        repair = repair_metadata_only_vector_lag(working / "chroma.sqlite3")
        count = _chroma_count(working)
        return working, {
            "copied": True,
            "source_error": str(error),
            "repair": repair,
            "vector_count": count,
        }


def _chroma_id(case: ReceiptCase, row_id: int) -> str:
    return f"IMAGE#{case.image_id}#RECEIPT#{case.receipt_id:05d}" f"#LINE#{row_id:05d}"


def load_embeddings(
    path: Path, cases: Sequence[ReceiptCase], batch_size: int = 1000
) -> tuple[dict[tuple[str, int, int], np.ndarray], dict[str, int]]:
    """Load exact row vectors; no nearest-neighbor query or metadata is used."""

    requested: dict[str, tuple[str, int, int]] = {}
    for case in cases:
        for feature in case.features:
            requested[_chroma_id(case, feature.row.row_id)] = (
                case.image_id,
                case.receipt_id,
                feature.row.row_id,
            )
    result: dict[tuple[str, int, int], np.ndarray] = {}
    ids = sorted(requested)
    with ChromaClient(persist_directory=str(path), mode="read") as client:
        collection = client.get_collection("lines")
        for offset in range(0, len(ids), batch_size):
            payload = collection.get(
                ids=ids[offset : offset + batch_size], include=["embeddings"]
            )
            embeddings = payload.get("embeddings")
            if embeddings is None:
                continue
            for chroma_id, embedding in zip(payload["ids"], embeddings, strict=True):
                result[requested[chroma_id]] = np.asarray(embedding, dtype=np.float32)
    return result, {"requested": len(ids), "found": len(result)}


def _split_value(case: ReceiptCase, seed: str) -> int:
    payload = f"{seed}:{case.image_id}:{case.receipt_id}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % 100


def split_cases(
    cases: Sequence[ReceiptCase], seed: str
) -> tuple[list[ReceiptCase], list[ReceiptCase], list[ReceiptCase]]:
    train, validation, test = [], [], []
    for case in cases:
        value = _split_value(case, seed)
        if value < 60:
            train.append(case)
        elif value < 80:
            validation.append(case)
        else:
            test.append(case)
    return train, validation, test


def _decoder_model(cases: Sequence[ReceiptCase]) -> dict[str, Any]:
    labeled = [
        [
            (feature, case.truth[feature.row.row_id])
            for feature in case.features
            if feature.row.row_id in case.truth
        ]
        for case in cases
    ]
    labeled = [receipt for receipt in labeled if receipt]
    return {
        "schema_version": "geometry-experiment-v1",
        "global": learn_prior(labeled),
        "merchants": {},
    }


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    return values / (np.linalg.norm(values, axis=1, keepdims=True) + _EPSILON)


def fit_evidence_model(
    cases: Sequence[ReceiptCase],
    embeddings: Mapping[tuple[str, int, int], np.ndarray],
    sections: Sequence[str],
) -> EvidenceModel:
    """Fit regularized class geometry and embedding centroids."""

    section_tuple = tuple(sections)
    section_index = {section: index for index, section in enumerate(section_tuple)}
    structures: list[np.ndarray] = []
    labels: list[int] = []
    embedding_rows: list[np.ndarray] = []
    embedding_labels: list[int] = []
    for case in cases:
        for row_index, feature in enumerate(case.features):
            label = case.truth.get(feature.row.row_id)
            if label not in section_index:
                continue
            label_index = section_index[label]
            structures.append(case.structure[row_index])
            labels.append(label_index)
            embedding = embeddings.get(
                (case.image_id, case.receipt_id, feature.row.row_id)
            )
            if embedding is not None:
                embedding_rows.append(embedding)
                embedding_labels.append(label_index)
    structure_values = np.asarray(structures, dtype=np.float32)
    label_values = np.asarray(labels, dtype=np.int32)
    structure_mean = structure_values.mean(axis=0)
    structure_scale = np.maximum(structure_values.std(axis=0), 0.05)
    normalized_structure = (structure_values - structure_mean) / structure_scale
    pooled_variance = np.maximum(normalized_structure.var(axis=0), 0.05)
    class_means = []
    class_scales = []
    for index in range(len(section_tuple)):
        selected = normalized_structure[label_values == index]
        if not len(selected):
            raise RuntimeError(f"No structure training rows for {section_tuple[index]}")
        class_means.append(selected.mean(axis=0))
        variance = (selected.var(axis=0) * len(selected) + pooled_variance * 8) / (
            len(selected) + 8
        )
        class_scales.append(np.sqrt(np.maximum(variance, 0.05)))

    embedding_centroids = None
    train_embeddings = None
    train_embedding_labels = None
    if embedding_rows:
        train_embeddings = _normalize_rows(np.asarray(embedding_rows, dtype=np.float32))
        train_embedding_labels = np.asarray(embedding_labels, dtype=np.int32)
        centroids = []
        for index in range(len(section_tuple)):
            selected = train_embeddings[train_embedding_labels == index]
            if not len(selected):
                raise RuntimeError(
                    f"No embedding training rows for {section_tuple[index]}"
                )
            centroid = selected.mean(axis=0, keepdims=True)
            centroids.append(_normalize_rows(centroid)[0])
        embedding_centroids = np.asarray(centroids, dtype=np.float32)
    return EvidenceModel(
        sections=section_tuple,
        structure_mean=structure_mean,
        structure_scale=structure_scale,
        class_structure_mean=np.asarray(class_means, dtype=np.float32),
        class_structure_scale=np.asarray(class_scales, dtype=np.float32),
        embedding_centroids=embedding_centroids,
        train_embeddings=train_embeddings,
        train_embedding_labels=train_embedding_labels,
    )


def _center_clip(scores: np.ndarray, limit: float = 8.0) -> np.ndarray:
    centered = scores - scores.mean(axis=1, keepdims=True)
    return np.clip(centered, -limit, limit).astype(np.float32)


def case_evidence(
    case: ReceiptCase,
    model: EvidenceModel,
    embeddings: Mapping[tuple[str, int, int], np.ndarray],
    knn_neighbors: int,
) -> CaseEvidence:
    """Project one receipt onto the learned structure and embedding spaces."""

    normalized = (case.structure - model.structure_mean) / model.structure_scale
    residual = (
        normalized[:, None, :] - model.class_structure_mean[None, :, :]
    ) / model.class_structure_scale[None, :, :]
    geometry = -0.5 * np.mean(
        residual * residual + 2 * np.log(model.class_structure_scale[None, :, :]),
        axis=2,
    )
    geometry = _center_clip(geometry)

    row_embeddings: list[np.ndarray] = []
    available = []
    dimension = (
        model.embedding_centroids.shape[1]
        if model.embedding_centroids is not None
        else 0
    )
    for feature in case.features:
        embedding = embeddings.get((case.image_id, case.receipt_id, feature.row.row_id))
        available.append(embedding is not None)
        row_embeddings.append(
            embedding
            if embedding is not None
            else np.zeros(dimension, dtype=np.float32)
        )
    available_array = np.asarray(available, dtype=bool)
    projection = np.zeros((len(case.features), len(model.sections)), dtype=np.float32)
    knn = np.zeros_like(projection)
    if (
        model.embedding_centroids is not None
        and model.train_embeddings is not None
        and model.train_embedding_labels is not None
        and np.any(available_array)
    ):
        query = _normalize_rows(np.asarray(row_embeddings, dtype=np.float32))
        projection = _center_clip((query @ model.embedding_centroids.T) / 0.05)
        projection[~available_array] = 0.0

        similarities = query[available_array] @ model.train_embeddings.T
        neighbors = min(knn_neighbors, model.train_embeddings.shape[0])
        indices = np.argpartition(-similarities, kth=neighbors - 1, axis=1)[
            :, :neighbors
        ]
        probabilities = np.full(
            (indices.shape[0], len(model.sections)), 0.1, dtype=np.float32
        )
        for query_index, neighbor_indices in enumerate(indices):
            for neighbor_index in neighbor_indices:
                weight = max(float(similarities[query_index, neighbor_index]), 0.0)
                probabilities[
                    query_index, model.train_embedding_labels[neighbor_index]
                ] += weight
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        knn[available_array] = _center_clip(np.log(probabilities + _EPSILON))
    return CaseEvidence(geometry, projection, knn, available_array)


def _lexical_scores(
    feature: RowFeatures, model: Mapping[str, Any], sections: Sequence[str]
) -> np.ndarray:
    result = []
    for section in sections:
        token_log_odds = model["global"]["section_models"][section].get(
            "token_log_odds", {}
        )
        result.append(
            sum(
                float(token_log_odds[token])
                for token in feature.tokens
                if token in token_log_odds
            )
        )
    return np.asarray(result, dtype=np.float32)


def _predict(
    case: ReceiptCase,
    decoder_model: Mapping[str, Any],
    evidence: CaseEvidence | None = None,
    weights: EvidenceWeights | None = None,
) -> dict[int, str]:
    if evidence is None or weights is None or not any(weights.as_dict().values()):
        features = case.features
    else:
        sections = tuple(decoder_model["global"]["sections"])
        features = tuple(
            replace(
                feature,
                token_evidence=tuple(
                    zip(
                        sections,
                        (
                            _lexical_scores(feature, decoder_model, sections)
                            + weights.geometry_math * evidence.geometry_math[index]
                            + weights.embedding_projection
                            * evidence.embedding_projection[index]
                            + weights.embedding_knn * evidence.embedding_knn[index]
                        ).tolist(),
                        strict=True,
                    )
                ),
            )
            for index, feature in enumerate(case.features)
        )
    return {
        assignment.row.row_id: assignment.section_type
        for assignment in assign_feature_sections(features, decoder_model)
    }


def score_cases(
    cases: Sequence[ReceiptCase],
    decoder_model: Mapping[str, Any],
    evidence: Mapping[tuple[str, int], CaseEvidence] | None = None,
    weights: EvidenceWeights | None = None,
) -> dict[str, Any]:
    matched = 0
    total = 0
    per_type_total: Counter[str] = Counter()
    per_type_matched: Counter[str] = Counter()
    case_scores = []
    for case in cases:
        predicted = _predict(
            case,
            decoder_model,
            evidence.get(case.key) if evidence else None,
            weights,
        )
        case_matched = 0
        for row_id, expected in case.truth.items():
            total += 1
            per_type_total[expected] += 1
            if predicted.get(row_id) == expected:
                matched += 1
                case_matched += 1
                per_type_matched[expected] += 1
        case_scores.append(case_matched / len(case.truth))
    per_type = {
        section: {
            "matched": per_type_matched[section],
            "scored": count,
            "recall": per_type_matched[section] / count,
        }
        for section, count in sorted(per_type_total.items())
    }
    return {
        "receipts": len(cases),
        "matched": matched,
        "scored": total,
        "agreement": matched / total if total else 0.0,
        "mean_receipt_agreement": fmean(case_scores) if case_scores else 0.0,
        "macro_recall": (
            fmean(item["recall"] for item in per_type.values()) if per_type else 0.0
        ),
        "per_type": per_type,
    }


def _objective(score: Mapping[str, Any]) -> tuple[float, float]:
    return float(score["agreement"]), float(score["macro_recall"])


def tune_weights(
    cases: Sequence[ReceiptCase],
    decoder_model: Mapping[str, Any],
    evidence: Mapping[tuple[str, int], CaseEvidence],
) -> tuple[EvidenceWeights, list[dict[str, Any]]]:
    """Use two deterministic coordinate-descent passes on validation only."""

    current = EvidenceWeights()
    trials: list[dict[str, Any]] = []
    fields = ("geometry_math", "embedding_projection", "embedding_knn")
    for _ in range(2):
        for field in fields:
            candidates = []
            for choice in _WEIGHT_CHOICES:
                candidate = replace(current, **{field: choice})
                score = score_cases(cases, decoder_model, evidence, candidate)
                trials.append(
                    {
                        "weights": candidate.as_dict(),
                        "agreement": score["agreement"],
                        "macro_recall": score["macro_recall"],
                    }
                )
                candidates.append((candidate, score))
            current, _ = max(
                candidates,
                key=lambda item: (
                    _objective(item[1]),
                    -sum(item[0].as_dict().values()),
                ),
            )
    return current, trials


def _metric_delta(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, float]:
    result = {
        "agreement": float(candidate["agreement"] - baseline["agreement"]),
        "macro_recall": float(candidate["macro_recall"] - baseline["macro_recall"]),
    }
    all_types = set(baseline["per_type"]) | set(candidate["per_type"])
    for section in sorted(all_types):
        before = baseline["per_type"].get(section, {}).get("recall", 0.0)
        after = candidate["per_type"].get(section, {}).get("recall", 0.0)
        result[f"recall_{section}"] = float(after - before)
    return result


def paired_comparison(
    cases: Sequence[ReceiptCase],
    decoder_model: Mapping[str, Any],
    evidence: Mapping[tuple[str, int], CaseEvidence],
    weights: EvidenceWeights,
    *,
    bootstrap_seed: int = 20260729,
    bootstrap_samples: int = 5000,
) -> dict[str, Any]:
    """Return paired row outcomes and a receipt-bootstrap interval."""

    before_only = 0
    after_only = 0
    both_correct = 0
    both_wrong = 0
    receipt_outcomes: list[tuple[int, int, int]] = []
    for case in cases:
        baseline = _predict(case, decoder_model)
        hybrid = _predict(case, decoder_model, evidence[case.key], weights)
        baseline_matched = 0
        hybrid_matched = 0
        for row_id, expected in case.truth.items():
            before = baseline.get(row_id) == expected
            after = hybrid.get(row_id) == expected
            baseline_matched += int(before)
            hybrid_matched += int(after)
            if before and after:
                both_correct += 1
            elif before:
                before_only += 1
            elif after:
                after_only += 1
            else:
                both_wrong += 1
        receipt_outcomes.append((baseline_matched, hybrid_matched, len(case.truth)))

    discordant = before_only + after_only
    tail = min(before_only, after_only)
    mcnemar_p = min(
        1.0,
        2
        * sum(
            math.comb(discordant, value) * (0.5**discordant)
            for value in range(tail + 1)
        ),
    )
    values = np.asarray(receipt_outcomes, dtype=np.float64)
    generator = np.random.default_rng(bootstrap_seed)
    deltas = np.empty(bootstrap_samples, dtype=np.float64)
    for sample in range(bootstrap_samples):
        indices = generator.integers(0, len(values), size=len(values))
        selected = values[indices]
        deltas[sample] = (selected[:, 1].sum() - selected[:, 0].sum()) / selected[
            :, 2
        ].sum()
    lower, upper = np.quantile(deltas, [0.025, 0.975])
    return {
        "both_correct": both_correct,
        "baseline_only_correct": before_only,
        "hybrid_only_correct": after_only,
        "both_wrong": both_wrong,
        "net_rows": after_only - before_only,
        "exact_mcnemar_two_sided_p": mcnemar_p,
        "receipt_bootstrap_delta_95pct": [float(lower), float(upper)],
        "bootstrap_samples": bootstrap_samples,
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    manifest = json.loads((args.cache_root / "manifest.json").read_text())
    for component in ("dynamodb", "chroma"):
        if manifest.get("components", {}).get(component, {}).get("valid") is not True:
            raise RuntimeError(f"Local analytics {component} component is not valid")
    cases = load_cases(args.cache_root / "dynamodb.sqlite3", args.max_receipts)
    train, validation, test = split_cases(cases, args.seed)
    if not train or not validation or not test:
        raise RuntimeError("Receipt split produced an empty partition")
    line_version = manifest["components"]["chroma"]["collections"]["lines"][
        "version_id"
    ]
    chroma_path, repair = prepare_chroma_lines(
        args.cache_root / "chroma" / "lines",
        args.working_root / "chroma" / line_version / "lines",
    )
    embeddings, embedding_coverage = load_embeddings(chroma_path, cases)

    tuning_decoder = _decoder_model(train)
    tuning_sections = tuning_decoder["global"]["sections"]
    tuning_evidence_model = fit_evidence_model(train, embeddings, tuning_sections)
    validation_evidence = {
        case.key: case_evidence(
            case, tuning_evidence_model, embeddings, args.knn_neighbors
        )
        for case in validation
    }
    validation_baseline = score_cases(validation, tuning_decoder)
    weights, tuning_trials = tune_weights(
        validation, tuning_decoder, validation_evidence
    )
    validation_hybrid = score_cases(
        validation, tuning_decoder, validation_evidence, weights
    )

    training = train + validation
    final_decoder = _decoder_model(training)
    final_sections = final_decoder["global"]["sections"]
    final_evidence_model = fit_evidence_model(training, embeddings, final_sections)
    test_evidence = {
        case.key: case_evidence(
            case, final_evidence_model, embeddings, args.knn_neighbors
        )
        for case in test
    }
    baseline = score_cases(test, final_decoder)
    hybrid = score_cases(test, final_decoder, test_evidence, weights)
    paired = paired_comparison(test, final_decoder, test_evidence, weights)
    ablations = {}
    for field in weights.as_dict():
        component_weight = EvidenceWeights(**{field: getattr(weights, field)})
        ablations[field] = score_cases(
            test, final_decoder, test_evidence, component_weight
        )

    available = sum(
        int(item.embedding_available.sum()) for item in test_evidence.values()
    )
    test_rows = sum(len(case.features) for case in test)
    return {
        "experiment": "section-geometry-embedding-v1",
        "cache": {
            "dynamodb_synced_at": manifest["components"]["dynamodb"]["synced_at"],
            "dynamodb_rows": manifest["components"]["dynamodb"]["row_count"],
            "chroma_versions": {
                name: value["version_id"]
                for name, value in manifest["components"]["chroma"][
                    "collections"
                ].items()
            },
            "chroma_read_repair": repair,
        },
        "corpus": {
            "receipts": len(cases),
            "split": {
                "train": len(train),
                "validation": len(validation),
                "test": len(test),
            },
            "section_types": final_sections,
            "embedding_coverage": {
                **embedding_coverage,
                "test_rows": test_rows,
                "test_found": available,
                "test_fraction": available / test_rows if test_rows else 0.0,
            },
        },
        "selection": {
            "weights": weights.as_dict(),
            "validation_baseline": validation_baseline,
            "validation_hybrid": validation_hybrid,
            "trials": tuning_trials,
        },
        "test": {
            "baseline": baseline,
            "hybrid": hybrid,
            "delta": _metric_delta(baseline, hybrid),
            "paired": paired,
            "ablations": ablations,
        },
    }


def main() -> int:
    args = _arguments()
    report = evaluate(args)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
