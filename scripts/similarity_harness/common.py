"""Fixture schema helpers shared by capture and evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from receipt_embeddings import ScoredItem, VectorItem

SCHEMA_VERSION = 1
MIN_RECEIPTS = 40
MERCHANT_FAMILY = "merchant_resolution"
WORD_FAMILY = "word_neighbors"
SECTION_FAMILY = "section_verifier"
QUERY_FAMILIES = (MERCHANT_FAMILY, WORD_FAMILY, SECTION_FAMILY)
LINE_INDEX = "lines-vectors"
WORD_INDEX = "words-vectors"
DEFAULT_RECALL_K = 10
DISTANCE_DECIMALS = 8
VECTOR_DECIMALS = 8

_METADATA_KEYS = frozenset(
    {
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
        "row_line_ids",
        "merchant_name",
        "place_id",
        "section_type",
        "label_status",
        "primary_label",
        "valid_labels",
    }
)


class FixtureError(ValueError):
    """A similarity fixture does not satisfy the Round A contract."""


def receipt_key(image_id: str, receipt_id: int) -> str:
    """Return the stable receipt identity used throughout the harness."""

    return f"{image_id}#{receipt_id:05d}"


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a value in the fixture's byte-stable JSON representation."""

    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def content_digest(fixture: Mapping[str, Any]) -> str:
    """Hash fixture content while excluding the hash field itself."""

    payload = dict(fixture)
    source = dict(payload.get("source", {}))
    source.pop("content_sha256", None)
    payload["source"] = source
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def write_fixture(path: Path, fixture: dict[str, Any]) -> None:
    """Write normalized JSON atomically and include a self-checking digest."""

    normalized = dict(fixture)
    source = dict(normalized.get("source", {}))
    source.pop("content_sha256", None)
    normalized["source"] = source
    source["content_sha256"] = content_digest(normalized)
    data = canonical_json_bytes(normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def load_fixture(
    path: Path, *, minimum_receipts: int = MIN_RECEIPTS
) -> dict[str, Any]:
    """Load and fully validate one similarity fixture."""

    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"could not read fixture {path}: {exc}") from exc
    validate_fixture(fixture, minimum_receipts=minimum_receipts)
    expected_digest = fixture.get("source", {}).get("content_sha256")
    if expected_digest and expected_digest != content_digest(fixture):
        raise FixtureError("fixture content_sha256 does not match its content")
    return cast(dict[str, Any], fixture)


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FixtureError(f"{name} must be an object")
    return value


def _require_sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise FixtureError(f"{name} must be an array")
    return value


def validate_fixture(fixture: object, *, minimum_receipts: int) -> None:
    """Enforce coverage and shape invariants before any score is computed."""

    root = _require_mapping(fixture, "fixture")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise FixtureError(
            f"schema_version must be {SCHEMA_VERSION}, "
            f"got {root.get('schema_version')!r}"
        )
    receipts = _require_sequence(root.get("receipts"), "receipts")
    if len(receipts) < minimum_receipts:
        raise FixtureError(
            f"fixture has {len(receipts)} receipts; "
            f"Round A requires at least {minimum_receipts}"
        )
    receipt_ids: set[str] = set()
    for position, receipt in enumerate(receipts):
        item = _require_mapping(receipt, f"receipts[{position}]")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise FixtureError(f"receipts[{position}].key must be a string")
        if key in receipt_ids:
            raise FixtureError(f"duplicate receipt key: {key}")
        receipt_ids.add(key)

    corpus = _require_sequence(root.get("corpus"), "corpus")
    corpus_keys: set[str] = set()
    dimension: int | None = None
    for position, raw_item in enumerate(corpus):
        item = _require_mapping(raw_item, f"corpus[{position}]")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise FixtureError(f"corpus[{position}].key must be a string")
        if key in corpus_keys:
            raise FixtureError(f"duplicate corpus key: {key}")
        corpus_keys.add(key)
        vector = _require_sequence(
            item.get("vector"), f"corpus[{position}].vector"
        )
        if not vector or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in vector
        ):
            raise FixtureError(
                f"corpus[{position}].vector must contain finite numbers"
            )
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise FixtureError("all corpus vectors must have one dimension")

    queries = _require_sequence(root.get("queries"), "queries")
    query_ids: set[str] = set()
    coverage: dict[str, set[str]] = {key: set() for key in receipt_ids}
    for position, raw_query in enumerate(queries):
        name = f"queries[{position}]"
        query = _require_mapping(raw_query, name)
        query_id = query.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise FixtureError(f"{name}.query_id must be a string")
        if query_id in query_ids:
            raise FixtureError(f"duplicate query_id: {query_id}")
        query_ids.add(query_id)
        family = query.get("family")
        if family not in QUERY_FAMILIES:
            raise FixtureError(f"{name}.family is invalid: {family!r}")
        query_receipt = query.get("receipt_key")
        if query_receipt not in receipt_ids:
            raise FixtureError(
                f"{name}.receipt_key is not declared: {query_receipt!r}"
            )
        coverage[query_receipt].add(str(family))
        vector = _require_sequence(query.get("vector"), f"{name}.vector")
        if dimension is not None and len(vector) != dimension:
            raise FixtureError(
                f"{name}.vector has dimension {len(vector)}; "
                f"expected {dimension}"
            )
        if not vector or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in vector
        ):
            raise FixtureError(f"{name}.vector must contain finite numbers")
        top_k = query.get("top_k")
        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or not 1 <= top_k <= 100
        ):
            raise FixtureError(f"{name}.top_k must be in [1, 100]")
        if family == WORD_FAMILY and top_k != 30:
            raise FixtureError(f"{name} must capture word top-30 neighbors")
        expected_index = WORD_INDEX if family == WORD_FAMILY else LINE_INDEX
        if query.get("index") != expected_index:
            raise FixtureError(
                f"{name}.index must be {expected_index!r} for {family}"
            )
        _require_mapping(query.get("filters"), f"{name}.filters")
        expected = _require_mapping(query.get("expected"), f"{name}.expected")
        neighbors = _require_sequence(
            expected.get("neighbors"), f"{name}.expected.neighbors"
        )
        if family == WORD_FAMILY and len(neighbors) != 30:
            raise FixtureError(
                f"{name} must contain exactly 30 word neighbors"
            )
        if len(neighbors) != top_k:
            raise FixtureError(
                f"{name} contains {len(neighbors)} neighbors; expected {top_k}"
            )
        neighbor_keys: set[str] = set()
        previous_distance = -math.inf
        for rank, raw_neighbor in enumerate(neighbors):
            neighbor = _require_mapping(
                raw_neighbor, f"{name}.expected.neighbors[{rank}]"
            )
            neighbor_key = neighbor.get("key")
            if not isinstance(neighbor_key, str):
                raise FixtureError(f"{name} neighbor {rank} has no key")
            if neighbor_key in neighbor_keys:
                raise FixtureError(f"{name} repeats neighbor {neighbor_key}")
            neighbor_keys.add(neighbor_key)
            if neighbor_key not in corpus_keys:
                raise FixtureError(
                    f"{name} neighbor {neighbor_key} is absent from corpus"
                )
            distance = neighbor.get("distance")
            if (
                not isinstance(distance, (int, float))
                or isinstance(distance, bool)
                or not math.isfinite(distance)
            ):
                raise FixtureError(
                    f"{name} neighbor {rank} has invalid distance"
                )
            if float(distance) < previous_distance:
                raise FixtureError(f"{name} neighbors are not distance-ranked")
            previous_distance = float(distance)
        if family == MERCHANT_FAMILY:
            merchant = _require_mapping(
                expected.get("merchant"), f"{name}.expected.merchant"
            )
            if merchant.get("decision") not in {"matched", "not_found"}:
                raise FixtureError(f"{name} has invalid merchant decision")
            if not isinstance(merchant.get("tier"), str):
                raise FixtureError(f"{name} has no merchant tier")
        elif family == SECTION_FAMILY:
            section = _require_mapping(
                expected.get("section"), f"{name}.expected.section"
            )
            if section.get("vote") not in {
                "agree",
                "disagree",
                "abstain",
            }:
                raise FixtureError(f"{name} has invalid section vote")

    missing = {
        key: sorted(set(QUERY_FAMILIES) - families)
        for key, families in coverage.items()
        if set(QUERY_FAMILIES) - families
    }
    if missing:
        first_key = sorted(missing)[0]
        raise FixtureError(
            f"receipt {first_key} lacks query families: {missing[first_key]}"
        )


def sanitize_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    """Keep only non-sensitive fields required to replay consumer decisions."""

    result: dict[str, object] = {}
    for key in sorted(_METADATA_KEYS):
        if key not in metadata or metadata[key] is None:
            continue
        value = metadata[key]
        if key == "section_type" and value == "":
            continue
        result[key] = json.loads(json.dumps(value, allow_nan=False))
    return result


def scored_item_dict(item: ScoredItem) -> dict[str, object]:
    """Convert a result to canonical fixture form."""

    return {
        "distance": round(float(item.distance), DISTANCE_DECIMALS),
        "key": item.key,
    }


def scored_item_from_dict(
    value: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> ScoredItem:
    """Rehydrate one captured neighbor."""

    return ScoredItem(
        key=str(value["key"]),
        distance=float(value["distance"]),
        metadata=dict(metadata or value.get("metadata", {})),
    )


def corpus_items(fixture: Mapping[str, Any]) -> list[VectorItem]:
    """Build typed items for ``FakeVectorIndex`` from a fixture corpus."""

    return [
        VectorItem(
            key=str(item["key"]),
            index=str(item["index"]),
            vector=[float(value) for value in item["vector"]],
            metadata=dict(item.get("metadata", {})),
        )
        for item in fixture["corpus"]
    ]


def round_vector(vector: Sequence[float]) -> list[float]:
    """Round a vector enough to absorb service serialization noise."""

    return [round(float(value), VECTOR_DECIMALS) for value in vector]


def derive_merchant(
    neighbors: Sequence[ScoredItem],
    *,
    image_id: str,
    receipt_id: int,
    tier: str,
    max_distance: float,
) -> dict[str, object]:
    """Apply the fixture's retrieval-only merchant decision rule."""

    for item in neighbors:
        metadata = item.metadata
        metadata_receipt_id = metadata.get("receipt_id")
        if (
            metadata.get("image_id") == image_id
            and isinstance(metadata_receipt_id, (str, int))
            and int(metadata_receipt_id) == receipt_id
        ):
            continue
        merchant_name = metadata.get("merchant_name")
        if not merchant_name or item.distance > max_distance:
            continue
        return {
            "decision": "matched",
            "merchant_name": str(merchant_name),
            "place_id": (
                str(metadata["place_id"]) if metadata.get("place_id") else None
            ),
            "tier": tier,
        }
    return {
        "decision": "not_found",
        "merchant_name": None,
        "place_id": None,
        "tier": "not_found",
    }


def derive_section_vote(
    neighbors: Sequence[ScoredItem],
    *,
    image_id: str,
    receipt_id: int,
    proposed_section_type: str | None,
) -> dict[str, object]:
    """Apply the runtime's non-negative cosine-weighted KNN vote.

    With normalized vectors, cosine similarity is ``1 - cosine_distance``.
    This is the scalar form of ``receipt_chroma.propagate_knn`` and therefore
    does not require search results to expose their stored vectors.
    """

    votes: dict[str, float] = {}
    total_weight = 0.0
    for item in neighbors:
        metadata = item.metadata
        metadata_receipt_id = metadata.get("receipt_id")
        if (
            metadata.get("image_id") == image_id
            and isinstance(metadata_receipt_id, (str, int))
            and int(metadata_receipt_id) == receipt_id
        ):
            continue
        section_type = metadata.get("section_type")
        if section_type:
            label = str(section_type)
            weight = max(1.0 - float(item.distance), 0.0)
            votes[label] = votes.get(label, 0.0) + weight
            total_weight += weight
    if not votes or total_weight <= 0.0:
        predicted = None
        vote = "abstain"
    else:
        predicted = max(votes.items(), key=lambda item: (item[1], item[0]))[0]
        vote = (
            "agree"
            if proposed_section_type and predicted == proposed_section_type
            else "disagree"
        )
    return {
        "predicted_section_type": predicted,
        "proposed_section_type": proposed_section_type,
        "vote": vote,
    }


__all__ = [
    "DEFAULT_RECALL_K",
    "DISTANCE_DECIMALS",
    "FixtureError",
    "LINE_INDEX",
    "MERCHANT_FAMILY",
    "MIN_RECEIPTS",
    "QUERY_FAMILIES",
    "SCHEMA_VERSION",
    "SECTION_FAMILY",
    "VECTOR_DECIMALS",
    "WORD_FAMILY",
    "WORD_INDEX",
    "canonical_json_bytes",
    "content_digest",
    "corpus_items",
    "derive_merchant",
    "derive_section_vote",
    "load_fixture",
    "receipt_key",
    "round_vector",
    "sanitize_metadata",
    "scored_item_dict",
    "scored_item_from_dict",
    "validate_fixture",
    "write_fixture",
]
